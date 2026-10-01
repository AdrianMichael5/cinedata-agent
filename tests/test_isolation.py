"""The test suite itself must never reach the network."""

import socket

import pytest


def test_outbound_socket_connections_are_blocked():
    # 203.0.113.0/24 is TEST-NET-3 (documentation only); the guard must refuse before any packet.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client:
        client.settimeout(0.5)
        with pytest.raises(RuntimeError, match="Network access is disabled"):
            client.connect(("203.0.113.1", 9))


def test_loopback_connections_still_work():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        with socket.create_connection(server.getsockname(), timeout=2):
            pass
