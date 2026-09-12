import os, subprocess, sys, tempfile, time
import shutil
CHROME = (shutil.which("google-chrome")
          or "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
SVG = ("<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 200 100' width='200' height='100'>"
       "<rect width='200' height='100' fill='#eef'/><circle cx='100' cy='50' r='30' fill='#36f'/></svg>")
d = tempfile.mkdtemp(prefix="bisect-")
wrap = os.path.join(d, "w.html")
open(wrap, "w").write('<body style="margin:0;background:#fff"><div style="width:512px;height:512px">' + SVG + "</div>")
BASE = [CHROME, "--headless=new", "--disable-gpu", "--no-sandbox",
        "--disable-crashpad", "--disable-crash-reporter",
        "--virtual-time-budget=2000"]
VARIANTS = {
    "A about:blank minimal": BASE + ["--user-data-dir=" + os.path.join(d, "udA"),
        "--screenshot=" + os.path.join(d, "a.png"), "--window-size=300,300", "about:blank"],
    "B wrap.html minimal": BASE + ["--user-data-dir=" + os.path.join(d, "udB"),
        "--screenshot=" + os.path.join(d, "b.png"), "--window-size=300,300", "file://" + wrap],
    "C wrap.html +512+scale+bg": BASE + ["--force-device-scale-factor=1",
        "--default-background-color=FFFFFF", "--user-data-dir=" + os.path.join(d, "udC"),
        "--screenshot=" + os.path.join(d, "c.png"), "--window-size=512,512", "file://" + wrap],
    "D full current set": BASE + ["--force-device-scale-factor=1",
        "--default-background-color=FFFFFF", "--user-data-dir=" + os.path.join(d, "udD"),
        "--screenshot=" + os.path.join(d, "d.png"), "--window-size=512,512", "file://" + wrap],
    "E about:blank full set": BASE + ["--force-device-scale-factor=1",
        "--default-background-color=FFFFFF", "--user-data-dir=" + os.path.join(d, "udE"),
        "--screenshot=" + os.path.join(d, "e.png"), "--window-size=512,512", "about:blank"],
}
# F: the real svg_to_png function (what the pipeline uses)
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    from wl_benchmark.tasks.svg import svg_to_png
    from wl_benchmark.chrome_capture import run_chrome_capture
    orig = run_chrome_capture
    def spy(cmd, out_path, timeout=60):
        print("REAL-CMD:", " ".join(cmd), flush=True)
        return orig(cmd, out_path, timeout)
    import wl_benchmark.chrome_capture as cc
    cc.run_chrome_capture = spy
    fsvg = os.path.join(d, "real.svg")
    open(fsvg, "w").write(SVG)
    fpng = os.path.join(d, "real.png")
    try:
        svg_to_png(fsvg, fpng, size=512)
        print(f"{'PASS' if os.path.getsize(fpng) > 100 else 'FAIL'} "
              f"F real svg_to_png: {os.path.getsize(fpng)} bytes")
    except Exception as e:
        print("FAIL F real svg_to_png:", str(e)[:150])
except Exception as e:
    print("FAIL F setup:", str(e)[:150])

for name, cmd in VARIANTS.items():
    png = cmd[cmd.index([a for a in cmd if a.startswith("--screenshot=")][0])].split("=", 1)[1]
    p = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    t0 = time.time()
    ok = False
    while time.time() - t0 < 25:
        if p.poll() is not None: break
        if os.path.exists(png) and os.path.getsize(png) > 100:
            ok = True; break
        time.sleep(0.3)
    p.terminate()
    try: p.wait(5)
    except Exception: p.kill()
    sz = os.path.getsize(png) if os.path.exists(png) else 0
    print(f"{'PASS' if ok else 'FAIL'} {name}: {sz} bytes, rc={p.returncode}")
