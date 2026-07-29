import sys
from pathlib import Path

import pytest
from fastapi import HTTPException


API_DIR = Path(__file__).resolve().parents[1] / "api" / "mock-fastapi"
sys.path.insert(0, str(API_DIR))

import app as app_module  # noqa: E402


def test_alive_payload():
    assert app_module.alive() == {
        "ok": True,
        "service": "iot-sim-ops",
        "version": "0.3.1",
    }


def test_expected_routes_are_registered():
    routes = {
        (method, route.path)
        for route in app_module.app.routes
        for method in getattr(route, "methods", set())
    }
    assert ("GET", "/alive") in routes
    assert ("POST", "/auth/login") in routes
    assert ("GET", "/sims/search") in routes
    assert ("POST", "/sims/{iccid}/purchase") in routes
    assert ("PATCH", "/sims/{iccid}/status") in routes


def test_cors_origin_parser_trims_and_drops_empty_values():
    assert app_module.parse_cors_origins(
        "https://one.example, ,https://two.example"
    ) == ["https://one.example", "https://two.example"]
    assert app_module.parse_cors_origins("") == ["*"]


@pytest.mark.parametrize("value", ["true", "1", "YES", "on"])
def test_boolean_parser_accepts_true_values(value):
    assert app_module.parse_bool(value) is True


@pytest.mark.parametrize("value", ["false", "0", "NO", "off"])
def test_boolean_parser_accepts_false_values(value):
    assert app_module.parse_bool(value) is False


def test_boolean_parser_rejects_unknown_value():
    with pytest.raises(ValueError):
        app_module.parse_bool("sometimes")


def test_missing_bearer_token_is_rejected_without_database_access():
    with pytest.raises(HTTPException) as exc_info:
        app_module.require_auth_user(None)
    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "missing bearer token"
