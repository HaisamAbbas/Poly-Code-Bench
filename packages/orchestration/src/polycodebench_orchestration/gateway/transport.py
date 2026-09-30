"""HTTP transport with per-connection address validation (SSRF and DNS-rebinding safe).

The hostname is resolved on every connection, every answer is checked against the endpoint's
network policy, and the socket is opened to the validated address while TLS still verifies
the registered hostname. Redirects are never followed and response size is bounded.
"""

from __future__ import annotations

import asyncio
import contextlib
import http.client
import socket
import ssl
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

from polycodebench_core.endpoint_policy import RegisteredEndpoint, check_resolved_addresses

MAX_RESPONSE_BYTES = 16 * 1024 * 1024

Phase = Literal["resolve", "connect", "tls", "build", "send", "receive"]
Resolver = Callable[[str, int], Sequence[str]]


@dataclass(frozen=True)
class HttpResponse:
    status: int
    headers: dict[str, str]  # lower-cased names
    body: bytes


class TransportError(Exception):
    """A transport failure. ``phase`` tells whether request bytes may have reached the peer."""

    def __init__(
        self, phase: Phase, code: str, *, timed_out: bool = False, retryable: bool = True
    ) -> None:
        super().__init__(f"{phase}:{code}")
        self.phase = phase
        self.code = code
        self.timed_out = timed_out
        self.retryable = retryable

    @property
    def request_may_have_been_sent(self) -> bool:
        return self.phase in {"send", "receive"}


class HttpTransport(Protocol):
    async def send(
        self,
        endpoint: RegisteredEndpoint,
        path: str,
        headers: dict[str, str],
        body: bytes,
        *,
        timeout_seconds: float,
    ) -> HttpResponse: ...


def system_resolver(host: str, port: int) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError):
        raise TransportError("resolve", "dns_failed") from None
    seen: dict[str, None] = {}
    for info in infos:
        seen[str(info[4][0])] = None
    return list(seen)


class PinnedHttpTransport:
    def __init__(
        self,
        *,
        resolver: Resolver = system_resolver,
        tls_context: ssl.SSLContext | None = None,
        max_response_bytes: int = MAX_RESPONSE_BYTES,
    ) -> None:
        self._resolver = resolver
        self._tls = tls_context or ssl.create_default_context()
        self._max_response_bytes = max_response_bytes

    async def send(
        self,
        endpoint: RegisteredEndpoint,
        path: str,
        headers: dict[str, str],
        body: bytes,
        *,
        timeout_seconds: float,
    ) -> HttpResponse:
        return await asyncio.to_thread(
            self._send_blocking, endpoint, path, headers, body, timeout_seconds
        )

    def _send_blocking(
        self,
        endpoint: RegisteredEndpoint,
        path: str,
        headers: dict[str, str],
        body: bytes,
        timeout: float,
    ) -> HttpResponse:
        target = endpoint.endpoint
        # Policy violations propagate as EndpointPolicyViolation: nothing was contacted.
        validated = check_resolved_addresses(
            self._resolver(target.host, target.port), endpoint.policy
        )
        sock = self._connect(str(validated[0]), target.port, timeout)
        # A duplicate descriptor of the TCP socket lets a watchdog shut the connection down at the
        # overall deadline, even after http.client or TLS has taken over the original object.
        guard = sock.dup()
        expired = threading.Event()

        def expire() -> None:
            expired.set()
            with contextlib.suppress(OSError):
                guard.shutdown(socket.SHUT_RDWR)

        watchdog = threading.Timer(timeout, expire)
        watchdog.daemon = True
        watchdog.start()
        try:
            if target.scheme == "https":
                try:
                    sock = self._tls.wrap_socket(sock, server_hostname=target.host)
                except (ssl.SSLError, OSError, TimeoutError) as error:
                    sock.close()
                    raise TransportError(
                        "tls",
                        "tls_failed",
                        timed_out=expired.is_set() or isinstance(error, TimeoutError),
                    ) from None
            connection = _PreConnected(target.host, target.port, sock, timeout)
            try:
                connection.request("POST", target.base_path + path, body=body, headers=headers)
            except (ValueError, http.client.InvalidURL):
                # http.client validates the request line and headers before any byte is sent.
                raise TransportError("build", "invalid_request", retryable=False) from None
            except (OSError, http.client.HTTPException) as error:
                raise TransportError(
                    "send",
                    "send_failed",
                    timed_out=expired.is_set() or isinstance(error, TimeoutError),
                ) from None
            try:
                response = connection.getresponse()
                data = self._read_bounded(response)
            except (OSError, http.client.HTTPException) as error:
                raise TransportError(
                    "receive",
                    "deadline_exceeded" if expired.is_set() else "receive_failed",
                    timed_out=expired.is_set() or isinstance(error, TimeoutError),
                ) from None
            return HttpResponse(
                status=response.status,
                headers={name.lower(): value for name, value in response.getheaders()},
                body=data,
            )
        finally:
            watchdog.cancel()
            guard.close()
            sock.close()

    def _read_bounded(self, response: http.client.HTTPResponse) -> bytes:
        """Read the body in chunks under the size cap; the watchdog enforces the deadline."""
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = response.read(65536)
            if not chunk:
                return b"".join(chunks)
            total += len(chunk)
            if total > self._max_response_bytes:
                # The provider did process the request; do not pay for another attempt.
                raise TransportError("receive", "response_too_large", retryable=False)
            chunks.append(chunk)

    def _connect(self, address: str, port: int, timeout: float) -> socket.socket:
        try:
            return socket.create_connection((address, port), timeout=timeout)
        except TimeoutError:
            raise TransportError("connect", "connect_timeout", timed_out=True) from None
        except OSError:
            raise TransportError("connect", "connect_failed") from None


class _PreConnected(http.client.HTTPConnection):
    """HTTP/1.1 connection over an already-validated socket; closes after one exchange."""

    def __init__(self, host: str, port: int, sock: socket.socket, timeout: float) -> None:
        super().__init__(host, port, timeout=timeout)
        self.sock = sock
        sock.settimeout(timeout)

    def connect(self) -> None:  # the socket is supplied; never dial by hostname
        return None
