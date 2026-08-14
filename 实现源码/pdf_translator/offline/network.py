from __future__ import annotations

import contextlib
import socket
from collections.abc import Iterator
from urllib.parse import urlparse


class RemoteNetworkDisabled(RuntimeError):
    """Raised when an offline run attempts a non-loopback connection."""


def _is_loopback(host: object) -> bool:
    value = str(host).strip("[]").casefold()
    return value in {"localhost", "127.0.0.1", "::1"} or value.startswith(
        "127."
    )


def _allowed_host_values(hosts: set[str]) -> set[str]:
    values = {value.strip("[]").casefold() for value in hosts if value}
    resolved = set(values)
    for value in values:
        try:
            resolved.update(
                item[4][0].strip("[]").casefold()
                for item in socket.getaddrinfo(value, None)
            )
        except socket.gaierror:
            pass
    return resolved


@contextlib.contextmanager
def allow_only_remote_hosts(
    hosts: set[str], attempts: list[dict] | None = None
) -> Iterator[None]:
    """Allow loopback and an explicit remote-host allowlist only."""

    attempts = attempts if attempts is not None else []
    allowed = _allowed_host_values(hosts)
    original_connect = socket.socket.connect
    original_create_connection = socket.create_connection

    def permitted(host: object) -> bool:
        value = str(host).strip("[]").casefold()
        return _is_loopback(value) or value in allowed

    def guarded_connect(sock: socket.socket, address):
        host = address[0] if isinstance(address, tuple) and address else address
        if not permitted(host):
            attempts.append({"operation": "socket.connect", "address": repr(address)})
            raise RemoteNetworkDisabled(f"网络白名单禁止远程连接：{address!r}")
        return original_connect(sock, address)

    def guarded_create_connection(address, *args, **kwargs):
        host = address[0] if isinstance(address, tuple) and address else address
        if not permitted(host):
            attempts.append(
                {"operation": "socket.create_connection", "address": repr(address)}
            )
            raise RemoteNetworkDisabled(f"网络白名单禁止远程连接：{address!r}")
        return original_create_connection(address, *args, **kwargs)

    socket.socket.connect = guarded_connect
    socket.create_connection = guarded_create_connection
    try:
        yield
    finally:
        socket.socket.connect = original_connect
        socket.create_connection = original_create_connection


def host_from_url(url: str) -> str:
    host = urlparse(url).hostname
    if not host:
        raise ValueError(f"无效服务地址：{url!r}")
    return host


@contextlib.contextmanager
def deny_remote_network(attempts: list[dict] | None = None) -> Iterator[None]:
    """Block outbound sockets for the duration of an offline PDF run.

    CTranslate2 and ONNX Runtime do not require sockets.  This guard catches
    accidental future additions of an API translator or model downloader.
    Loopback remains available for ordinary local tooling.
    """

    with allow_only_remote_hosts(set(), attempts):
        yield
