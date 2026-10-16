"""
Tests for function_app.py's daily/cards/* route handlers. Mocks daily.cards
directly; the real Table Storage I/O is covered in tests/test_daily_cards.py.
"""
import json as _json
from unittest.mock import MagicMock, patch

import azure.functions as func
from azure.core.exceptions import ResourceNotFoundError

import function_app
from daily.cards import IndexCard


def _auth_patch(email: str = "test@example.com"):
    return patch("function_app.require_auth", return_value=("google-sub-123", email))


def _req(method, url, route_params=None, params=None, body=b""):
    return func.HttpRequest(
        method=method, body=body, url=url,
        params=params or {}, route_params=route_params or {},
    )


def test_list_due_cards_requires_auth():
    resp = function_app.list_due_cards(_req("GET", "/api/daily/cards/due"))
    assert resp.status_code == 401


def test_list_due_cards_invalid_limit():
    with _auth_patch():
        resp = function_app.list_due_cards(
            _req("GET", "/api/daily/cards/due", params={"date": "2026-10-12", "limit": "abc"})
        )
    assert resp.status_code == 400


def test_list_due_cards_success():
    card = IndexCard(id="c1", text="capital of France", ease_factor=2.5, interval_days=6,
                      next_review_date="2026-10-12", last_reviewed_date="2026-10-06")
    with _auth_patch(), patch("daily.cards.list_due", return_value=[card]) as mock_list:
        resp = function_app.list_due_cards(
            _req("GET", "/api/daily/cards/due", params={"date": "2026-10-12"})
        )
    assert resp.status_code == 200
    assert _json.loads(resp.get_body())["items"][0]["id"] == "c1"
    mock_list.assert_called_once_with("test@example.com", "2026-10-12", limit=20)


def test_create_index_card_requires_text():
    with _auth_patch():
        resp = function_app.create_index_card(
            _req("POST", "/api/daily/cards", body=_json.dumps({}).encode())
        )
    assert resp.status_code == 400


def test_create_index_card_passes_back():
    card = IndexCard(id="c1", text="capital of France", ease_factor=2.5, interval_days=0,
                      next_review_date="2026-10-12", back="Paris")
    with _auth_patch(), patch("daily.cards.create_card", return_value=card) as mock_create:
        resp = function_app.create_index_card(
            _req("POST", "/api/daily/cards",
                 body=_json.dumps({"text": " capital of France ", "back": " Paris "}).encode())
        )
    assert resp.status_code == 201
    assert _json.loads(resp.get_body())["back"] == "Paris"
    mock_create.assert_called_once_with("test@example.com", "capital of France", back="Paris")


def test_create_index_card_defaults_back_to_empty():
    card = IndexCard(id="c1", text="capital of France", ease_factor=2.5, interval_days=0,
                      next_review_date="2026-10-12")
    for body in ({"text": "capital of France"}, {"text": "capital of France", "back": None}):
        with _auth_patch(), patch("daily.cards.create_card", return_value=card) as mock_create:
            resp = function_app.create_index_card(
                _req("POST", "/api/daily/cards", body=_json.dumps(body).encode())
            )
        assert resp.status_code == 201
        mock_create.assert_called_once_with("test@example.com", "capital of France", back="")


def test_create_index_card_rejects_non_string_back():
    for back in (42, 0, False, ["Paris"]):
        with _auth_patch(), patch("daily.cards.create_card") as mock_create:
            resp = function_app.create_index_card(
                _req("POST", "/api/daily/cards",
                     body=_json.dumps({"text": "capital of France", "back": back}).encode())
            )
        assert resp.status_code == 400, back
        mock_create.assert_not_called()


def test_delete_index_card_requires_auth():
    resp = function_app.delete_index_card(
        _req("DELETE", "/api/daily/cards/c1", route_params={"card_id": "c1"})
    )
    assert resp.status_code == 401


def test_delete_index_card_success():
    with _auth_patch(), patch("daily.cards.delete_card") as mock_delete:
        resp = function_app.delete_index_card(
            _req("DELETE", "/api/daily/cards/c1", route_params={"card_id": "c1"})
        )
    assert resp.status_code == 204
    assert resp.get_body() == b""
    mock_delete.assert_called_once_with("test@example.com", "c1")


def test_delete_index_card_missing_card_is_204():
    mock_table = MagicMock()
    mock_table.delete_entity.side_effect = ResourceNotFoundError("missing")
    with _auth_patch(), patch("daily.cards.get_table_client", return_value=mock_table):
        resp = function_app.delete_index_card(
            _req("DELETE", "/api/daily/cards/missing", route_params={"card_id": "missing"})
        )
    assert resp.status_code == 204


def test_review_index_card_rejects_bad_grade():
    with _auth_patch():
        resp = function_app.review_index_card(
            _req("POST", "/api/daily/cards/c1/review",
                 route_params={"card_id": "c1"}, body=_json.dumps({"grade": "excellent"}).encode())
        )
    assert resp.status_code == 400


def test_review_index_card_not_found():
    with _auth_patch(), patch("daily.cards.review_card", side_effect=ValueError("missing")):
        resp = function_app.review_index_card(
            _req("POST", "/api/daily/cards/missing/review",
                 route_params={"card_id": "missing"}, body=_json.dumps({"grade": "good"}).encode())
        )
    assert resp.status_code == 404


def test_review_index_card_success():
    updated = IndexCard(id="c1", text="capital of France", ease_factor=2.5, interval_days=15,
                         next_review_date="2026-10-27", last_reviewed_date="2026-10-12")
    with _auth_patch(), patch("daily.cards.review_card", return_value=updated) as mock_review:
        resp = function_app.review_index_card(
            _req("POST", "/api/daily/cards/c1/review",
                 route_params={"card_id": "c1"}, body=_json.dumps({"grade": "good"}).encode())
        )
    assert resp.status_code == 200
    mock_review.assert_called_once_with("test@example.com", "c1", "good")
