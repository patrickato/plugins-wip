#!/bin/bash
# remoteexecctl.sh - friendly control for the remoteexec agent so you never type
# curl by hand. Run ON the pi (or anywhere that can reach the agent URL + token).
#
#   ./remoteexecctl.sh status            # is the agent up? (GET /)
#   ./remoteexecctl.sh tasks             # list the allowed named tasks
#   ./remoteexecctl.sh run <task>        # run one named task, print its JSON
#
# Token is read from TOKEN_FILE (default /etc/pwnagotchi/remoteexec_ng/
# auth_token.txt), or set REMOTEEXEC_TOKEN. Base URL defaults to
# http://127.0.0.1:<port-from-config>; override with REMOTEEXEC_URL.
set -euo pipefail

CONFIG="${CONFIG:-/etc/pwnagotchi/config.toml}"
TOKEN_FILE="${TOKEN_FILE:-/etc/pwnagotchi/remoteexec_ng/auth_token.txt}"

blk() {  # print only the [main.plugins.remoteexec_ng] block (section-bounded)
  awk '/^\[main\.plugins\.remoteexec_ng\][[:space:]]*$/{i=1;next}/^\[/{i=0}i' "$CONFIG" 2>/dev/null
}

TOKEN="${REMOTEEXEC_TOKEN:-}"
# The token file is root-only by design. If it exists but we can't read it,
# that's almost always "ran without sudo" - say so plainly instead of leaking a
# raw shell 'Permission denied'.
if [ -z "$TOKEN" ] && [ -f "$TOKEN_FILE" ] && [ ! -r "$TOKEN_FILE" ]; then
  echo "ERROR: $TOKEN_FILE is root-only - re-run with sudo:  sudo $0 $*" >&2
  exit 1
fi
[ -z "$TOKEN" ] && [ -r "$TOKEN_FILE" ] && TOKEN="$(tr -d '\r\n' < "$TOKEN_FILE")"
[ -z "$TOKEN" ] && [ -r "$CONFIG" ] && TOKEN="$(blk | grep -m1 '^[[:space:]]*auth_token' | cut -d'"' -f2 || true)"
[ -n "$TOKEN" ] || { echo "ERROR: no token found. Run with sudo, or set REMOTEEXEC_TOKEN=..." >&2; exit 1; }

PORT="$(blk | grep -m1 '^[[:space:]]*port' | tr -dc '0-9')"; PORT="${PORT:-8084}"
URL="${REMOTEEXEC_URL:-http://127.0.0.1:$PORT}"

cmd="${1:-status}"
case "$cmd" in
  status)
    curl -fsS -H "Authorization: Bearer $TOKEN" "$URL/" -o /dev/null -w "agent up at %{url_effective} (HTTP %{http_code})\n" 2>/dev/null \
      || { echo "agent not answering at $URL (enabled? restarted? check the pwnagotchi log for the bound URL)"; exit 1; } ;;
  tasks)
    curl -fsS -H "Authorization: Bearer $TOKEN" "$URL/tasks" || { echo "request failed (see above)"; exit 1; }; echo ;;
  run)
    task="${2:-}"
    [ -n "$task" ] || { echo "usage: $0 run <task>   (see: $0 tasks)"; exit 2; }
    curl -fsS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
      -d "{\"task\":\"$task\"}" "$URL/run" || { echo "request failed (see above)"; exit 1; }; echo ;;
  *)
    echo "usage: $0 {status|tasks|run <task>}"; exit 2 ;;
esac
