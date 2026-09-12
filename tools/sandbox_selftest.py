"""Sandbox self-test: spawn the sandboxed child and verify it holds.

Run on any machine: `python tools/sandbox_selftest.py`
  --baseline   chrome raster only, no sandbox (sanity baseline)
  no flags     full test: parent spawns a sandboxed child of THIS file
               via the real spawn machinery (fd-passing tunnel included)

Checks inside the child: writes outside the run dir denied, writes
inside allowed, direct TCP/UDP denied, local Unix sockets (docker.sock
class) denied, tunnel to the exact whitelisted target works, tunnel
refuses anything else (other port on the same host included), Chrome
raster works.
"""
import http.server
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

SVG = ("<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 200 100'"
       " width='200' height='100'><rect width='200' height='100'"
       " fill='#eef'/><circle cx='100' cy='50' r='30' fill='#36f'/></svg>")


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
    svg_path = os.path.join(OUT, "selftest.svg")
    png_path = os.path.join(OUT, "selftest.png")
    open(svg_path, "w").write(SVG)
    try:
        from wl_benchmark.tasks.svg import svg_to_png
        svg_to_png(svg_path, png_path, size=512)
        print(f"PASS: chrome raster ({os.path.getsize(png_path)} bytes)")
        print("SELFTEST PASSED")
        sys.exit(0)
    except Exception as e:  # noqa: BLE001
        print(f"FAIL: chrome raster: {str(e)[:200]}")
        print("SELFTEST FAILED")
        sys.exit(1)


if CHILD:
    # ---- inside the sandbox child ----------------------------------------
    from wl_benchmark import tunnel_client
    tunnel_client.install(int(os.environ[sandbox.SOCK_ENV]))
    port = int(os.environ["WL_BENCH_SELFTEST_PORT"])
    wrong_port = int(os.environ["WL_BENCH_SELFTEST_WRONG_PORT"])
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

    # raw UDP is denied too
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.sendto(b"x", ("93.184.216.34", 53))
        check("direct UDP denied", False)
    except OSError:
        check("direct UDP denied", True)

    # local Unix sockets are denied (docker.sock class — would be a
    # full host escape if reachable)
    unix_leak = False
    for path in ("/var/run/docker.sock", "/private/var/run/docker.sock"):
        if os.path.exists(path):
            try:
                s = socket.socket(socket.AF_UNIX)
                s.settimeout(3)
                s.connect(path)
                unix_leak = True
            except OSError:
                pass
    check("local unix sockets denied (docker.sock class)",
          not unix_leak)

    # the tunnel reaches the exact whitelisted target
    try:
        import urllib.request
        body = urllib.request.urlopen(
            f"http://127.0.0.1:{port}/", timeout=15).read()
        check(f"tunnel to whitelisted target ({body!r})", body == b"ok")
    except Exception as e:  # noqa: BLE001
        check(f"tunnel to whitelisted target ({e})", False)

    # the tunnel refuses a different port on the same host
    try:
        import urllib.request
        urllib.request.urlopen(f"http://127.0.0.1:{wrong_port}/",
                               timeout=10)
        check("tunnel refuses non-whitelisted port", False)
    except Exception:  # noqa: BLE001
        check("tunnel refuses non-whitelisted port", True)

    # the hard part: Chrome rasterization inside the sandbox
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
    if os.environ.get("WL_BENCH_SELFTEST_REQUIRE"):
        print("FAIL: no sandbox backend on this machine (required)")
        sys.exit(1)
    print("SKIP: no sandbox backend on this machine")
    sys.exit(0)
print(f"backend: {sandbox._backend()}")

port = _local_server()
# a second server on a different port (must be refused by the tunnel)
wrong_port = _local_server()

rc = sandbox.spawn_sandboxed_entry(
    [sys.executable, "-u", __file__],
    OUT,
    allowed={("127.0.0.1", port)},
    env_extra={
        "WL_BENCH_SELFTEST_CHILD": "1",
        "WL_BENCH_SELFTEST_PORT": str(port),
        "WL_BENCH_SELFTEST_WRONG_PORT": str(wrong_port),
    })
sys.exit(rc)
