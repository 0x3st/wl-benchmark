"""Parent-side network tunnel for the sandboxed child.

The sandbox denies ALL network access; the only egress is this Unix
socket proxy. The child speaks an HTTP CONNECT protocol over the
socket; the parent resolves the whitelist (the user-chosen endpoint
and the results platform), relays raw TCP, and refuses everything
else. TLS is end-to-end between the child and the real server — the
tunnel never sees plaintext.

Threat model: model output must not be able to reach the network
except through the two destinations the operator chose.
"""
from __future__ import annotations

import os
import socket
import tempfile
import threading


def _host_of(url: str) -> str:
    from urllib.parse import urlparse
    host = urlparse(url).hostname or ""
    return host.lower().rstrip(".")


def allowed_hosts(provider_base_url: str, platform_url: str | None) -> set:
    hosts = {_host_of(provider_base_url)}
    if platform_url:
        hosts.add(_host_of(platform_url))
    return {h for h in hosts if h}


class Tunnel:
    def __init__(self, allowed: set):
        fd, self.path = tempfile.mkstemp(prefix="wlb-tunnel-",
                                         suffix=".sock")
        os.close(fd)
        os.unlink(self.path)          # bind wants a free path
        self.allowed = {h.lower() for h in allowed if h}
        self.srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.srv.bind(self.path)
        self.srv.listen(16)
        self.srv.settimeout(0.5)
        self._alive = True
        threading.Thread(target=self._accept_loop, daemon=True).start()

    # ------------------------------------------------------------ server
    def _accept_loop(self) -> None:
        while self._alive:
            try:
                conn, _ = self.srv.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(target=self._handle, args=(conn,),
                             daemon=True).start()

    def _handle(self, conn: socket.socket) -> None:
        try:
            conn.settimeout(30)
            buf = b""
            while b"\r\n\r\n" not in buf:
                chunk = conn.recv(4096)
                if not chunk:
                    return
                buf += chunk
                if len(buf) > 8192:
                    return
            line = buf.split(b"\r\n")[0].decode(errors="replace")
            parts = line.split()
            if len(parts) < 2 or parts[0].upper() != "CONNECT":
                conn.sendall(b"HTTP/1.1 405 Method Not Allowed\r\n\r\n")
                return
            host, _, port = parts[1].partition(":")
            if host.lower() not in self.allowed:
                conn.sendall(b"HTTP/1.1 403 Forbidden\r\n\r\n")
                return
            try:
                remote = socket.create_connection(
                    (host, int(port or "443")), timeout=20)
            except OSError:
                conn.sendall(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
                return
            conn.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            conn.settimeout(None)
            _relay(conn, remote)
        except OSError:
            pass
        finally:
            try:
                conn.close()
            except OSError:
                pass

    def close(self) -> None:
        self._alive = False
        try:
            self.srv.close()
        except OSError:
            pass
        try:
            os.unlink(self.path)
        except OSError:
            pass


def _relay(a: socket.socket, b: socket.socket) -> None:
    def pump(src, dst):
        try:
            while True:
                data = src.recv(65536)
                if not data:
                    break
                dst.sendall(data)
        except OSError:
            pass
        finally:
            try:
                dst.shutdown(socket.SHUT_WR)
            except OSError:
                pass

    t = threading.Thread(target=pump, args=(a, b), daemon=True)
    t.start()
    pump(b, a)
    t.join(timeout=30)
