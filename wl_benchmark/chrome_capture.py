"""Run a headless-Chrome capture without waiting for its exit.

Under the wlb seatbelt sandbox Chrome reliably PRODUCES the output
file but sometimes never exits (its own nested sandbox cannot
initialize). We poll for the output file, harvest it once its size is
stable, and terminate the browser.
"""
from __future__ import annotations

import os
import subprocess
import time


def run_chrome_capture(cmd: list, out_path: str, timeout: float = 60) -> None:
    """Run cmd; succeed once out_path exists and is stable (or the
    process exits cleanly). Raises RuntimeError otherwise."""
    import tempfile
    err_file = tempfile.NamedTemporaryFile(prefix="wlb-chrome-err-",
                                           suffix=".log", delete=False)
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                            stderr=err_file)
    deadline = time.time() + timeout
    last_size, last_change = -1, time.time()
    try:
        while time.time() < deadline:
            if proc.poll() is not None:          # exited by itself
                break
            if os.path.exists(out_path):
                sz = os.path.getsize(out_path)
                if sz > 0:
                    if sz == last_size:
                        if time.time() - last_change > 1.0:
                            return               # written and stable
                    else:
                        last_size, last_change = sz, time.time()
            time.sleep(0.2)
        # final check after exit / deadline
        if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
            return
        tail = ""
        try:
            with open(err_file.name, "rb") as f:
                tail = f.read()[-3000:].decode(errors="replace") \
                    .replace("\n", " | ")
        except OSError:
            pass
        status = ('timed out' if proc.poll() is None
                  else f'exit {proc.returncode}')
        raise RuntimeError(
            f"chrome capture produced no output ({status})"
            + (f": {tail}" if tail else ""))
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        err_file.close()
        try:
            os.unlink(err_file.name)   # no per-capture temp leak
        except OSError:
            pass
