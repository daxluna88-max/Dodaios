"""Pairing codes and device credentials."""

from __future__ import annotations

from pathlib import Path

import pytest

from dodaios.phone.devices import (
    CODE_TTL_S,
    MAX_DEVICES,
    MAX_FAILURES,
    DeviceRegistry,
    PairingError,
)


def pair(registry: DeviceRegistry, name: str = "iPhone di Dax") -> tuple[dict, str]:
    code, _ttl = registry.new_code()
    return registry.redeem(code, name)


def test_a_code_works_once_and_gives_a_credential(tmp_path: Path) -> None:
    registry = DeviceRegistry(tmp_path)
    code, ttl = registry.new_code()
    assert len(code) == 9 and code[4] == "-" and ttl == CODE_TTL_S
    device, token = registry.redeem(code.lower().replace("-", " "), "iPhone di Dax")
    assert device["name"] == "iPhone di Dax" and registry.authenticate(token)["id"] == device["id"]
    with pytest.raises(PairingError):
        registry.redeem(code, "again")  # one use


def test_expired_wrong_and_replaced_codes_fail(tmp_path: Path) -> None:
    registry = DeviceRegistry(tmp_path)
    code, _ = registry.new_code(now=1000.0)
    with pytest.raises(PairingError):
        registry.redeem(code, "x", now=1000.0 + CODE_TTL_S + 1)
    first, _ = registry.new_code()
    second, _ = registry.new_code()
    with pytest.raises(PairingError):
        registry.redeem(first, "x")  # a newer code replaces the older one
    registry.redeem(second, "x")


def test_wrong_codes_are_rate_limited_even_for_the_right_code(tmp_path: Path) -> None:
    registry = DeviceRegistry(tmp_path)
    code, _ = registry.new_code()
    for _ in range(MAX_FAILURES):
        with pytest.raises(PairingError):
            registry.redeem("AAAA-AAAA", "x")
    with pytest.raises(PairingError, match="too many"):
        registry.redeem(code, "x")


def test_revocation_is_immediate_and_the_credential_is_never_stored(tmp_path: Path) -> None:
    registry = DeviceRegistry(tmp_path)
    device, token = pair(registry)
    assert registry.is_active(device["id"])
    assert registry.revoke(device["id"]) and not registry.revoke(device["id"])
    assert registry.authenticate(token) is None and not registry.is_active(device["id"])
    assert token.encode() not in (tmp_path / "devices.sqlite3").read_bytes()
    assert "csrf" not in registry.list()[0]


def test_garbage_credentials_are_refused(tmp_path: Path) -> None:
    registry = DeviceRegistry(tmp_path)
    assert registry.authenticate(None) is None
    assert registry.authenticate("") is None
    assert registry.authenticate("x" * 500) is None
    assert registry.authenticate("' OR 1=1 --") is None


def test_the_device_count_is_capped(tmp_path: Path) -> None:
    registry = DeviceRegistry(tmp_path)
    for index in range(MAX_DEVICES):
        pair(registry, f"phone {index}")
    with pytest.raises(PairingError, match="too many paired"):
        registry.new_code()
