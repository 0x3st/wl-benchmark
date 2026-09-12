"""Child-side network: everything goes through the parent tunnel.

socket.create_connection is patched to speak HTTP CONNECT over the
Unix-socket tunnel. This covers urllib/http.client (the chat client
and the publisher) transparently; DNS is resolved by the parent — the
sandboxed child performs no host lookups. TLS is end-to-end.
"""
from __future__ import annotations

import os
import socket


class _TunnelSocket(socket.socket):
    """AF_UNIX socket that ignores TCP-level socket options.

    http.client sets TCP_NODELAY after connecting (unguarded in newer
    CPython); on a Unix socket that raises EOPNOTSUPP. Everything else
    behaves like a normal stream socket.
    """

    def setsockopt(self, level, optname, value):
        if level == socket.IPPROTO_TCP:
            return
        return super().setsockopt(level, optname, value)


def install(sock_path: str) -> None:
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
        timeout = kwargs.get("timeout") or (args[0] if args else None)
        s = _TunnelSocket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            s.settimeout(30)
            s.connect(sock_path)
            s.settimeout(timeout)
            s.sendall(f"CONNECT {host}:{port} HTTP/1.0\r\n"
                      f"Host: {host}:{port}\r\n\r\n".encode())
            buf = b""
            while b"\r\n\r\n" not in buf:
                chunk = s.recv(4096)
                if not chunk:
                    raise OSError(f"tunnel closed for {host}:{port}")
                buf += chunk
            status = buf.split(b"\r\n", 1)[0].decode(errors="replace")
            if " 200 " not in status:
                s.close()
                raise OSError(f"tunnel refused {host}:{port} ({status})")
            return s
        except Exception:
            try:
                s.close()
            except OSError:
                pass
            raise

    socket.create_connection = tunneled
