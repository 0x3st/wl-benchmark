"""Sandbox self-test: spawn the sandboxed child and verify it holds.

Run on any machine: `python tools/sandbox_selftest.py`
  --baseline   chrome raster only, no sandbox (sanity baseline)
  no flags     full test: parent spawns a sandboxed child of THIS file
               via the real spawn machinery (tunnel included)

Checks inside the child: writes outside the run dir denied, writes
inside allowed, direct TCP denied, tunnel to a local server works,
tunnel refuses non-whitelisted hosts, Chrome raster works.
"""
import http.server
import json
import os
import socket
import sys
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from wl_benchmark import sandbox  # noqa: E402

OUT = os.path.join(ROOT, "results-sandbox-selftest")
os.makedirs(OUT, exist_ok=True)

CHILD = os.environ.get("WL_BENCH_SELFTEST_CHILD") == "1"


def _local_server() -> int:
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv.server_address[1]


if "--baseline" in sys.argv:
    print("baseline mode: chrome raster only, no sandbox")
    SVG = ("<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 200 100'"
           " width='200' height='100'><rect width='200' height='100'"
           " fill='#eef'/><circle cx='100' cy='50' r='30' fill='#36f'/></svg>")
    from wl_benchmark.tasks.svg import svg_to_png
    svg_path = os.path.join(OUT, "selftest.svg")
    png_path = os.path.join(OUT, "selftest.png")
    open(svg_path, "w").write(SVG)
    try:
        svg_to_png(svg_path, png_path, size=512)
        print("PASS: chrome raster "
              f"({os.path.getsize(png_path)} bytes)")
        print("SELFTEST PASSED")
        sys.exit(0)
    except Exception as e:  # noqa: BLE001
        print(f"FAIL: chrome raster: {str(e)[:200]}")
        print("SELFTEST FAILED")
        sys.exit(1)


if CHILD:
    # ---- inside the sandbox child ----------------------------------------
    from wl_benchmark import tunnel_client
    tunnel_client.install(os.environ[sandbox.SOCK_ENV])
    port = int(os.environ["WL_BENCH_SELFTEST_PORT"])
    fails = []

    def check(name, ok):
        print(("PASS" if ok else "FAIL") + f": {name}")
        if not ok:
            fails.append(name)

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

    # direct TCP is denied (this is the point of the network sandbox)
    try:
        socket.create_connection(("93.184.216.34", 80), timeout=5)
        check("direct TCP denied", False)
    except OSError:
        check("direct TCP denied", True)

    # the tunnel reaches the whitelisted local server
    try:
        import urllib.request
        body = urllib.request.urlopen(
            f"http://127.0.0.1:{port}/", timeout=15).read()
        check(f"tunnel to whitelisted host ({body!r})", body == b"ok")
    except Exception as e:  # noqa: BLE001
        check(f"tunnel to whitelisted host ({e})", False)

    # the tunnel refuses non-whitelisted hosts
    try:
        import urllib.request
        urllib.request.urlopen("http://192.0.2.1/", timeout=10)
        check("tunnel refuses non-whitelisted host", False)
    except Exception as e:  # noqa: BLE001
        check("tunnel refuses non-whitelisted host", True)

    # the hard part: Chrome rasterization inside the sandbox
    SVG = ("<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 200 100'"
           " width='200' height='100'><rect width='200' height='100'"
           " fill='#eef'/><circle cx='100' cy='50' r='30' fill='#36f'/></svg>")
    svg_path = os.path.join(OUT, "selftest.svg")
    png_path = os.path.join(OUT, "selftest.png")
    open(svg_path, "w").write(SVG)
    try:
        from wl_benchmark.tasks.svg import svg_to_png
        svg_to_png(svg_path, png_path, size=512)
        check(f"chrome raster ({os.path.getsize(png_path)} bytes)",
              os.path.getsize(png_path) > 1000)
    except Exception as e:  # noqa: BLE001
        check(f"chrome raster: {str(e)[:120]}", False)

    if fails:
        print("SELFTEST FAILED: " + ", ".join(fails))
        sys.exit(1)
    print("SELFTEST PASSED")
    sys.exit(0)


# ---- parent: spawn the sandboxed child of this file --------------------
if not sandbox.available():
    print("SKIP: no sandbox backend on this machine")
    sys.exit(0)
print(f"backend: {sandbox._backend()}")

port = _local_server()
payload = {"selftest": True}       # not used by the child, kept for shape
child_env_extra = {
    "WL_BENCH_SELFTEST_CHILD": "1",
    "WL_BENCH_SELFTEST_PORT": str(port),
}

# reuse spawn_sandboxed's machinery with a custom child entry
import tempfile  # noqa: E402
from wl_benchmark.tunnel import Tunnel  # noqa: E402

backend = sandbox._backend()
out_root = os.path.abspath(OUT)
tunnel = Tunnel({"127.0.0.1"})
child_env = dict(os.environ, **child_env_extra,
                 **{sandbox.MARKER: "1", sandbox.SOCK_ENV: tunnel.path})
entry = [sys.executable, "-u", __file__]
try:
    if backend == "seatbelt":
        profile = sandbox._seatbelt_profile(out_root)
        fd, prof_path = tempfile.mkstemp(suffix=".sb", prefix="wlb-")
        with os.fdopen(fd, "w") as f:
            f.write(profile)
        argv = [sandbox.SANDBOX_EXEC, "-f", prof_path] + entry
    else:
        argv = sandbox._bwrap_cmd(out_root, tunnel.path) + entry
    import signal
    import subprocess
    prev = signal.signal(signal.SIGINT, signal.SIG_IGN)
    try:
        proc = subprocess.Popen(argv, env=child_env)
        rc = proc.wait()
    finally:
        signal.signal(signal.SIGINT, prev)
finally:
    tunnel.close()
sys.exit(rc if rc >= 0 else 128 - rc)
