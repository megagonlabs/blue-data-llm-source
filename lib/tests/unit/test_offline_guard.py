"""The guard that keeps this suite offline is itself under test."""

from __future__ import annotations

import socket

import pytest

from tests.doubles import NetworkAccessError


@pytest.mark.expects_offline_violation
def test_socket_connect_raises_network_access_error() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        with pytest.raises(NetworkAccessError, match="offline"):
            sock.connect(("127.0.0.1", 9))


@pytest.mark.expects_offline_violation
def test_socket_connect_ex_raises_network_access_error() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        with pytest.raises(NetworkAccessError):
            sock.connect_ex(("127.0.0.1", 9))


@pytest.mark.expects_offline_violation
def test_create_connection_raises_network_access_error() -> None:
    with pytest.raises(NetworkAccessError):
        socket.create_connection(("127.0.0.1", 9), timeout=0.01)


@pytest.mark.expects_offline_violation
def test_guard_names_the_refused_address() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        with pytest.raises(NetworkAccessError, match="example.invalid"):
            sock.connect(("example.invalid", 443))
