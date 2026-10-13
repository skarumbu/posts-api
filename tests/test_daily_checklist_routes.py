"""
Tests for function_app.py's daily/checklist/* route handlers. Mocks
daily.checklist directly, same spirit as test_function_app.py mocking
requests.get/post for the sections routes: these exercise auth/allowlist
gating, request parsing, and response shaping, not the real Table Storage
I/O (covered in tests/test_daily_checklist.py).
"""
import json as _json
from unittest.mock import patch

import azure.functions as func

import function_app
from daily.checklist import ChecklistTask


def _auth_patch(email: str = "test@example.com"):
    return patch("function_app.require_auth", return_value=("google-sub-123", email))


def _req(method, url, route_params=None, params=None, body=b""):
    return func.HttpRequest(
        method=method, body=body, url=url,
        params=params or {}, route_params=route_params or {},
    )


def test_list_checklist_requires_auth():
    resp = function_app.list_checklist_tasks(_req("GET", "/api/daily/checklist"))
    assert resp.status_code == 401


def test_list_checklist_respects_allowlist():
    with patch.dict("os.environ", {"ALLOWED_WRITERS": "other@example.com"}), _auth_patch():
        resp = function_app.list_checklist_tasks(_req("GET", "/api/daily/checklist"))
    assert resp.status_code == 403


def test_list_checklist_tasks_returns_items():
    task = ChecklistTask(id="t1", text="Stretch", done_dates=["2026-10-10"], expires_after=None)
    with _auth_patch(), patch("daily.checklist.list_tasks", return_value=[task]):
        resp = function_app.list_checklist_tasks(_req("GET", "/api/daily/checklist"))
    assert resp.status_code == 200
    assert _json.loads(resp.get_body())["items"] == [
        {"id": "t1", "text": "Stretch", "done_dates": ["2026-10-10"], "expires_after": None}
    ]


def test_create_checklist_task_requires_text():
    with _auth_patch():
        resp = function_app.create_checklist_task(
            _req("POST", "/api/daily/checklist", body=_json.dumps({}).encode())
        )
    assert resp.status_code == 400


def test_create_checklist_task_success():
    created = ChecklistTask(id="t1", text="Stretch", done_dates=[], expires_after=None)
    with _auth_patch(), patch("daily.checklist.create_task", return_value=created) as mock_create:
        resp = function_app.create_checklist_task(
            _req("POST", "/api/daily/checklist", body=_json.dumps({"text": "Stretch"}).encode())
        )
    assert resp.status_code == 201
    mock_create.assert_called_once_with("test@example.com", text="Stretch", expires_after=None)


def test_toggle_checklist_task_invalid_date():
    with _auth_patch():
        resp = function_app.toggle_checklist_task(
            _req("POST", "/api/daily/checklist/t1/toggle",
                 route_params={"task_id": "t1"}, params={"date": "not-a-date"})
        )
    assert resp.status_code == 400


def test_toggle_checklist_task_not_found():
    with _auth_patch(), patch("daily.checklist.toggle_done", side_effect=ValueError("missing")):
        resp = function_app.toggle_checklist_task(
            _req("POST", "/api/daily/checklist/missing/toggle",
                 route_params={"task_id": "missing"}, params={"date": "2026-10-12"})
        )
    assert resp.status_code == 404


def test_toggle_checklist_task_success():
    updated = ChecklistTask(id="t1", text="Stretch", done_dates=["2026-10-12"], expires_after=None)
    with _auth_patch(), patch("daily.checklist.toggle_done", return_value=updated):
        resp = function_app.toggle_checklist_task(
            _req("POST", "/api/daily/checklist/t1/toggle",
                 route_params={"task_id": "t1"}, params={"date": "2026-10-12"})
        )
    assert resp.status_code == 200
    assert _json.loads(resp.get_body())["done_dates"] == ["2026-10-12"]
