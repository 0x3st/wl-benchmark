"""OS-level sandbox for the benchmark run, network included.

Architecture: the CLI (parent) prompts for everything, then spawns the
benchmark as a child process under a deny-by-default sandbox:

  - file writes confined to the run dir, /tmp, cache and the browser's
    own support dirs
  - ALL network denied — including Unix-socket connects (a broad
    unix-socket allow would expose local services like docker.sock).
    The child's only egress is a control socketpair inherited via
    pass_fds; each connection request gets back an already-connected
    TCP socket as a file descriptor (SCM_RIGHTS), whitelisted to the
    exact (host, port) of the user-chosen endpoint. TLS stays
    end-to-end — the parent passes the socket, it never relays data.

Backends:
  darwin  Seatbelt via /usr/bin/sandbox-exec (fds survive execve)
  linux   bubblewrap (bwrap, probed before use) with --unshare-net and
          --pass-fd
  windows not supported (no practical unprivileged sandbox)

When no backend is available the run proceeds unsandboxed.
"""
from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile

from .tunnel import endpoint_target, Tunnel

MARKER = "WL_BENCH_SANDBOX"
PAYLOAD = "WL_BENCH_CHILD"
SOCK_ENV = "WL_BENCH_TUNNEL_SOCK"
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
    """bubblewrap: read-only root, writable islands, no network at all
    (--unshare-net). The control socket enters as stdin (fd 0) — bwrap
    passes the standard fds through and older versions lack
    --pass-fd. Read-only binds also make every other Unix socket on
    the filesystem unconnectable (connect needs write access)."""
    cmd = [
        "bwrap",
        "--ro-bind", "/", "/",
        "--dev-bind", "/dev", "/dev",
        "--proc", "/proc",
        "--tmpfs", "/tmp",
        # hide every system service socket (docker.sock, dbus, ...) —
        # read-only binds do NOT prevent AF_UNIX connects, but the
        # sockets cannot be reached if they are not there
        "--tmpfs", "/run",
        "--unshare-net",
        "--bind", os.path.abspath(out_root), os.path.abspath(out_root),
        "--die-with-parent",
        "--",
    ]
    home = os.path.expanduser("~")
    cache = os.path.abspath(os.environ.get(
        "XDG_CACHE_HOME", os.path.join(home, ".cache")))
    tmpdir = tempfile.gettempdir()
    for path in [cache, tmpdir] + _chrome_dirs():
        if path == "/tmp" or not os.path.isdir(path):
            continue
        cmd[-1:-1] = ["--bind", path, path]
    return cmd


def _bwrap_ok() -> bool:
    """bwrap can exist yet be unusable — e.g. Ubuntu 24.04 restricts
    unprivileged user namespaces via AppArmor. Probe it with the
    network namespace we actually use."""
    try:
        p = subprocess.run(
            ["bwrap", "--ro-bind", "/", "/", "--dev-bind", "/dev", "/dev",
             "--proc", "/proc", "--unshare-net", "--", "/bin/true"],
            capture_output=True, timeout=20)
        return p.returncode == 0
    except Exception:  # noqa: BLE001
        return False


def _backend() -> str | None:
    if sys.platform == "darwin" and os.path.exists(SANDBOX_EXEC):
        return "seatbelt"
    if sys.platform.startswith("linux") and shutil.which("bwrap") \
            and _bwrap_ok():
        return "bwrap"
    return None


def available() -> bool:
    return _backend() is not None


def spawn_sandboxed_entry(entry: list, out_root: str, allowed: set,
                          env_extra: dict | None = None) -> int:
    """Run `entry` under the sandbox; returns its exit code.

    allowed: set of (host, port) targets the child may connect to.
    The control socketpair fd is passed to the child; its number is in
    env WL_BENCH_TUNNEL_SOCK.
    """
    backend = _backend()
    out_root = os.path.abspath(out_root)
    os.makedirs(out_root, exist_ok=True)
    tunnel = Tunnel(allowed)

    stdin_arg = None
    child_env = dict(os.environ, **(env_extra or {}), **{
        MARKER: "1",
        SOCK_ENV: str(tunnel.child_fd),
    })
    try:
        if backend == "seatbelt":
            profile = _seatbelt_profile(out_root)
            fd, prof_path = tempfile.mkstemp(suffix=".sb", prefix="wlb-")
            with os.fdopen(fd, "w") as f:
                f.write(profile)
            argv = [SANDBOX_EXEC, "-f", prof_path] + entry
            pass_fds = [tunnel.child_fd]
        else:
            # bwrap: the control socket rides in as stdin (fd 0)
            argv = _bwrap_cmd(out_root) + entry
            child_env[SOCK_ENV] = "0"
            stdin_arg = os.dup(tunnel.child_fd)
            pass_fds = []

        # the terminal delivers Ctrl+C to the whole foreground group;
        # the child handles it (exit 130) — the parent must not die first
        prev_int = signal.signal(signal.SIGINT, signal.SIG_IGN)
        try:
            proc = subprocess.Popen(argv, env=child_env, stdin=stdin_arg,
                                    pass_fds=pass_fds)
            rc = proc.wait()
        finally:
            signal.signal(signal.SIGINT, prev_int)
            if stdin_arg is not None:
                os.close(stdin_arg)
        return rc if rc >= 0 else 128 - rc
    finally:
        tunnel.close()


def spawn_sandboxed(payload: dict, out_root: str) -> int:
    """Run the benchmark child under the sandbox; returns its exit code.

    payload: {provider, run_cfg, only_types, out, keep, done_file} —
    the child runs the tasks and writes the run dir to done_file.
    Network is endpoint-only (exact host+port).
    """
    payload = dict(payload)
    payload.setdefault("done_file", None)
    allowed = {endpoint_target(payload["provider"]["base_url"])}
    return spawn_sandboxed_entry(
        [sys.executable, "-u", "-m", "wl_benchmark.child"],
        out_root, allowed,
        env_extra={PAYLOAD: json.dumps(payload)})
