"""Version check + self-update, install-method aware.

Detection logic (how was this wlb installed?):
  source   package dir sits in a repo checkout (pyproject.toml + .git
           next to the package)        -> never self-update; git pull
  pipx     dist path contains "pipx"   -> pipx upgrade wl-benchmark
  brew     dist path contains Cellar / homebrew -> brew upgrade 0x3st/tap/wlb
  pip      anything else installed     -> <sys.executable> -m pip install -U

Latest version comes from the PyPI JSON API, cached for 6 h in the user
cache dir so startup stays fast. Network failures are silent: the tool
must work offline.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import urllib.request
from importlib.metadata import PackageNotFoundError, distribution, version
from pathlib import Path

PYPI_JSON = "https://pypi.org/pypi/wl-benchmark/json"
PKG = "wl-benchmark"
CACHE = Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache"))
             ) / "wlb" / "version-check.json"


# ----------------------------------------------------------- detection
def install_mode() -> str:
    """'source' | 'pipx' | 'brew' | 'pip' | 'unknown'."""
    pkg_dir = Path(__file__).resolve().parent.parent
    if (pkg_dir / "pyproject.toml").exists() and (pkg_dir / ".git").exists():
        return "source"
    try:
        base = str(distribution(PKG).locate_file("")).lower()
    except PackageNotFoundError:
        return "source"      # shim from a checkout without installation
    if "pipx" in base:
        return "pipx"
    if "cellar" in base or "homebrew" in base:
        return "brew"
    if base.strip():
        return "pip"
    return "unknown"


def running_version() -> str:
    try:
        return version(PKG)
    except PackageNotFoundError:
        return "0.0.0"       # source checkout; pyproject holds the truth


# -------------------------------------------------------------- lookup
def _cache_read(max_age: float) -> str | None:
    try:
        data = json.loads(CACHE.read_text())
        if time.time() - data["ts"] < max_age:
            return data["latest"]
    except (OSError, KeyError, json.JSONDecodeError):
        pass
    return None


def _cache_write(latest: str) -> None:
    try:
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        CACHE.write_text(json.dumps({"ts": time.time(), "latest": latest}))
    except OSError:
        pass


def _fetch(timeout: float, bypass_proxy: bool = False) -> str | None:
    try:
        if bypass_proxy:
            # macOS system proxies (urllib.getproxies) often point at a
            # local proxy app that is not running — retry direct
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        else:
            opener = urllib.request.build_opener()
        req = urllib.request.Request(PYPI_JSON,
                                     headers={"User-Agent": "wl-benchmark"})
        with opener.open(req, timeout=timeout) as r:
            return json.load(r)["info"]["version"]
    except Exception:        # noqa: BLE001
        return None


def latest_pypi(timeout: float = 2.5) -> str | None:
    """Latest version on PyPI.

    Every interactive run fetches fresh (the lookup runs in a background
    thread while the user types). Order: normal -> direct (proxy
    bypassed) -> stale disk cache as last resort. None only when every
    path failed.
    """
    latest = _fetch(timeout)
    if latest is None:
        latest = _fetch(timeout, bypass_proxy=True)
    if latest is not None:
        _cache_write(latest)
        return latest
    return _cache_read(10 ** 9)   # offline fallback: any-age cache


# --------------------------------------------------------------- update
def _update_command(mode: str) -> list[str] | None:
    if mode == "pipx":
        return ["pipx", "upgrade", PKG]
    if mode == "brew":
        return ["brew", "upgrade", "0x3st/tap/wlb"]
    if mode == "pip":
        return [sys.executable, "-m", "pip", "install", "--upgrade", PKG]
    return None


def update(mode: str) -> bool:
    """Run the upgrade command for the detected install method."""
    cmd = _update_command(mode)
    if not cmd:
        return False
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    except (OSError, subprocess.TimeoutExpired):
        return False
    if proc.returncode != 0:
        print((proc.stderr or proc.stdout or "")[-400:])
        return False
    return True


def check_background() -> tuple[threading.Thread, list]:
    """Start the PyPI lookup in a daemon thread; returns (thread, box)
    where box[0] holds the latest version once the thread finishes."""
    box: list = [None]

    def work():
        box[0] = latest_pypi()

    th = threading.Thread(target=work, daemon=True)
    th.start()
    return th, box


def maybe_upgrade(latest: str | None, interactive: bool = True) -> None:
    """If a newer release exists, offer the update (before testing starts).

    Exits the process after a successful upgrade — the running code is
    the old one, so the user should re-run `wlb` on the new version.
    """
    if install_mode() == "source":
        # a checkout is by definition the freshest code; never nag
        return
    if not latest or latest <= current:
        return
    if not interactive:
        print(f"[update] v{latest} available (installed {current}) — "
              f"update skipped in non-interactive mode")
        return
    ans = input(f"Update wl-benchmark to v{latest} (installed {current})? "
                f"[Y/n] ").strip().lower()
    if ans in ("n", "no"):
        return
    print(f"[update] {mode}: upgrading …")
    if update(mode):
        CACHE.unlink(missing_ok=True)   # re-check against the new release
        print(f"[update] done — v{latest} installed, please re-run `wlb`")
        raise SystemExit(0)
    print("[update] failed — continuing with the current version; manual "
          f"hint: {_update_command(mode)}")
