"""Sandbox self-test: re-exec into the OS sandbox and verify it holds.

Run on any machine: `python tools/sandbox_selftest.py`
  - macOS: seatbelt backend
  - linux: bubblewrap backend (install with: sudo apt install bubblewrap)
  - no backend: prints SKIP and exits 0

Verifies: writes outside the run dir are denied, writes inside are
allowed, and the headless-Chrome SVG rasterization works under the
sandbox (the trickiest part — nested sandbox + capture harvesting).
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

OUT = os.path.join(ROOT, "results-sandbox-selftest")
os.makedirs(OUT, exist_ok=True)

from wl_benchmark import sandbox  # noqa: E402

BASELINE = "--baseline" in sys.argv

if BASELINE:
    print("baseline mode: chrome raster only, no sandbox")
elif not sandbox.in_sandbox():
    backend = sandbox._backend()
    print(f"backend: {backend}")
    if backend is None:
        print("SKIP: no sandbox backend on this machine")
        sys.exit(0)
    sandbox.maybe_reexec(OUT)      # execs; never returns
    raise SystemExit("re-exec failed")   # pragma: no cover

# ---- (baseline) or (inside the sandbox) -------------------------------
fails = []
if BASELINE:
    for name in [n for n in ("write-denial", "read", "network")]:
        pass    # confinement checks are meaningless unsandboxed


def check(name, ok):
    print(("PASS" if ok else "FAIL") + f": {name}")
    if not ok:
        fails.append(name)


if not BASELINE:
    # writes inside the run dir are allowed
    try:
        open(os.path.join(OUT, "ok.txt"), "w").write("x")
        check("write inside run dir", True)
    except OSError as e:
        check(f"write inside run dir ({e})", False)

    # writes outside are denied (seatbelt: EPERM; bwrap: EROFS)
    import errno
    for victim in (os.path.expanduser("~/wlb-evil.txt"),
                   "/etc/wlb-evil.conf"):
        try:
            open(victim, "w").write("evil")
            os.unlink(victim)
            check(f"write denied: {victim}", False)
        except OSError as e:
            denied = e.errno in (errno.EPERM, errno.EACCES, errno.EROFS)
            check(f"write denied: {victim} "
                  f"({errno.errorcode.get(e.errno, '?')})", denied)

    # reads anywhere are allowed
    try:
        open("/etc/hosts").read()[:1]
        check("read /etc allowed", True)
    except OSError:
        check("read /etc allowed", False)

    # network is open
    try:
        import urllib.request
        req = urllib.request.Request("https://pypi.org",
                                     headers={"User-Agent": "wl-benchmark"})
        urllib.request.urlopen(req, timeout=15).read(1)
        check("outbound network", True)
    except Exception as e:  # noqa: BLE001
        check(f"outbound network ({e})", False)

# ---- the hard part: Chrome rasterization inside the sandbox ------------
SVG = ("<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 200 100'"
       " width='200' height='100'><rect width='200' height='100'"
       " fill='#eef'/><circle cx='100' cy='50' r='30' fill='#36f'/></svg>")
svg_path = os.path.join(OUT, "selftest.svg")
png_path = os.path.join(OUT, "selftest.png")
open(svg_path, "w").write(SVG)
try:
    from wl_benchmark.tasks.svg import svg_to_png
    svg_to_png(svg_path, png_path, size=512)
    check(f"chrome raster "
          f"({os.path.getsize(png_path)} bytes)",
          os.path.getsize(png_path) > 1000)
except Exception as e:  # noqa: BLE001
    check(f"chrome raster ({e})", False)

if fails:
    print("SELFTEST FAILED: " + ", ".join(fails))
    sys.exit(1)
print("SELFTEST PASSED")
sys.exit(1 if fails else 0)
