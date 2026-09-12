"""OS-level sandbox for the benchmark run.

The whole `wlb` process re-executes itself inside a sandbox with
deny-by-default semantics: file writes are confined to the run output
directory, the temp dir, the cache dir and the browser's own support
dirs; network stays open (the endpoint is user-chosen at prompt time).
Combined with the SVG content sanitizer this contains the only places
where model output is handled.

Backends:
  darwin  Seatbelt via /usr/bin/sandbox-exec
  linux   bubblewrap (bwrap) if installed — read-only root bind with
          selected writable paths
  windows not supported (no practical unprivileged sandbox); the run
          proceeds unsandboxed

When no backend is available the run silently proceeds unsandboxed.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile

MARKER = "WL_BENCH_SANDBOX"
SANDBOX_EXEC = "/usr/bin/sandbox-exec"

PROFILE = """(version 1)
(deny default)
(allow file-read*)
(allow file-write*
    (subpath "/tmp")
    (subpath "/private/tmp")
    (subpath "/private/var/folders")
    (subpath "{tmpdir}")
    (subpath "{out_root}")
    (subpath "{cache_dir}")
    (subpath "{chrome_support}")
    (subpath "{chrome_caches}")
    (literal "/dev/null")
    (literal "/dev/stdout")
    (literal "/dev/stderr"))
(allow process*)
(allow network*)
(allow mach*)
(allow signal)
(allow sysctl*)
(allow ipc-posix*)
(allow user-preference*)
(allow iokit*)
"""


def in_sandbox() -> bool:
    return bool(os.environ.get(MARKER))


def _chrome_dirs() -> list:
    home = os.path.expanduser("~")
    if sys.platform == "darwin":
        return [os.path.join(home, "Library/Application Support/Google/Chrome"),
                os.path.join(home, "Library/Caches/Google/Chrome")]
    return [os.path.join(home, ".config/google-chrome"),
            os.path.join(home, ".cache/google-chrome"),
            os.path.join(home, ".config/chromium"),
            os.path.join(home, ".cache/chromium")]


def _seatbelt_profile(out_root: str) -> str:
    home = os.path.expanduser("~")
    cache = os.environ.get("XDG_CACHE_HOME",
                           os.path.join(home, ".cache"))
    chrome = _chrome_dirs()
    return PROFILE.format(
        tmpdir=tempfile.gettempdir(),
        out_root=os.path.abspath(out_root),
        cache_dir=os.path.abspath(cache),
        chrome_support=chrome[0],
        chrome_caches=chrome[1] if len(chrome) > 1 else chrome[0],
    )


def _bwrap_cmd(out_root: str) -> list:
    """bubblewrap command line: everything read-only, writable islands."""
    cmd = [
        "bwrap",
        "--ro-bind", "/", "/",
        "--dev-bind", "/dev", "/dev",
        "--proc", "/proc",
        "--tmpfs", "/tmp",
        "--bind", "/run", "/run",
        "--bind", os.path.abspath(out_root), os.path.abspath(out_root),
        "--die-with-parent",
        "--",
    ]
    home = os.path.expanduser("~")
    cache = os.path.abspath(os.environ.get(
        "XDG_CACHE_HOME", os.path.join(home, ".cache")))
    # the temp dir python uses (TMPDIR may point outside /tmp)
    tmpdir = tempfile.gettempdir()
    for path in [cache, tmpdir] + _chrome_dirs():
        if os.path.isdir(path):
            cmd[-1:-1] = ["--bind", path, path]
    return cmd


def _backend() -> str | None:
    if sys.platform == "darwin" and os.path.exists(SANDBOX_EXEC):
        return "seatbelt"
    if sys.platform.startswith("linux") and shutil.which("bwrap"):
        return "bwrap"
    return None


def available() -> bool:
    return _backend() is not None


def maybe_reexec(out_root: str) -> None:
    """Re-exec the current process inside the sandbox.

    No-op when already sandboxed, when no backend exists (Windows, or
    linux without bwrap), or on error — the run proceeds unsandboxed
    rather than failing.
    """
    if in_sandbox() or not available():
        return
    backend = _backend()
    env = dict(os.environ, **{MARKER: "1"})
    try:
        if backend == "seatbelt":
            profile = _seatbelt_profile(out_root)
            fd, path = tempfile.mkstemp(suffix=".sb", prefix="wlb-")
            with os.fdopen(fd, "w") as f:
                f.write(profile)
            os.execve(SANDBOX_EXEC,
                      [SANDBOX_EXEC, "-f", path, sys.executable]
                      + list(sys.argv), env)
        else:   # bwrap
            os.makedirs(out_root, exist_ok=True)
            cmd = _bwrap_cmd(out_root) + [sys.executable] + list(sys.argv)
            os.execvpe(cmd[0], cmd, env)
        # exec never returns
    except OSError:
        pass    # run unsandboxed
