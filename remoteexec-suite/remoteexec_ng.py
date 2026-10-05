"""
remoteexec_ng.py - pwnagotchi plugin (runs ON the pi)

RemoteExecNG is a hardened, authenticated command API for a pwnagotchi YOU OWN.
It exposes a small HTTP endpoint that runs a command and returns its result as
JSON ({ok, stdout, stderr, exit_code, duration, truncated}). Two uses:

  * remote admin of your own devices (what SSH already gives you, as a clean
    token-gated API), and
  * the AGENT side of the fleet-coordination track (B2): a controller you run
    calls this endpoint on each enrolled agent to fan tasks out.

This is the B1 slice of the BADUSB_AND_REMOTE_EXEC_IDEAS backlog.

THE SAFETY LINE (non-negotiable - infrastructure for gear you own, not an
implant):

  * This runs on a device YOU control and YOU install it on. It does not
    exploit, self-propagate, persist beyond a normal plugin, or compromise
    anything - it runs commands you authorize, for yourself.
  * DEFAULT MODE is "tasks": the API can ONLY run NAMED commands you defined in
    config (label -> exact command). A caller asks for a task by name; it can't
    inject arbitrary shell. Empty task list = the API runs nothing.
  * "free" mode (arbitrary commands) is DOUBLE-GATED: it requires BOTH
    command_mode = "free" AND allow_free_mode = true, so it can never be on by
    accident. Even then it's token-gated and logged.
  * Token auth (>=12 chars, no placeholders) on every request, and bind_scope
    never binds the open LAN by default - same discipline as web2ssh_ng/badhid.
  * Every command runs through a timeout and an output cap, and every run is
    logged at WARNING (task/command + exit code), so nothing is silent.

Framework facts (verified against jayofelony/pwnagotchi
pwnagotchi/plugins/__init__.py, as with the other suites here):
  - config section = file basename -> [main.plugins.remoteexec_ng].
  - loader sets plugin.options verbatim (no __defaults__ merge) -> read every
    option via _opt()/_opt_int()/_opt_bool() against the module DEFAULTS dict.
  - cls() zero-arg construction; real hooks only: on_loaded(self),
    on_unload(self, ui), on_webhook(self, path, request), on_ui_setup(self, ui),
    on_ui_update(self, ui). Flask served via make_server in a daemon thread so
    on_loaded never blocks.
"""

import hmac
import json
import logging
import os
import re
import shlex
import subprocess
import threading
import time

import pwnagotchi.plugins as plugins

try:
    from flask import Response, jsonify, render_template_string, request  # noqa: F401
    from werkzeug.serving import make_server
except Exception:  # pragma: no cover
    Response = None
    jsonify = None
    render_template_string = None
    make_server = None

try:
    import pwnagotchi.ui.fonts as fonts
    from pwnagotchi.ui.components import LabeledValue
    from pwnagotchi.ui.view import BLACK
    _UI_AVAILABLE = True
except Exception:  # pragma: no cover
    fonts = None
    LabeledValue = None
    BLACK = 0
    _UI_AVAILABLE = False

ELEMENT_NAME = "remoteexec"


# NOTE: auth_token defaults to None on purpose - there is NO working default.
DEFAULTS = {
    "enabled": False,

    # >>> USER INPUT REQUIRED <<< - no working default; a missing/blank/
    # placeholder/short token means the server refuses to start.
    "auth_token": None,

    # "auto" (tailscale IP if present else localhost), "tailscale", "localhost",
    # or "lan" (0.0.0.0 - explicit, logged opt-in). The URL is always logged.
    "bind_scope": "auto",
    "port": 8084,

    # "tasks" (default, SAFE): only run NAMED commands from the `tasks` table.
    # "free": run arbitrary commands - requires allow_free_mode=true as well.
    "command_mode": "tasks",

    # label -> exact command. In tasks mode ONLY these run. Pre-populated with
    # safe, read-only examples; edit to taste. Empty = the API runs nothing.
    "tasks": {
        "uptime": "uptime",
        "status": "systemctl is-active pwnagotchi",
        "disk": "df -h /",
        "temp": "vcgencmd measure_temp",
        "ip": "hostname -I",
    },

    # Second gate for arbitrary-command execution. Both command_mode="free" AND
    # this must be true, or free commands are refused.
    "allow_free_mode": False,

    # A hung command can't hang a request past this many seconds.
    "command_timeout_seconds": 30,
    # Output longer than this (stdout+stderr each) is truncated with a note.
    "max_output_chars": 20000,

    # Optional append-only audit log of every command run (path or "").
    "audit_log": "",

    # On-screen status (optional; monochrome-safe for the TFT).
    "ui_enabled": True,
    "ui_position_x": -40,
    "ui_position_y": 20,
}

PLACEHOLDER_TOKENS = frozenset({
    "", "changeme", "change_me", "change-me", "token", "default", "secret",
    "password", "admin", "root", "pwnagotchi", "remoteexec", "test", "12345",
    "123456", "0000", "none", "null",
})

IPV4_RE = re.compile(r"\b(\d{1,3}(?:\.\d{1,3}){3})\b")


# ===========================================================================
# Pure / testable helpers
# ===========================================================================

def _looks_like_placeholder(value):
    if value is None:
        return True
    return str(value).strip().lower() in PLACEHOLDER_TOKENS


def validate_token(token):
    if _looks_like_placeholder(token):
        return False, ("auth_token is missing/blank/placeholder - the "
                       "RemoteExec server will NOT start. Set a long random token.")
    if len(str(token).strip()) < 12:
        return False, "auth_token is shorter than 12 characters - refusing to start."
    return True, "ok"


def token_matches(configured, presented):
    if configured is None or presented is None:
        return False
    return hmac.compare_digest(str(configured), str(presented))


def _detect_tailscale_ip():  # pragma: no cover
    for cmd in (["tailscale", "ip", "-4"],
                ["ip", "-4", "addr", "show", "tailscale0"]):
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=5).stdout
            m = IPV4_RE.search(out or "")
            if m:
                return m.group(1)
        except Exception:
            pass
    return None


def resolve_bind_plan(bind_scope, port, detect_tailscale=None):
    """(bind_host, note, ok) - same semantics as web2ssh_ng/badhid_ng."""
    if detect_tailscale is None:
        detect_tailscale = _detect_tailscale_ip
    scope = (bind_scope or "auto").strip().lower()
    if scope == "localhost":
        return "127.0.0.1", f"localhost only - http://127.0.0.1:{port}/", True
    if scope == "lan":
        return "0.0.0.0", f"LAN/all interfaces (0.0.0.0:{port}) - EXPLICIT broad exposure", True
    if scope == "tailscale":
        ip = detect_tailscale()
        if ip:
            return ip, f"tailscale only - http://{ip}:{port}/", True
        return None, ("bind_scope='tailscale' but no tailscale interface found - "
                      "refusing to start (fail-safe)."), False
    if scope == "auto":
        ip = detect_tailscale()
        if ip:
            return ip, f"auto -> tailscale - http://{ip}:{port}/", True
        return "127.0.0.1", (f"auto -> no tailscale, localhost only - "
                             f"http://127.0.0.1:{port}/ (use an SSH tunnel)"), True
    return "127.0.0.1", f"unknown bind_scope {scope!r}, using localhost", True


def run_command(command, timeout_seconds=30, max_output_chars=20000):
    """Run a command and return a result dict. Pure-ish (does spawn a process):
    testable with safe commands like 'echo'. Never raises - failures become a
    result with ok=False and a message in stderr."""
    start = time.time()
    try:
        proc = subprocess.run(
            command, shell=True, capture_output=True, text=True,
            timeout=max(1, int(timeout_seconds)),
        )
        out = proc.stdout or ""
        err = proc.stderr or ""
        truncated = False
        if len(out) > max_output_chars:
            out = out[:max_output_chars]; truncated = True
        if len(err) > max_output_chars:
            err = err[:max_output_chars]; truncated = True
        return {
            "ok": proc.returncode == 0,
            "exit_code": proc.returncode,
            "stdout": out,
            "stderr": err,
            "truncated": truncated,
            "duration": round(time.time() - start, 3),
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "exit_code": None, "stdout": "", "stderr":
                f"timed out after {timeout_seconds}s", "truncated": False,
                "duration": round(time.time() - start, 3)}
    except Exception as exc:
        return {"ok": False, "exit_code": None, "stdout": "", "stderr":
                f"could not run: {exc}", "truncated": False,
                "duration": round(time.time() - start, 3)}


def resolve_request(command_mode, allow_free_mode, tasks, body):
    """Decide WHAT to run from a request body, enforcing the mode gates.
    Returns (command, label, error). Pure - no execution. This is the security
    choke point, isolated so it's unit-tested directly."""
    mode = (command_mode or "tasks").strip().lower()
    task = (body or {}).get("task")
    command = (body or {}).get("command")

    if mode == "free":
        if not allow_free_mode:
            return None, None, ("free command mode requires BOTH "
                                "command_mode='free' AND allow_free_mode=true")
        if task is not None:
            # even in free mode, a named task runs the configured command
            if task in (tasks or {}):
                return tasks[task], f"task:{task}", None
            return None, None, f"unknown task {task!r}"
        if not command or not str(command).strip():
            return None, None, "free mode: provide a non-empty 'command'"
        return str(command), "free", None

    # tasks mode (default, safe): ONLY named tasks
    if command is not None:
        return None, None, ("this agent is in tasks mode - arbitrary 'command' "
                            "is refused; use {'task': '<name>'}")
    if task is None:
        return None, None, "provide {'task': '<name>'} (see /tasks for the list)"
    if task not in (tasks or {}):
        return None, None, f"unknown task {task!r} (see /tasks)"
    return tasks[task], f"task:{task}", None


# ===========================================================================
# The plugin
# ===========================================================================

class RemoteExecNG(plugins.Plugin):
    __author__ = "built for this project's remote-exec track (B1)"
    __version__ = "0.1.0"
    __license__ = "GPL3"
    __description__ = ("Hardened authenticated command API for a pwnagotchi you "
                       "own: named-tasks by default, free mode double-gated, "
                       "token auth + bind_scope, timeout + output cap, loud log.")

    def __init__(self):
        self._server = None
        self._server_thread = None
        self._bind_note = ""
        self._last = None  # (label, exit_code, ts)
        self._count = 0

    # --- option readers ---
    def _opt(self, key):
        try:
            return self.options.get(key, DEFAULTS.get(key))
        except Exception:
            return DEFAULTS.get(key)

    def _opt_int(self, key, default=0):
        try:
            return int(self._opt(key))
        except (TypeError, ValueError):
            return default

    def _opt_bool(self, key, default=False):
        val = self._opt(key)
        if isinstance(val, bool):
            return val
        if val is None:
            return default
        return str(val).strip().lower() in ("1", "true", "yes", "on")

    # --- lifecycle ---
    def on_loaded(self):
        logging.info("[remoteexec_ng] loading")
        mode = (self._opt("command_mode") or "tasks").strip().lower()
        if mode == "free" and self._opt_bool("allow_free_mode", False):
            logging.warning("[remoteexec_ng] FREE command mode is ENABLED - this "
                            "agent will run arbitrary commands it's given (token-"
                            "gated). Make sure bind_scope is tight.")
        else:
            tasks = self._opt("tasks") or {}
            logging.info("[remoteexec_ng] tasks mode - %d named task(s): %s",
                         len(tasks), ", ".join(sorted(tasks)) or "(none)")
        self._start_server()

    def on_unload(self, ui):
        if self._server is not None:
            try:
                self._server.shutdown()
            except Exception:
                pass
            self._server = None
        if _UI_AVAILABLE and ui is not None:
            try:
                with ui._lock:
                    ui.remove_element(ELEMENT_NAME)
            except Exception:
                pass
        logging.info("[remoteexec_ng] unloaded")

    # --- execution ---
    def _execute(self, body, source):
        command, label, err = resolve_request(
            self._opt("command_mode"), self._opt_bool("allow_free_mode", False),
            self._opt("tasks") or {}, body)
        if err:
            logging.warning("[remoteexec_ng] refused (%s): %s", source, err)
            return {"ok": False, "error": err}, 400
        logging.warning("[remoteexec_ng] RUN %s via %s: %s", label, source, command)
        result = run_command(
            command,
            timeout_seconds=self._opt_int("command_timeout_seconds", 30),
            max_output_chars=self._opt_int("max_output_chars", 20000))
        result["label"] = label
        self._count += 1
        self._last = (label, result.get("exit_code"), time.time())
        logging.warning("[remoteexec_ng] DONE %s exit=%s (%.3fs)",
                        label, result.get("exit_code"), result.get("duration", 0))
        self._audit(label, command, result)
        return result, (200 if result.get("ok") else 200)  # 200 even on nonzero exit; ok flag carries it

    def _audit(self, label, command, result):
        path = self._opt("audit_log")
        if not path:
            return
        try:
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps({
                    "ts": time.time(), "label": label, "command": command,
                    "exit_code": result.get("exit_code"),
                    "duration": result.get("duration"),
                }) + "\n")
        except Exception as exc:
            logging.warning("[remoteexec_ng] audit_log write failed: %s", exc)

    # --- server ---
    def _start_server(self):
        ok, msg = validate_token(self._opt("auth_token"))
        if not ok:
            logging.error("[remoteexec_ng] %s", msg)
            return False
        if make_server is None:
            logging.error("[remoteexec_ng] flask/werkzeug unavailable")
            return False
        port = self._opt_int("port", 8084)
        bind_host, note, bok = resolve_bind_plan(self._opt("bind_scope"), port)
        self._bind_note = note
        if not bok:
            logging.error("[remoteexec_ng] %s", note)
            return False

        from flask import Flask
        app = Flask(__name__)
        app.add_url_rule("/", "root", self._http_root, methods=["GET"])
        app.add_url_rule("/tasks", "tasks", self._http_tasks, methods=["GET"])
        app.add_url_rule("/run", "run", self._http_run, methods=["POST"])
        try:
            self._server = make_server(bind_host, port, app, threaded=True)
        except Exception as exc:
            logging.error("[remoteexec_ng] could not bind %s:%s - %s", bind_host, port, exc)
            return False
        self._server_thread = threading.Thread(
            target=self._server.serve_forever, daemon=True, name="remoteexec_ng-http")
        self._server_thread.start()
        logging.warning("[remoteexec_ng] command API up: %s", note)
        return True

    def on_webhook(self, path, request):  # pragma: no cover
        if not self._authed(request):
            return Response("unauthorized", status=401)
        p = (path or "").strip("/")
        if p in ("", "index"):
            return self._http_root()
        if p == "tasks":
            return self._http_tasks()
        if p == "run" and request.method == "POST":
            return self._http_run()
        return Response("not found", status=404)

    # --- auth ---
    def _authed(self, req):
        configured = self._opt("auth_token")
        presented = None
        try:
            auth = req.headers.get("Authorization", "")
            if auth.startswith("Bearer "):
                presented = auth[len("Bearer "):].strip()
            if presented is None:
                presented = req.headers.get("X-Auth-Token")
            if presented is None:
                presented = req.values.get("token")
        except Exception:
            presented = None
        return token_matches(configured, presented)

    def _body(self):  # pragma: no cover - needs flask req
        try:
            if request.is_json:
                return request.get_json(silent=True) or {}
            return {k: request.values.get(k) for k in ("task", "command", "token")}
        except Exception:
            return {}

    # --- handlers ---
    def _http_root(self):  # pragma: no cover
        if not self._authed(request):
            return Response("unauthorized", status=401)
        mode = (self._opt("command_mode") or "tasks").strip().lower()
        free_on = mode == "free" and self._opt_bool("allow_free_mode", False)
        return render_template_string(
            _PAGE, bind=self._bind_note, mode=mode, free_on=free_on,
            tasks=sorted((self._opt("tasks") or {})), count=self._count,
            last=self._last)

    def _http_tasks(self):  # pragma: no cover
        if not self._authed(request):
            return Response("unauthorized", status=401)
        return jsonify({"mode": (self._opt("command_mode") or "tasks"),
                        "tasks": sorted((self._opt("tasks") or {}))})

    def _http_run(self):  # pragma: no cover
        if not self._authed(request):
            return Response("unauthorized", status=401)
        result, status = self._execute(self._body(), "web API")
        return jsonify(result), status

    # --- on-screen status ---
    def on_ui_setup(self, ui):
        if not (_UI_AVAILABLE and self._opt_bool("ui_enabled", True)):
            return
        try:
            cx = self._opt_int("ui_position_x", -40)
            pos_x = ui.width() + cx if cx < 0 else cx
            pos_x = max(0, min(pos_x, max(0, ui.width() - 10)))
            pos_y = self._opt_int("ui_position_y", 20)
            ui.add_element(ELEMENT_NAME, LabeledValue(
                color=BLACK, label="rexec", value="0",
                position=(pos_x, pos_y),
                label_font=fonts.Bold, text_font=fonts.Medium))
        except Exception as exc:
            logging.error("[remoteexec_ng] UI setup failed: %r", exc)

    def on_ui_update(self, ui):
        if not (_UI_AVAILABLE and self._opt_bool("ui_enabled", True)):
            return
        try:
            ui.set(ELEMENT_NAME, str(self._count))
        except Exception:
            pass


_PAGE = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>RemoteExec</title>
<style>body{font-family:system-ui,sans-serif;max-width:34em;margin:0 auto;padding:16px;line-height:1.4}
code{background:#8882;padding:.1em .3em;border-radius:4px}</style></head><body>
<h2>RemoteExec NG</h2>
<p><b>Mode:</b> {{ mode }}{% if free_on %} (free commands ENABLED){% endif %} &middot; <b>bound:</b> {{ bind }}</p>
<p><b>Runs so far:</b> {{ count }}{% if last %} &middot; last: {{ last[0] }} (exit {{ last[1] }}){% endif %}</p>
<h3>Named tasks</h3>
<ul>{% for t in tasks %}<li><code>{{ t }}</code></li>{% endfor %}{% if not tasks %}<li>(none configured)</li>{% endif %}</ul>
<h3>Run one</h3>
<p>POST JSON to <code>/run</code> with your token. Example:</p>
<pre>curl -s -H "Authorization: Bearer YOUR_TOKEN" \\
  -H "Content-Type: application/json" \\
  -d '{"task":"uptime"}' http://THIS_PI:PORT/run</pre>
<p style="color:#777">For devices you own or are authorized to test. Every run is logged.</p>
</body></html>"""
