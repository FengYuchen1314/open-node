from __future__ import annotations

import importlib.util
from ipaddress import ip_address
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "validate-camouflage-pools.py"
SPEC = importlib.util.spec_from_file_location("camouflage_validator", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
validator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(validator)


def test_resolve_keeps_public_system_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        validator,
        "_system_addresses",
        lambda _host: [ip_address("8.8.8.8")],
    )
    monkeypatch.setattr(
        validator,
        "_doh_addresses",
        lambda *_args: pytest.fail("DoH must not run for public system DNS"),
    )

    assert validator.resolve(object(), "example.com", 3) == [ip_address("8.8.8.8")]


def test_resolve_uses_doh_for_fake_ip_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        validator,
        "_system_addresses",
        lambda _host: [ip_address("198.18.0.17")],
    )
    monkeypatch.setattr(
        validator,
        "_doh_addresses",
        lambda *_args: [ip_address("8.8.8.8"), ip_address("2001:4860:4860::8888")],
    )

    assert validator.resolve(object(), "example.com", 3) == [
        ip_address("8.8.8.8"),
        ip_address("2001:4860:4860::8888"),
    ]


def test_resolve_uses_doh_when_system_dns_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    def failed_system_dns(_host: str):
        raise OSError("resolver unavailable")

    monkeypatch.setattr(validator, "_system_addresses", failed_system_dns)
    monkeypatch.setattr(
        validator,
        "_doh_addresses",
        lambda *_args: [ip_address("8.8.8.8")],
    )

    assert validator.resolve(object(), "example.com", 3) == [ip_address("8.8.8.8")]


def test_resolve_rejects_non_public_doh_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(validator, "_system_addresses", lambda _host: [])
    monkeypatch.setattr(
        validator,
        "_doh_addresses",
        lambda *_args: [ip_address("192.0.2.1")],
    )

    with pytest.raises(ValueError, match="did not return only public"):
        validator.resolve(object(), "example.com", 3)


def test_doh_parser_ignores_cname_and_collects_address_records(monkeypatch) -> None:
    answers = iter(
        [
            '{"Status":0,"Answer":[{"type":5,"data":"alias.example"},{"type":1,"data":"8.8.4.4"}]}',
            '{"Status":0,"Answer":[{"type":28,"data":"2001:4860:4860::8844"}]}',
        ]
    )
    monkeypatch.setattr(validator, "request_text", lambda *_args: next(answers))

    assert validator._doh_addresses(object(), "example.com", 3) == [
        ip_address("8.8.4.4"),
        ip_address("2001:4860:4860::8844"),
    ]
