"""Bounded, SSRF-guarded HTTP GET for untrusted agent URLs.

Rules: http/https only, default ports only, no userinfo, every resolved IP
must be public, the connection is pinned to the validated IP (no DNS
rebinding between check and connect), redirects are re-validated and capped,
and bodies are size-capped. Nothing fetched here is ever executed or rendered
as HTML.
"""

from __future__ import annotations

import http.client
import ipaddress
import socket
import ssl
import time
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit

from . import config

DEFAULT_PORTS = {"http": 80, "https": 443}
MAX_URL_LENGTH = 2048


class BlockedURL(Exception):
    pass


@dataclass
class FetchResult:
    outcome: str                   # OK | HTTP_ERROR | BLOCKED | UNREACHABLE | TOO_LARGE | INVALID_URL
    http_status: int | None = None
    body: bytes = b""
    final_url: str | None = None
    error: str | None = None
    redirects: list = field(default_factory=list)


def validate_url(url: str) -> tuple[str, str, int, str]:
    """Return (scheme, host, port, path_with_query) or raise BlockedURL."""
    if not isinstance(url, str) or not url or len(url) > MAX_URL_LENGTH:
        raise BlockedURL("URL_LENGTH")
    if any(ord(c) < 0x21 or ord(c) == 0x7F for c in url):
        raise BlockedURL("URL_CONTROL_OR_SPACE")
    if not url.isascii():
        raise BlockedURL("URL_NOT_ASCII")
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    if scheme not in DEFAULT_PORTS:
        raise BlockedURL("SCHEME_NOT_HTTP")
    if parts.username is not None or parts.password is not None:
        raise BlockedURL("USERINFO_NOT_ALLOWED")
    host = parts.hostname
    if not host:
        raise BlockedURL("HOST_MISSING")
    try:
        port = parts.port
    except ValueError:
        raise BlockedURL("PORT_INVALID")
    if port is not None and port != DEFAULT_PORTS[scheme]:
        raise BlockedURL("NON_DEFAULT_PORT")
    path = parts.path or "/"
    if parts.query:
        path += "?" + parts.query
    return scheme, host, DEFAULT_PORTS[scheme], path


def is_public_ip(ip: ipaddress._BaseAddress) -> bool:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return bool(ip.is_global) and not (ip.is_multicast or ip.is_reserved or ip.is_loopback
                                       or ip.is_link_local or ip.is_unspecified or ip.is_private)


def resolve_public(host: str, port: int, resolver=socket.getaddrinfo) -> str:
    try:
        infos = resolver(host, port, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError, OSError) as exc:
        raise ConnectionError(f"DNS:{type(exc).__name__}")
    ips = sorted({info[4][0] for info in infos})
    if not ips:
        raise ConnectionError("DNS_EMPTY")
    for raw in ips:
        if not is_public_ip(ipaddress.ip_address(raw.split("%")[0])):
            raise BlockedURL("NON_PUBLIC_ADDRESS")
    return ips[0]


class _PinnedHTTP(http.client.HTTPConnection):
    def __init__(self, host, ip, port, timeout):
        super().__init__(host, port, timeout=timeout)
        self._pinned_ip = ip

    def connect(self):
        self.sock = socket.create_connection((self._pinned_ip, self.port), self.timeout)


class _PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, host, ip, port, timeout):
        super().__init__(host, port, timeout=timeout, context=ssl.create_default_context())
        self._pinned_ip = ip

    def connect(self):
        sock = socket.create_connection((self._pinned_ip, self.port), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


def _default_connection(scheme, host, ip, port, timeout):
    cls = _PinnedHTTPS if scheme == "https" else _PinnedHTTP
    return cls(host, ip, port, timeout)


def fetch(url: str, *, max_bytes: int, timeout: float = config.HTTP_TIMEOUT_S,
          max_redirects: int = config.MAX_REDIRECTS, resolver=socket.getaddrinfo,
          connection_factory=_default_connection, accept: str = "application/json, */*;q=0.5") -> FetchResult:
    current = url
    redirects: list[str] = []
    deadline = time.monotonic() + timeout * (max_redirects + 1)
    for _ in range(max_redirects + 1):
        try:
            scheme, host, port, path = validate_url(current)
            ip = resolve_public(host, port, resolver)
        except BlockedURL as exc:
            malformed = str(exc) in ("URL_LENGTH", "HOST_MISSING", "PORT_INVALID", "URL_CONTROL_OR_SPACE",
                                     "URL_NOT_ASCII")
            outcome = "INVALID_URL" if malformed and not redirects else "BLOCKED"
            return FetchResult(outcome, error=str(exc), final_url=current, redirects=redirects)
        except ConnectionError as exc:
            return FetchResult("UNREACHABLE", error=str(exc), final_url=current, redirects=redirects)
        if time.monotonic() > deadline:
            return FetchResult("UNREACHABLE", error="DEADLINE", final_url=current, redirects=redirects)
        conn = connection_factory(scheme, host, ip, port, timeout)
        try:
            conn.request("GET", path, headers={"Host": host, "User-Agent": config.USER_AGENT,
                                               "Accept": accept, "Accept-Encoding": "identity"})
            resp = conn.getresponse()
            status = resp.status
            if status in (301, 302, 303, 307, 308):
                location = resp.getheader("Location")
                resp.close()
                if not location:
                    return FetchResult("HTTP_ERROR", status, final_url=current, error="REDIRECT_NO_LOCATION",
                                       redirects=redirects)
                redirects.append(current)
                current = urljoin(current, location)
                continue
            body = resp.read(max_bytes + 1)
        except (OSError, http.client.HTTPException, ssl.SSLError, ValueError) as exc:
            return FetchResult("UNREACHABLE", error=f"CONNECT:{type(exc).__name__}", final_url=current,
                               redirects=redirects)
        finally:
            conn.close()
        if len(body) > max_bytes:
            return FetchResult("TOO_LARGE", status, final_url=current, error="BODY_CAP", redirects=redirects)
        outcome = "OK" if 200 <= status < 300 else "HTTP_ERROR"
        return FetchResult(outcome, status, body, final_url=current, redirects=redirects)
    return FetchResult("BLOCKED", error="TOO_MANY_REDIRECTS", final_url=current, redirects=redirects)
