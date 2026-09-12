"""Child-side network: connections arrive as file descriptors.

socket.create_connection is patched to ask the parent (over the
inherited control socketpair) for an already-connected TCP socket.
Covers urllib/http.client transparently — the chat client and the
publisher. No sockets are created in the child, no DNS lookups
happen, TLS is end-to-end with the real server.
"""
from __future__ import annotations

import array
import os
import socket
import threading

_lock = threading.Lock()


def install(ctrl_fd: int) -> None:
    # the tunnel IS the egress — system/HTTP proxies must not intercept
    # (urllib reads macOS system proxies and would route everything to
    # a possibly-dead local proxy instead of the tunnel)
    import urllib.request
    urllib.request.getproxies = lambda: {}
    os.environ["no_proxy"] = "*"
    os.environ.pop("http_proxy", None)
    os.environ.pop("https_proxy", None)
    os.environ.pop("all_proxy", None)

    orig = socket.create_connection

    def tunneled(address, *args, **kwargs):
        host, port = address[0], address[1]
        with _lock:
            ctrl = socket.socket(fileno=ctrl_fd)
            try:
                ctrl.sendall(f"CONNECT {host}:{port}\n".encode())
                # recvmsg discards ancillary data unless a buffer is
                # given — SCM_RIGHTS fds arrive as ancdata
                data, ancdata, _, _ = ctrl.recvmsg(
                    64, socket.CMSG_SPACE(4))
            finally:
                # detach without closing the inherited fd — the socket
                # object is a per-call view; the fd stays open for the
                # next request
                ctrl.detach()
        if data.startswith(b"ERR"):
            raise OSError(f"tunnel: {data.decode(errors='replace').strip()}")
        for level, type_, fds in ancdata:
            if level == socket.SOL_SOCKET and type_ == socket.SCM_RIGHTS:
                return socket.socket(fileno=fds[0])
        raise OSError(f"tunnel: no fd in reply ({data!r})")

    socket.create_connection = tunneled
    _keepalive = orig      # keep a reference for inspection/debugging
