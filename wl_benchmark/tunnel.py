"""Parent-side network tunnel for the sandboxed child (fd passing).

The sandbox denies ALL network access — including Unix-socket
connects (a broad unix-socket allow would expose local services such
as docker.sock). The child instead inherits one end of a socketpair
via pass_fds; each connection request travels over that control
channel and the parent passes back an already-connected TCP socket as
a file descriptor (SCM_RIGHTS). The child never creates a socket,
never resolves DNS, and talks TLS end-to-end with the real server.

Whitelist: exact (host, port) pairs derived from the user-chosen
endpoint.
"""
from __future__ import annotations

import array
import os
import socket
import threading

from urllib.parse import urlparse


def endpoint_target(base_url: str) -> tuple:
    u = urlparse(base_url)
    host = (u.hostname or "").lower().rstrip(".")
    port = u.port or (443 if u.scheme == "https" else 80)
    return host, port


class Tunnel:
    def __init__(self, allowed: set):
        """allowed: set of (host, port) tuples (exact matches only).
        child_fd is a raw int (for pass_fds); the parent serves its end
        on parent_fd."""
        self.allowed = {(h.lower().rstrip("."), int(p))
                        for h, p in allowed if h}
        a, b = socket.socketpair()
        self.parent_fd = a.detach()      # raw int; served by the thread
        self.child_fd = b.detach()       # raw int, passed to the child
        self._alive = True
        threading.Thread(target=self._serve, daemon=True).start()

    # ------------------------------------------------------------ server
    def _serve(self) -> None:
        # wrap the raw fd; the object keeps ownership until close()
        self._ctrl = socket.socket(fileno=self.parent_fd)
        ctrl = self._ctrl
        try:
            buf = b""
            while self._alive:
                chunk = ctrl.recv(4096)
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    self._handle(ctrl, line.decode(errors="replace"))
        except OSError:
            pass

    def _handle(self, ctrl, line: str) -> None:
        parts = line.split()
        if len(parts) != 2 or parts[0] != "CONNECT":
            self._reply_err(ctrl, "bad request")
            return
        host, _, port = parts[1].rpartition(":")
        try:
            target = (host.lower().rstrip("."), int(port or "443"))
        except ValueError:
            self._reply_err(ctrl, "bad port")
            return
        if target not in self.allowed:
            self._reply_err(ctrl, f"refused {host}:{port}")
            return
        try:
            remote = socket.create_connection(target, timeout=20)
        except OSError as e:
            self._reply_err(ctrl, f"unreachable {host}:{port} ({e})")
            return
        # create_connection leaves O_NONBLOCK set for the timeout mode;
        # the child would inherit it and see EAGAIN on every read
        remote.setblocking(True)
        # pass the connected socket to the child; the parent closes its
        # copy — data flows directly between child and server
        fds = array.array("i", [remote.fileno()])
        try:
            ctrl.sendmsg([b"OK\n"], [(socket.SOL_SOCKET,
                                      socket.SCM_RIGHTS, fds)])
        finally:
            remote.close()

    @staticmethod
    def _reply_err(ctrl, msg: str) -> None:
        try:
            ctrl.sendall(f"ERR {msg}\n".encode()[:200])
        except OSError:
            pass

    def close(self) -> None:
        self._alive = False
        try:
            self._ctrl.close()
        except OSError:
            pass
        try:
            os.close(self.child_fd)
        except OSError:
            pass
