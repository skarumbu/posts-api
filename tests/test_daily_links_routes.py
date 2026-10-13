"""
Tests for function_app.py's daily/links/* route handlers. Mocks daily.links
directly; the real Table Storage I/O is covered in tests/test_daily_links.py.
"""
import json as _json
from unittest.mock import patch

import azure.functions as func

import function_app
from daily.links import LinkDef


def _auth_patch(email: str = "test@example.com"):
    return patch("function_app.require_auth", return_value=("google-sub-123", email))


def _req(method, url, route_params=None, params=None, body=b""):
    return func.HttpRequest(
        method=method, body=body, url=url,
        params=params or {}, route_params=route_params or {},
    )


def test_get_links_config_requires_auth():
    resp = function_app.get_links_config(_req("GET", "/api/daily/links/config"))
    assert resp.status_code == 401


def test_get_links_config_returns_links():
    links_list = [LinkDef(id="wordle", label="Wordle", url="https://www.nytimes.com/games/wordle")]
    with _auth_patch(), patch("daily.links.get_config", return_value=links_list):
        resp = function_app.get_links_config(_req("GET", "/api/daily/links/config"))
    assert resp.status_code == 200
    assert _json.loads(resp.get_body())["links"][0]["id"] == "wordle"


def test_set_links_config_rejects_malformed_link():
    with _auth_patch():
        resp = function_app.set_links_config(
            _req("PUT", "/api/daily/links/config", body=_json.dumps({"links": [{"id": "x"}]}).encode())
        )
    assert resp.status_code == 400


def test_set_links_config_success():
    body = {"links": [{"id": "wordle", "label": "Wordle", "url": "https://www.nytimes.com/games/wordle"}]}
    with _auth_patch(), patch("daily.links.set_config") as mock_set:
        resp = function_app.set_links_config(
            _req("PUT", "/api/daily/links/config", body=_json.dumps(body).encode())
        )
    assert resp.status_code == 200
    mock_set.assert_called_once()


def test_get_links_opened_invalid_date():
    with _auth_patch():
        resp = function_app.get_links_opened(
            _req("GET", "/api/daily/links/opened", params={"date": "bogus"})
        )
    assert resp.status_code == 400


def test_mark_link_opened_success():
    with _auth_patch(), patch("daily.links.mark_opened") as mock_mark:
        resp = function_app.mark_link_opened(
            _req("POST", "/api/daily/links/wordle/opened",
                 route_params={"link_id": "wordle"}, params={"date": "2026-10-12"})
        )
    assert resp.status_code == 200
    mock_mark.assert_called_once()
