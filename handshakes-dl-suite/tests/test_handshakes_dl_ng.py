import sys
import os
import io
import time
import zipfile
import tempfile
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "mock_pwnagotchi"))
sys.path.insert(0, os.path.join(HERE, "..", ".."))  # not used, but harmless
sys.path.insert(0, os.path.join(HERE, ".."))

from flask import Flask, request  # noqa: E402

import handshakes_dl_ng  # noqa: E402

failures = []


def check(name, cond):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}")
    if not cond:
        failures.append(name)


REAL_TEMPLATES = "/home/claude/jayofelony/pwnagotchi/pwnagotchi/ui/web/templates"
app = Flask(
    __name__,
    template_folder=REAL_TEMPLATES if os.path.isdir(REAL_TEMPLATES) else None,
)

tmpdir = tempfile.mkdtemp()


def make_file(name, content=b"x", mtime=None):
    path = os.path.join(tmpdir, name)
    with open(path, "wb") as f:
        f.write(content)
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


now = time.time()
make_file("older.pcapng", b"a" * 100, mtime=now - 100)
make_file("older.22000", b"hash-data")
newer_path = make_file("newer.pcapng", b"b" * 5000, mtime=now - 10)
make_file("newer.gps.json", b'{"Latitude": 1.0, "Longitude": 2.0}')
make_file("ignored.pcap", b"legacy format, must never be picked up")

plugin = handshakes_dl_ng.HandshakesDLNG()
plugin.config = {"bettercap": {"handshakes": tmpdir}}
plugin.options = {"max_results": 200}
plugin.ready = True

with app.test_request_context("/"):
    # --- Test 1: .pcap (legacy) files are never collected ---
    results = plugin._collect()
    names = [h.name for h in results]
    check(".pcap file never collected (only .pcapng)", "ignored" not in names)
    check("both .pcapng captures collected", set(names) == {"older", "newer"})

    # --- Test 2: newest-first sort order ---
    check("sorted newest-first", [h.name for h in results] == ["newer", "older"])

    # --- Test 3: hash file detected and attached only to the right capture ---
    older = next(h for h in results if h.name == "older")
    newer = next(h for h in results if h.name == "newer")
    check("hash file (.22000) attached to the capture that has one", ".22000" in older.exts)
    check("capture without a hash file doesn't get one", ".22000" not in newer.exts)

    # --- Test 4: GPS file detected and attached only to the right capture ---
    check("GPS file (.gps.json) attached to the capture that has one", ".gps.json" in newer.exts)
    check("capture without a GPS file doesn't get one", ".gps.json" not in older.exts and ".geo.json" not in older.exts)

    # --- Test 5: size formatting ---
    check("small file formatted in bytes", older.size_human.endswith("B") and "KB" not in older.size_human)
    check("larger file formatted in KB", "KB" in newer.size_human)

    # --- Test 6: full page render works against the REAL base.html template ---
    if os.path.isdir(REAL_TEMPLATES):
        page = plugin.on_webhook("/", request)
        check("page renders without raising", isinstance(page, str))
        check(
            "rendered page includes both capture names",
            "/handshakes_dl_ng/older.pcapng" in page and "/handshakes_dl_ng/newer.pcapng" in page,
        )
        check("rendered page includes the hash download link", ".22000" in page)
        check("rendered page includes the GPS download link", ".gps.json" in page)
        check("rendered page includes the download-all button", "download-all.zip" in page)
    else:
        check("SKIPPED real base.html render (template not found in this env)", True)

    # --- Test 7: max_results cap + truncated flag ---
    plugin.options["max_results"] = 1
    if os.path.isdir(REAL_TEMPLATES):
        page = plugin.on_webhook("/", request)
        # NOTE: "older" is a substring of "placeholder" (from the search
        # box's HTML), so check the actual download-link text instead of
        # a bare substring match.
        check(
            "cap of 1 shows only the newest capture",
            "/handshakes_dl_ng/newer.pcapng" in page and "/handshakes_dl_ng/older.pcapng" not in page,
        )
        check("truncated note appears when capped", "Showing the newest" in page)
    plugin.options["max_results"] = 200

    # --- Test 8: single-file download uses the CURRENT Flask send_from_directory API ---
    resp = plugin.on_webhook("newer.pcapng", request)
    check("single-file download returns 200", resp.status_code == 200)

    # --- Test 9: nonexistent file download 404s cleanly instead of crashing ---
    try:
        plugin.on_webhook("does-not-exist.pcapng", request)
        check("nonexistent file download aborts with 404", False)
    except Exception as e:
        # Flask's abort(404) raises a werkzeug HTTPException with code 404
        code = getattr(e, "code", None)
        check(f"nonexistent file download aborts with 404 (got code={code})", code == 404)

    # --- Test 10: download-all.zip produces a real, correct ZIP ---
    resp = plugin._download_all_zip()
    check("zip response has correct mimetype", resp.mimetype == "application/zip")
    zf = zipfile.ZipFile(io.BytesIO(resp.get_data()))
    names_in_zip = set(zf.namelist())
    check(
        "zip contains capture, hash, and GPS files, but not the ignored .pcap",
        names_in_zip == {"older.pcapng", "older.22000", "newer.pcapng", "newer.gps.json"},
    )

shutil.rmtree(tmpdir)

print()
if failures:
    print(f"{len(failures)} FAILURE(S): {failures}")
    sys.exit(1)
else:
    print("ALL TESTS PASSED")
