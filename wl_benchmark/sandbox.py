"""OS-level sandbox for the benchmark run (macOS Seatbelt).

The whole `wlb` process re-execs itself under /usr/bin/sandbox-exec
with a deny-by-default profile: file writes are confined to the run
output directory, the temp dir and the cache dir; network is open (the
endpoint is user-chosen at prompt time); everything else — writing to
arbitrary user paths, modifying the installation, spawning system
utilities for side effects — is denied. Combined with the SVG content
sanitizer this contains the two places where model output is handled.

Not available on this machine / platform: silently runs without it.
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


def available() -> bool:
    return (sys.platform == "darwin" and os.path.exists(SANDBOX_EXEC)
            and shutil.which(SANDBOX_EXEC) is not None)


def in_sandbox() -> bool:
    return bool(os.environ.get(MARKER))


def _profile(out_root: str) -> str:
    home = os.path.expanduser("~")
    cache = os.environ.get("XDG_CACHE_HOME",
                           os.path.join(home, ".cache"))
    return PROFILE.format(
        tmpdir=tempfile.gettempdir(),
        out_root=os.path.abspath(out_root),
        cache_dir=os.path.abspath(cache),
        chrome_support=os.path.join(home,
            "Library/Application Support/Google/Chrome"),
        chrome_caches=os.path.join(home, "Library/Caches/Google/Chrome"),
    )


def maybe_reexec(out_root: str) -> None:
    """Re-exec the current process under the sandbox profile.

    No-op when already sandboxed, unavailable, or on error (the run
    proceeds unsandboxed rather than failing).
    """
    if in_sandbox() or not available():
        return
    try:
        profile = _profile(out_root)
        fd, path = tempfile.mkstemp(suffix=".sb", prefix="wlb-")
        with os.fdopen(fd, "w") as f:
            f.write(profile)
        env = dict(os.environ, **{MARKER: "1"})
        os.execve(SANDBOX_EXEC,
                  [SANDBOX_EXEC, "-f", path, sys.executable] + list(sys.argv),
                  env)
        # execve never returns
    except OSError:
        pass    # run unsandboxed
