"""
test_setup_helpers.py - sandbox tests for the ease-of-use helpers:
  * badhid_setopt.py  - section-scoped TOML option setter (the thing the wizard
      uses so a newcomer never hand-edits config.toml)
  * badhid_phone.sh   - phone URL + QR (URL building, reachability warning)
  * badhid_setup.sh   - guided wizard (board STOP, safe non-interactive default)

These never touch hardware. Shell tests drive the scripts with env overrides
(UDC_DIR/HIDG_DEV/CONFIG/PLUGINS_DIR/BADHID_HOME) so a pi is simulated.

Run from this folder:  python3 tests/test_setup_helpers.py
"""

import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SUITE = os.path.join(HERE, "..")
sys.path.insert(0, SUITE)

import badhid_setopt as setopt  # noqa: E402

failures = []


def check(name, cond):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")
    if not cond:
        failures.append(name)


TWO_PLUGINS = """\
[main.plugins.grid]
enabled = true
port = 9999

# a comment mentioning [main.plugins.badhid_ng] must NOT be treated as the block
[main.plugins.badhid_ng]
enabled = false
port = 8083
bind_scope = "localhost"
auth_token = "SECRETTOK123"

[main.plugins.webcfg]
enabled = true
port = 8080
"""


# --- badhid_setopt.set_in_block -------------------------------------------
def test_setopt_update_in_place():
    new, err = setopt.set_in_block(TWO_PLUGINS, "enabled", "true")
    check("setopt: no error on update", err is None)
    # exactly one 'enabled' line inside the badhid block, now true
    blk = _badhid_block(new)
    check("setopt: enabled set true", "enabled = true" in blk)
    check("setopt: no duplicate enabled", blk.count("enabled =") == 1)


def test_setopt_isolation():
    # changing badhid's port must not touch grid's or webcfg's port
    new, err = setopt.set_in_block(TWO_PLUGINS, "port", "7777")
    check("setopt: badhid port changed", "port = 7777" in _badhid_block(new))
    check("setopt: grid port untouched", "port = 9999" in new)
    check("setopt: webcfg port untouched", "port = 8080" in new)
    check("setopt: only one 7777", new.count("7777") == 1)


def test_setopt_append_when_missing():
    new, err = setopt.set_in_block(TWO_PLUGINS, "quickfire_payload", '"skull.duck"')
    blk = _badhid_block(new)
    check("setopt: appended missing key", 'quickfire_payload = "skull.duck"' in blk)
    # must land inside badhid block, before the next section
    check("setopt: append stayed in block", "quickfire_payload" not in new.split("[main.plugins.webcfg]")[1])


def test_setopt_comment_mention_not_matched():
    # the comment line mentioning the section name must not become the block
    new, err = setopt.set_in_block(TWO_PLUGINS, "bind_scope", '"lan"')
    check("setopt: bind_scope set", 'bind_scope = "lan"' in _badhid_block(new))
    check("setopt: comment line intact", "must NOT be treated as the block" in new)


def test_setopt_missing_block():
    new, err = setopt.set_in_block("[main.plugins.other]\nenabled=true\n", "enabled", "true")
    check("setopt: errors when block absent", new is None and err is not None)


def _badhid_block(text):
    out, grab = [], False
    for line in text.splitlines():
        s = line.strip()
        if s == "[main.plugins.badhid_ng]":
            grab = True
            continue
        if grab and s.startswith("[") and s.endswith("]"):
            break
        if grab:
            out.append(line)
    return "\n".join(out)


# --- shell helpers ---------------------------------------------------------
def _run(script, args, env):
    e = dict(os.environ)
    e.update(env)
    return subprocess.run(
        ["bash", os.path.join(SUITE, script), *args],
        env=e, stdin=subprocess.DEVNULL,
        capture_output=True, text=True,
    )


def _write(path, content):
    with open(path, "w") as fh:
        fh.write(content)


BADHID_LOCALHOST = """\
[main.plugins.badhid_ng]
enabled = true
port = 8083
bind_scope = "localhost"
auth_token = "SECRETTOK123"
"""

BADHID_LAN = """\
[main.plugins.badhid_ng]
enabled = true
port = 8083
bind_scope = "lan"
auth_token = "SECRETTOK123"
"""


# --- badhid_phone.sh -------------------------------------------------------
def test_phone_localhost_warns_no_url():
    with tempfile.TemporaryDirectory() as d:
        if os.geteuid() != 0:
            check("phone: localhost warns (skipped, not root)", True); return
        cfg = os.path.join(d, "config.toml"); _write(cfg, BADHID_LOCALHOST)
        home = os.path.join(d, "home"); os.makedirs(home)
        _write(os.path.join(home, "auth_token.txt"), "SECRETTOK123\n")
        r = _run("badhid_phone.sh", [], {"CONFIG": cfg, "BADHID_HOME": home})
        check("phone: localhost warns reachability", "isn't reachable" in r.stdout)
        check("phone: localhost emits no token URL", "token=SECRETTOK123" not in r.stdout)


def test_phone_lan_builds_url():
    with tempfile.TemporaryDirectory() as d:
        if os.geteuid() != 0:
            check("phone: lan builds url (skipped, not root)", True); return
        cfg = os.path.join(d, "config.toml"); _write(cfg, BADHID_LAN)
        home = os.path.join(d, "home"); os.makedirs(home)
        _write(os.path.join(home, "auth_token.txt"), "SECRETTOK123\n")
        r = _run("badhid_phone.sh", [], {"CONFIG": cfg, "BADHID_HOME": home})
        check("phone: lan builds token URL", "token=SECRETTOK123" in r.stdout)
        check("phone: lan uses :8083", ":8083/" in r.stdout)


# --- badhid_setup.sh -------------------------------------------------------
def test_setup_noninteractive_keeps_localhost():
    # the safe default: non-interactive must enable the plugin but NOT expose LAN
    with tempfile.TemporaryDirectory() as d:
        if os.geteuid() != 0:
            check("setup: safe default (skipped, not root)", True); return
        cfg = os.path.join(d, "config.toml"); _write(cfg, BADHID_LOCALHOST.replace("enabled = true", "enabled = false"))
        plugins = os.path.join(d, "plugins"); os.makedirs(plugins)
        _write(os.path.join(plugins, "badhid_ng.py"), "# stub\n")
        udc = os.path.join(d, "udc", "fake"); os.makedirs(udc)
        hidg = os.path.join(d, "hidg0"); _write(hidg, "")
        binp = os.path.join(d, "bin"); os.makedirs(binp)
        _write(os.path.join(binp, "systemctl"), "#!/bin/sh\nexit 0\n")
        os.chmod(os.path.join(binp, "systemctl"), 0o755)
        env = {
            "CONFIG": cfg, "PLUGINS_DIR": plugins,
            "UDC_DIR": os.path.join(d, "udc"), "HIDG_DEV": hidg,
            "BADHID_HOME": os.path.join(d, "home"),
            "PATH": binp + ":" + os.environ.get("PATH", ""),
        }
        r = _run("badhid_setup.sh", [], env)
        txt = open(cfg).read()
        check("setup: non-interactive enabled plugin", "enabled = true" in _badhid_block(txt))
        check("setup: non-interactive kept localhost", 'bind_scope = "localhost"' in txt)


def main():
    test_setopt_update_in_place()
    test_setopt_isolation()
    test_setopt_append_when_missing()
    test_setopt_comment_mention_not_matched()
    test_setopt_missing_block()
    test_phone_localhost_warns_no_url()
    test_phone_lan_builds_url()
    test_setup_noninteractive_keeps_localhost()

    print()
    if failures:
        print(f"{len(failures)} FAILURE(S): {failures}")
        sys.exit(1)
    print("all setup-helper sandbox tests passed")


if __name__ == "__main__":
    main()
