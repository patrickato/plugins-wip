"""Integration-test harness: run Tweak View NG against the REAL Jayofelony framework.

Unlike ``tests/conftest.py`` (which installs hand-written stubs), this harness
imports the *actual* Jayofelony Pwnagotchi ``pwnagotchi.ui.*`` modules and the
real plugin loader, and exercises Tweak View NG against them.

How the real framework is located, in order:

1. ``PWNAGOTCHI_SRC`` env var pointing at a jayofelony/pwnagotchi checkout.
2. A sibling ``pwnagotchi`` package already importable on ``sys.path``.
3. A checkout previously cloned to ``<repo>/.cache/jayo-pwnagotchi``.
4. A shallow clone of ``https://github.com/jayofelony/pwnagotchi`` at the
   pinned tag into that cache dir (only if git + network are available).

If none of those yield an importable real framework, every test in this
directory is skipped with a clear reason (so the offline unit suite in
``tests/`` still runs green). Nothing here ever imports the stub conftest;
the two harnesses are mutually exclusive by directory.

Only two things are shimmed, and both are sandbox artifacts unrelated to NG:

* ``prctl`` - a libcap-backed C extension used by the loader purely to set a
  cosmetic thread name. Stubbed to a no-op.
* ``PIL.ImageFont.FreeTypeFont.getsize`` - removed in Pillow >= 10; Jayofelony's
  ``DummyDisplay.layout()`` still calls it. Restored from ``getbbox`` so the
  real ``View`` can build under a modern Pillow. On the Pi's pinned Pillow this
  method exists natively and the shim is a no-op.

Everything else - ``View``, ``State``, ``components``, ``fonts``, the plugin
loader - is the genuine Jayofelony code.
"""

import importlib
import importlib.util
import logging
import os
import subprocess
import sys
import types
from pathlib import Path

import pytest

PINNED_REF = "v2.9.5.8"
REPO_URL = "https://github.com/jayofelony/pwnagotchi"
SUITE_ROOT = Path(__file__).resolve().parents[2]  # tweak-view-ng-suite/
CACHE_DIR = SUITE_ROOT / ".cache" / "jayo-pwnagotchi"

_skip_reason = None


def _install_prctl_shim():
    if "prctl" in sys.modules:
        return
    mod = types.ModuleType("prctl")
    mod.set_name = lambda *a, **k: None
    mod.set_proctitle = lambda *a, **k: None
    mod.get_name = lambda *a, **k: "test"
    mod.set_dumpable = lambda *a, **k: None
    sys.modules["prctl"] = mod


def _install_pillow_getsize_shim():
    try:
        from PIL import ImageFont
    except Exception:
        return
    ft = getattr(ImageFont, "FreeTypeFont", None)
    if ft is not None and not hasattr(ft, "getsize"):
        def _getsize(self, text, *a, **k):
            left, top, right, bottom = self.getbbox(text, *a, **k)
            return (right - left, bottom - top)
        ft.getsize = _getsize


def _candidate_roots():
    env = os.environ.get("PWNAGOTCHI_SRC")
    if env:
        yield Path(env).expanduser().resolve()
    # already importable on sys.path (e.g. installed on a real Pi)
    yield None  # sentinel: try a bare import
    if CACHE_DIR.exists():
        yield CACHE_DIR


def _try_import_real(root):
    """Return the imported real pwnagotchi module, or None."""
    if root is not None:
        root = str(root)
        if root not in sys.path:
            sys.path.insert(0, root)
    # Drop any stub the unit conftest may have planted in a shared process.
    for name in list(sys.modules):
        if name == "pwnagotchi" or name.startswith("pwnagotchi."):
            mod = sys.modules[name]
            if getattr(mod, "__TWEAKVIEW_STUB__", False):
                del sys.modules[name]
    try:
        pwn = importlib.import_module("pwnagotchi")
    except Exception:
        return None
    # A real checkout has pwnagotchi.ui.view; the stub does not.
    if importlib.util.find_spec("pwnagotchi.ui.view") is None:
        return None
    return pwn


def _clone_pinned():
    if CACHE_DIR.exists():
        return CACHE_DIR
    git = None
    for cand in ("git",):
        try:
            subprocess.run([cand, "--version"], capture_output=True, check=True)
            git = cand
            break
        except Exception:
            pass
    if git is None:
        return None
    CACHE_DIR.parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(
            [git, "clone", "--depth", "1", "--branch", PINNED_REF, REPO_URL, str(CACHE_DIR)],
            capture_output=True, check=True, timeout=600,
        )
    except Exception:
        # try default branch if the tag is unavailable
        try:
            subprocess.run(
                [git, "clone", "--depth", "1", REPO_URL, str(CACHE_DIR)],
                capture_output=True, check=True, timeout=600,
            )
        except Exception:
            return None
    return CACHE_DIR if CACHE_DIR.exists() else None


def _locate_real_framework():
    _install_prctl_shim()
    _install_pillow_getsize_shim()
    for root in _candidate_roots():
        pwn = _try_import_real(root)
        if pwn is not None:
            return pwn
    cloned = _clone_pinned()
    if cloned is not None:
        pwn = _try_import_real(cloned)
        if pwn is not None:
            return pwn
    return None


# Resolve once at import time so the skip reason is uniform and cheap.
logging.disable(logging.CRITICAL)  # silence the framework's import-time chatter
_real_pwn = _locate_real_framework()
if _real_pwn is None:
    _skip_reason = (
        "real Jayofelony framework not available: set PWNAGOTCHI_SRC to a "
        "jayofelony/pwnagotchi checkout, or allow a network clone of "
        f"{REPO_URL}@{PINNED_REF}"
    )


def pytest_collection_modifyitems(config, items):
    if _skip_reason is None:
        return
    marker = pytest.mark.skip(reason=_skip_reason)
    for item in items:
        item.add_marker(marker)


@pytest.fixture(scope="session")
def real_pwnagotchi():
    if _skip_reason is not None:
        pytest.skip(_skip_reason)
    return _real_pwn


@pytest.fixture(scope="session")
def real_fonts(real_pwnagotchi):
    """Initialize the real font module the way Jayofelony does at boot.

    ``fonts.init(config)`` sets ``STATUS_FONT_NAME``/``SIZE_OFFSET`` and calls
    ``setup()``; both are needed, because ``View`` builds its ``status`` widget
    via ``fonts.status_font()`` which reads ``STATUS_FONT_NAME``. Calling
    ``setup()`` alone (as an earlier draft did) leaves ``STATUS_FONT_NAME`` None
    and breaks the real View build.
    """
    import pwnagotchi.ui.fonts as fonts
    fonts.init({"ui": {"font": {"name": "DejaVuSansMono", "size_offset": 0}}})
    return fonts


def _base_config(width, height):
    """A realistic Jayofelony 2.9.5.8 config dict for building a real View."""
    face_glyphs = {
        "look_r": "( ⚆_⚆)", "look_l": "(☉_☉ )", "look_r_happy": "( ◕‿◕)",
        "look_l_happy": "(◕‿◕ )", "sleep": "(⇀‿‿↼)", "sleep2": "(≖‿‿≖)",
        "awake": "(◕‿‿◕)", "bored": "(-__-)", "intense": "(°▃▃°)",
        "cool": "(⊙☁◉ )", "happy": "(✪‿‿✪)", "excited": "(ᵔ◡◡ᵔ)",
        "grateful": "(^‿‿^)", "motivated": "(☼‿‿☼)", "demotivated": "(≖__≖)",
        "smart": "(✜‿‿✜)", "lonely": "(ب__ب)", "sad": "(╥☁╥ )",
        "angry": "(-_-')", "friend": "(♥‿‿♥)", "broken": "(☓‿‿☓)",
        "debug": "(#__#)", "upload": "(1__0)", "upload1": "(1__1)",
        "upload2": "(0__1)",
    }
    return {
        "main": {"lang": "en"},
        "ui": {
            "invert": False,
            "fps": 0,
            "font": {"name": "DejaVuSansMono", "size_offset": 0},
            "faces": dict({"position_x": 0, "position_y": 40, "png": False}, **face_glyphs),
            "display": {
                "rotation": 0, "type": "dummydisplay",
                "width": width, "height": height, "color": "black",
            },
        },
    }


@pytest.fixture
def real_view_factory(real_pwnagotchi, real_fonts):
    """Build a genuine Jayofelony ``View`` backed by the headless DummyDisplay.

    ``DummyDisplay`` honors width/height from config, so this exercises NG's
    resolution independence against the real View/State/layout code at any size.
    """
    from pwnagotchi.ui.hw.dummydisplay import DummyDisplay
    from pwnagotchi.ui.view import View

    created = []

    def make(width=480, height=320):
        cfg = _base_config(width, height)
        impl = DummyDisplay(config=cfg)
        view = View(config=cfg, impl=impl)
        created.append(view)
        return view

    return make


@pytest.fixture(scope="session")
def ng_module(real_pwnagotchi):
    """Import the NG plugin module against the real framework."""
    root = str(SUITE_ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)
    # flask is a real dependency and importable; no stub needed here.
    return importlib.import_module("tweak_view_ng")
