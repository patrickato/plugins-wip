#!/bin/sh
# Tweak View NG - plug-and-play installer for Pwnagotchi (Jayofelony 2.9.5.8/2.9.5.9).
#
# Run it on your Pwnagotchi, as root. Two ways:
#
#   One-liner (downloads the plugin itself):
#     curl -fsSL https://raw.githubusercontent.com/patrickato/plugins-wip/main/tweak-view-ng-suite/install.sh | sudo sh
#
#   From a local checkout of this folder:
#     sudo sh install.sh
#
# What it does: finds your plugin directory from config.toml (falling back to the
# Jayofelony default), backs up any existing copy, installs tweak_view_ng.py,
# adds a [main.plugins.tweak_view_ng] section to config.toml if one isn't there
# already (it never overwrites settings you've set), restarts Pwnagotchi and
# prints the editor URL.
#
# Env overrides (optional):
#   RAW_BASE=<url>   where to download from (default: this plugin on GitHub 'main')
#   CONFIG=<path>    config.toml path (default: /etc/pwnagotchi/config.toml)
#   NO_RESTART=1     install but don't restart Pwnagotchi
set -eu

RAW_BASE="${RAW_BASE:-https://raw.githubusercontent.com/patrickato/plugins-wip/main/tweak-view-ng-suite}"
CONFIG="${CONFIG:-/etc/pwnagotchi/config.toml}"
PLUGIN="tweak_view_ng.py"
SECTION="main.plugins.tweak_view_ng"

say()  { printf '%s\n' "$*"; }
die()  { printf 'error: %s\n' "$*" >&2; exit 1; }

# --- must be root (we write under /etc) ---
if [ "$(id -u)" -ne 0 ]; then
  die "please run as root (use: sudo sh install.sh  — or pipe the one-liner to 'sudo sh')"
fi

[ -f "$CONFIG" ] || die "config not found at $CONFIG (set CONFIG=/path/to/config.toml)"

# --- figure out where Pwnagotchi loads custom plugins from ---
PLUGDIR="$(sed -n 's/^[[:space:]]*main\.custom_plugins[[:space:]]*=[[:space:]]*"\{0,1\}\([^"#]*[^"# ]\)"\{0,1\}.*/\1/p' "$CONFIG" | head -1)"
[ -n "${PLUGDIR:-}" ] || PLUGDIR="/etc/pwnagotchi/custom-plugins/"
# strip a trailing slash for tidiness, then re-add one consistent form
PLUGDIR="${PLUGDIR%/}"
say "plugin directory: $PLUGDIR"
mkdir -p "$PLUGDIR"

DST="$PLUGDIR/$PLUGIN"
TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT

# --- get the plugin: prefer a local copy next to this script, else download ---
SRC_LOCAL=""
if [ -f "./$PLUGIN" ]; then SRC_LOCAL="./$PLUGIN"; fi
if [ -z "$SRC_LOCAL" ] && [ -n "${0:-}" ] && [ -f "$(dirname "$0")/$PLUGIN" ]; then
  SRC_LOCAL="$(dirname "$0")/$PLUGIN"
fi

if [ -n "$SRC_LOCAL" ]; then
  say "installing from local file: $SRC_LOCAL"
  cp "$SRC_LOCAL" "$TMP"
else
  say "downloading $PLUGIN from $RAW_BASE"
  if command -v curl >/dev/null 2>&1; then
    curl -fsSL -o "$TMP" "$RAW_BASE/$PLUGIN" || die "download failed"
  elif command -v wget >/dev/null 2>&1; then
    wget -qO "$TMP" "$RAW_BASE/$PLUGIN" || die "download failed"
  else
    die "need curl or wget to download (or run from a local checkout)"
  fi
fi

# --- sanity-check it's valid Python before putting it in place ---
if command -v python3 >/dev/null 2>&1; then
  python3 -c "import ast,sys; ast.parse(open(sys.argv[1]).read())" "$TMP" \
    || die "downloaded file failed a syntax check; not installing"
fi
grep -q "class TweakViewNG" "$TMP" || die "that doesn't look like the Tweak View NG plugin; not installing"

# --- back up any existing install, then put the new one in place ---
if [ -f "$DST" ]; then
  BAK="$DST.bak.$(date +%Y%m%d-%H%M%S)"
  cp -a "$DST" "$BAK"
  say "backed up existing plugin -> $BAK"
fi
install -m 0644 "$TMP" "$DST"
VER="$(sed -n 's/.*__version__ = "\([^"]*\)".*/\1/p' "$DST" | head -1)"
say "installed $DST (version ${VER:-unknown})"

# --- enable it in config.toml if it isn't already configured ---
if grep -q "^\[$SECTION\]" "$CONFIG" || grep -q "^[[:space:]]*$SECTION\.enabled" "$CONFIG"; then
  say "config already has [$SECTION] — leaving your settings untouched"
  say "  (make sure it has: enabled = true)"
else
  cp -a "$CONFIG" "$CONFIG.bak.$(date +%Y%m%d-%H%M%S)"
  {
    printf '\n[%s]\n' "$SECTION"
    printf 'enabled = true\n'
    printf 'filename = "/etc/pwnagotchi/tweak_view_ng.json"\n'
    printf 'legacy_filename = "/etc/pwnagotchi/tweak_view.json"\n'
    printf 'auto_import_legacy = true\n'
    printf 'backup = true\n'
    printf 'history_limit = 50\n'
    printf 'strict_version = false\n'
  } >> "$CONFIG"
  say "added [$SECTION] to $CONFIG (backed up first)"
fi

# --- work out the editor URL (host + web port) ---
PORT="$(sed -n 's/^[[:space:]]*ui\.web\.port[[:space:]]*=[[:space:]]*\([0-9]\{1,\}\).*/\1/p' "$CONFIG" | head -1)"
[ -n "${PORT:-}" ] || PORT="8080"
HOST="$(hostname 2>/dev/null || echo pwnagotchi)"
URL="http://$HOST:$PORT/plugins/tweak_view_ng/"

# --- restart Pwnagotchi so the plugin loads ---
if [ "${NO_RESTART:-0}" = "1" ]; then
  say "skipping restart (NO_RESTART=1) — restart Pwnagotchi yourself to load the plugin"
elif command -v systemctl >/dev/null 2>&1; then
  say "restarting pwnagotchi..."
  systemctl restart pwnagotchi || say "couldn't restart automatically — run: sudo systemctl restart pwnagotchi"
else
  say "restart Pwnagotchi to load the plugin: sudo systemctl restart pwnagotchi"
fi

say ""
say "Done. In ~30-60s, open the editor:"
say "    $URL"
say "If the full editor won't load, the fallback is at .../plugins/tweak_view_ng/recovery"
