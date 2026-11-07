"""The offline guard refuses network, subprocess and database access."""

from __future__ import annotations

import os
import socket
import subprocess

import pytest

from tests.doubles import NetworkAccessError, OfflineRecord, OfflineViolation


@pytest.mark.expects_offline_violation
def test_subprocess_run_is_refused() -> None:
    with pytest.raises(OfflineViolation, match="subprocess"):
        subprocess.run(["echo", "hi"], check=False)


@pytest.mark.expects_offline_violation
def test_subprocess_popen_is_refused() -> None:
    with pytest.raises(OfflineViolation, match="subprocess"):
        subprocess.Popen(["echo", "hi"])


@pytest.mark.expects_offline_violation
def test_os_system_is_refused() -> None:
    with pytest.raises(OfflineViolation, match="subprocess"):
        os.system("echo hi")  # pyright: ignore[reportDeprecated]  # the guard must cover it


@pytest.mark.expects_offline_violation
def test_a_database_connection_is_refused() -> None:
    import sqlite3

    with pytest.raises(OfflineViolation, match="database"):
        sqlite3.connect(":memory:")


@pytest.mark.expects_offline_violation
def test_the_record_counts_each_class_separately(offline: OfflineRecord) -> None:
    with pytest.raises(NetworkAccessError):
        socket.create_connection(("example.invalid", 80))
    with pytest.raises(OfflineViolation):
        subprocess.run(["echo"], check=False)

    assert offline.network == 1
    assert offline.subprocess == 1
    assert offline.database == 0
    assert offline.total == 2


def test_a_clean_test_records_no_violations(offline: OfflineRecord) -> None:
    assert offline.as_dict() == {"network": 0, "subprocess": 0, "database": 0}
