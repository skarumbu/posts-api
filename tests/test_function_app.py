"""
Tests for function_app.py HTTP handlers.

Covers all section-generic route handlers using unittest.mock.patch on
requests.get/put/delete, exercised through the "writing" section (the only
section registered so far). Pure unit tests with GitHub API mocks — no
external services required.
"""
from unittest.mock import patch, MagicMock
import base64
import json as _json
import os

import pytest
import azure.functions as func

import function_app
from schema_writing import build_post, serialize_post


def encode_content(content_str: str) -> str:
    """Encode a string as base64 the way GitHub API returns it (no embedded newlines)."""
    return base64.b64encode(content_str.encode("utf-8")).decode("ascii")


def _auth_patch(email: str = "test@example.com"):
    """Patch require_auth to return the given Google identity without touching real JWT logic."""
    return patch("function_app.require_auth", return_value=("google-sub-123", email))


_ENV = {"GITHUB_TOKEN": "fake-token", "GITHUB_REPO": "owner/repo"}


# ---------------------------------------------------------------------------
# Section resolution
# ---------------------------------------------------------------------------

def test_unknown_section_returns_404():
    """GET /api/sections/bogus/items returns 404 before any auth or storage access."""
    req = func.HttpRequest(
        method="GET",
        body=b"",
        url="/api/sections/bogus/items",
        params={},
        route_params={"section": "bogus"},
    )
    resp = function_app.list_items(req)

    assert resp.status_code == 404
    body = _json.loads(resp.get_body())
    assert body == {"error": "unknown section"}


# ---------------------------------------------------------------------------
# GET /api/sections/writing/items/:slug (get_item)
# ---------------------------------------------------------------------------

def test_get_item_success():
    """GET /api/sections/writing/items/test returns 200 with item data when GitHub returns 200."""
    post = build_post(
        title="Test Post",
        slug="test",
        date="2026-01-01T00:00:00+00:00",
        description="A test",
        body="Test body",
        published=True,
    )
    raw = serialize_post(post)
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"sha": "abc123", "content": encode_content(raw)}

    with patch.dict(os.environ, _ENV), patch("requests.get", return_value=mock_resp):
        req = func.HttpRequest(
            method="GET",
            body=b"",
            url="/api/sections/writing/items/test",
            params={},
            route_params={"section": "writing", "slug": "test"},
        )
        resp = function_app.get_item(req)

    assert resp.status_code == 200
    body = _json.loads(resp.get_body())
    assert "slug" in body
    assert body["slug"] == "test"


def test_get_item_not_found():
    """GET /api/sections/writing/items/missing returns 404 when GitHub returns 404."""
    mock_resp = MagicMock()
    mock_resp.status_code = 404

    with patch.dict(os.environ, _ENV), patch("requests.get", return_value=mock_resp):
        req = func.HttpRequest(
            method="GET",
            body=b"",
            url="/api/sections/writing/items/missing",
            params={},
            route_params={"section": "writing", "slug": "missing"},
        )
        resp = function_app.get_item(req)

    assert resp.status_code == 404
    body = _json.loads(resp.get_body())
    assert body == {"error": "not found"}


def test_get_item_invalid_slug():
    """GET with invalid slug returns 400 — no requests call needed."""
    req = func.HttpRequest(
        method="GET",
        body=b"",
        url="/api/sections/writing/items/../../etc/passwd",
        params={},
        route_params={"section": "writing", "slug": "../../etc/passwd"},
    )
    resp = function_app.get_item(req)

    assert resp.status_code == 400
    body = _json.loads(resp.get_body())
    assert body == {"error": "invalid slug"}


# ---------------------------------------------------------------------------
# GET /api/sections/writing/items (list_items)
# ---------------------------------------------------------------------------

def test_list_items_empty():
    """GET /api/sections/writing/items with empty directory returns 200 and empty items array."""
    mock_dir_resp = MagicMock()
    mock_dir_resp.status_code = 200
    mock_dir_resp.json.return_value = []

    with patch.dict(os.environ, _ENV), patch("requests.get", return_value=mock_dir_resp):
        req = func.HttpRequest(
            method="GET", body=b"", url="/api/sections/writing/items", params={},
            route_params={"section": "writing"},
        )
        resp = function_app.list_items(req)

    assert resp.status_code == 200
    body = _json.loads(resp.get_body())
    assert body == {"items": []}


def test_list_items_returns_published_only():
    """GET /api/sections/writing/items returns only published items — directory has one published post."""
    post = build_post(
        title="Published Post",
        slug="published-post",
        date="2026-05-30T00:00:00+00:00",
        description="A published post",
        body="Hello world",
        published=True,
    )
    raw = serialize_post(post)

    # First GET: directory listing; Second GET: file content
    dir_resp = MagicMock()
    dir_resp.status_code = 200
    dir_resp.json.return_value = [
        {"name": "published-post.md", "url": "https://api.github.com/repos/owner/repo/contents/posts/published-post.md"},
    ]
    file_resp = MagicMock()
    file_resp.status_code = 200
    file_resp.json.return_value = {"sha": "abc123", "content": encode_content(raw)}

    with patch.dict(os.environ, _ENV), patch("requests.get", side_effect=[dir_resp, file_resp]):
        req = func.HttpRequest(
            method="GET", body=b"", url="/api/sections/writing/items", params={},
            route_params={"section": "writing"},
        )
        resp = function_app.list_items(req)

    assert resp.status_code == 200
    body = _json.loads(resp.get_body())
    assert len(body["items"]) == 1
    assert body["items"][0]["slug"] == "published-post"


def test_list_items_excludes_drafts():
    """GET /api/sections/writing/items returns empty array when directory has only a draft post."""
    post = build_post(
        title="Draft Post",
        slug="draft-post",
        date="2026-05-30T00:00:00+00:00",
        description="A draft",
        body="Draft body",
        published=False,
    )
    raw = serialize_post(post)

    dir_resp = MagicMock()
    dir_resp.status_code = 200
    dir_resp.json.return_value = [
        {"name": "draft-post.md", "url": "https://api.github.com/repos/owner/repo/contents/posts/draft-post.md"},
    ]
    file_resp = MagicMock()
    file_resp.status_code = 200
    file_resp.json.return_value = {"sha": "abc123", "content": encode_content(raw)}

    with patch.dict(os.environ, _ENV), patch("requests.get", side_effect=[dir_resp, file_resp]):
        req = func.HttpRequest(
            method="GET", body=b"", url="/api/sections/writing/items", params={},
            route_params={"section": "writing"},
        )
        resp = function_app.list_items(req)

    assert resp.status_code == 200
    body = _json.loads(resp.get_body())
    assert body == {"items": []}


# ---------------------------------------------------------------------------
# POST /api/sections/writing/items (create_item)
# ---------------------------------------------------------------------------

def test_create_item_success():
    """POST /api/sections/writing/items with valid Google auth returns 201 with slug."""
    get_resp = MagicMock()
    get_resp.status_code = 404
    put_resp = MagicMock()
    put_resp.status_code = 201

    with patch.dict(os.environ, _ENV), \
         _auth_patch(), \
         patch("requests.get", return_value=get_resp), \
         patch("requests.put", return_value=put_resp):
        req = func.HttpRequest(
            method="POST",
            body=_json.dumps({
                "title": "My Test Post",
                "description": "A description",
                "body": "Content here",
                "published": False,
            }).encode(),
            url="/api/sections/writing/items",
            params={},
            headers={},
            route_params={"section": "writing"},
        )
        resp = function_app.create_item(req)

    assert resp.status_code == 201
    body = _json.loads(resp.get_body())
    assert "slug" in body


def test_create_item_unknown_section():
    """POST to an unknown section returns 404 before any auth check."""
    req = func.HttpRequest(
        method="POST",
        body=_json.dumps({"title": "Test"}).encode(),
        url="/api/sections/bogus/items",
        params={},
        headers={},
        route_params={"section": "bogus"},
    )
    resp = function_app.create_item(req)

    assert resp.status_code == 404


def test_create_item_requires_auth():
    """POST /api/sections/writing/items without Authorization header returns 401."""
    req = func.HttpRequest(
        method="POST",
        body=_json.dumps({"title": "Test", "description": "Desc"}).encode(),
        url="/api/sections/writing/items",
        params={},
        headers={},
        route_params={"section": "writing"},
    )
    resp = function_app.create_item(req)

    assert resp.status_code == 401


def test_create_item_forbidden_not_in_allowlist():
    """POST with ALLOWED_WRITERS set and requester not in list returns 403."""
    with patch.dict(os.environ, {**_ENV, "ALLOWED_WRITERS": "owner@example.com"}), \
         _auth_patch(email="other@example.com"):
        req = func.HttpRequest(
            method="POST",
            body=_json.dumps({"title": "Test", "description": "Desc"}).encode(),
            url="/api/sections/writing/items",
            params={},
            headers={},
            route_params={"section": "writing"},
        )
        resp = function_app.create_item(req)

    assert resp.status_code == 403


def test_create_item_missing_title():
    """POST with auth but no title returns 400."""
    with _auth_patch():
        req = func.HttpRequest(
            method="POST",
            body=_json.dumps({"description": "Desc only"}).encode(),
            url="/api/sections/writing/items",
            params={},
            headers={},
            route_params={"section": "writing"},
        )
        resp = function_app.create_item(req)

    assert resp.status_code == 400
    body = _json.loads(resp.get_body())
    assert "title" in body.get("error", "").lower() or "description" in body.get("error", "").lower()


def test_create_item_stores_author_email():
    """POST /api/sections/writing/items stores author_email in the serialized frontmatter."""
    get_resp = MagicMock()
    get_resp.status_code = 404
    put_resp = MagicMock()
    put_resp.status_code = 201

    captured_body = {}

    def capture_put2(url, **kwargs):
        import base64 as _b64
        data = kwargs.get("json") or _json.loads(kwargs.get("data", b"{}"))
        content_b64 = data.get("content", "")
        if content_b64:
            captured_body["content"] = _b64.b64decode(content_b64).decode()
        return put_resp

    with patch.dict(os.environ, _ENV), \
         _auth_patch(email="owner@example.com"), \
         patch("requests.get", return_value=get_resp), \
         patch("requests.put", side_effect=capture_put2):
        req = func.HttpRequest(
            method="POST",
            body=_json.dumps({
                "title": "Authored Post",
                "description": "A post with an author",
                "body": "Body text",
                "published": False,
            }).encode(),
            url="/api/sections/writing/items",
            params={},
            headers={},
            route_params={"section": "writing"},
        )
        function_app.create_item(req)

    assert "author_email" in captured_body.get("content", "")
    assert "owner@example.com" in captured_body.get("content", "")


# ---------------------------------------------------------------------------
# PUT /api/sections/writing/items/:slug (update_item)
# ---------------------------------------------------------------------------

def test_update_item_success():
    """PUT with valid auth returns 200 with all required fields."""
    post = build_post(
        title="Original",
        slug="test-slug",
        date="2026-01-15T00:00:00+00:00",
        description="desc",
        body="body",
        published=False,
        author_email="test@example.com",
    )
    raw = serialize_post(post)

    get_resp = MagicMock()
    get_resp.status_code = 200
    get_resp.json.return_value = {"sha": "abc123", "content": encode_content(raw)}
    put_resp = MagicMock()
    put_resp.status_code = 200

    with patch.dict(os.environ, _ENV), \
         _auth_patch(), \
         patch("requests.get", return_value=get_resp), \
         patch("requests.put", return_value=put_resp):
        req = func.HttpRequest(
            method="PUT",
            body=_json.dumps({
                "title": "Updated Title",
                "description": "Updated desc",
                "body": "Updated body",
                "published": True,
            }).encode(),
            url="/api/sections/writing/items/test-slug",
            params={},
            headers={},
            route_params={"section": "writing", "slug": "test-slug"},
        )
        resp = function_app.update_item(req)

    assert resp.status_code == 200
    body = _json.loads(resp.get_body())
    for field in ("title", "slug", "date", "description", "updatedAt", "published"):
        assert field in body, f"Missing field: {field}"


def test_update_item_ownership_mismatch():
    """PUT returns 403 when requester is not the author."""
    post = build_post(
        title="Original",
        slug="test-slug",
        date="2026-01-15T00:00:00+00:00",
        description="desc",
        body="body",
        published=False,
        author_email="alice@example.com",
    )
    raw = serialize_post(post)

    get_resp = MagicMock()
    get_resp.status_code = 200
    get_resp.json.return_value = {"sha": "abc123", "content": encode_content(raw)}

    with patch.dict(os.environ, _ENV), \
         _auth_patch(email="bob@example.com"), \
         patch("requests.get", return_value=get_resp):
        req = func.HttpRequest(
            method="PUT",
            body=_json.dumps({"title": "Hacked", "description": "Stolen"}).encode(),
            url="/api/sections/writing/items/test-slug",
            params={},
            headers={},
            route_params={"section": "writing", "slug": "test-slug"},
        )
        resp = function_app.update_item(req)

    assert resp.status_code == 403


def test_update_item_legacy_no_author_allowed_writer():
    """PUT on a legacy item (no author_email) succeeds for an ALLOWED_WRITERS member."""
    post = build_post(
        title="Legacy",
        slug="legacy-post",
        date="2026-01-01T00:00:00+00:00",
        description="old post",
        body="old body",
        published=True,
    )
    raw = serialize_post(post)

    get_resp = MagicMock()
    get_resp.status_code = 200
    get_resp.json.return_value = {"sha": "sha1", "content": encode_content(raw)}
    put_resp = MagicMock()
    put_resp.status_code = 200

    with patch.dict(os.environ, {**_ENV, "ALLOWED_WRITERS": "owner@example.com"}), \
         _auth_patch(email="owner@example.com"), \
         patch("requests.get", return_value=get_resp), \
         patch("requests.put", return_value=put_resp):
        req = func.HttpRequest(
            method="PUT",
            body=_json.dumps({"title": "Updated", "description": "Updated desc"}).encode(),
            url="/api/sections/writing/items/legacy-post",
            params={},
            headers={},
            route_params={"section": "writing", "slug": "legacy-post"},
        )
        resp = function_app.update_item(req)

    assert resp.status_code == 200


def test_update_item_not_found():
    """PUT with auth but missing item returns 404."""
    get_resp = MagicMock()
    get_resp.status_code = 404

    with patch.dict(os.environ, _ENV), \
         _auth_patch(), \
         patch("requests.get", return_value=get_resp):
        req = func.HttpRequest(
            method="PUT",
            body=_json.dumps({"title": "Updated", "description": "Updated desc"}).encode(),
            url="/api/sections/writing/items/missing",
            params={},
            headers={},
            route_params={"section": "writing", "slug": "missing"},
        )
        resp = function_app.update_item(req)

    assert resp.status_code == 404


def test_update_item_requires_auth():
    """PUT without Authorization header returns 401."""
    req = func.HttpRequest(
        method="PUT",
        body=_json.dumps({"title": "Updated", "description": "Desc"}).encode(),
        url="/api/sections/writing/items/test-slug",
        params={},
        headers={},
        route_params={"section": "writing", "slug": "test-slug"},
    )
    resp = function_app.update_item(req)

    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# DELETE /api/sections/writing/items/:slug (delete_item)
# ---------------------------------------------------------------------------

def test_delete_item_success():
    """DELETE with auth returns 204 with empty body."""
    post = build_post(
        title="To Delete",
        slug="test-slug",
        date="2026-01-01T00:00:00+00:00",
        description="A post to delete",
        body="body",
        published=True,
        author_email="test@example.com",
    )
    raw = serialize_post(post)

    get_resp = MagicMock()
    get_resp.status_code = 200
    get_resp.json.return_value = {"sha": "abc123", "content": encode_content(raw)}
    del_resp = MagicMock()
    del_resp.status_code = 200

    with patch.dict(os.environ, _ENV), \
         _auth_patch(), \
         patch("requests.get", return_value=get_resp), \
         patch("requests.delete", return_value=del_resp):
        req = func.HttpRequest(
            method="DELETE",
            body=b"",
            url="/api/sections/writing/items/test-slug",
            params={},
            headers={},
            route_params={"section": "writing", "slug": "test-slug"},
        )
        resp = function_app.delete_item(req)

    assert resp.status_code == 204
    assert resp.get_body() == b""


def test_delete_item_ownership_mismatch():
    """DELETE returns 403 when requester is not the author."""
    post = build_post(
        title="Alice's Post",
        slug="test-slug",
        date="2026-01-01T00:00:00+00:00",
        description="Alice wrote this",
        body="body",
        published=True,
        author_email="alice@example.com",
    )
    raw = serialize_post(post)

    get_resp = MagicMock()
    get_resp.status_code = 200
    get_resp.json.return_value = {"sha": "abc123", "content": encode_content(raw)}

    with patch.dict(os.environ, _ENV), \
         _auth_patch(email="bob@example.com"), \
         patch("requests.get", return_value=get_resp):
        req = func.HttpRequest(
            method="DELETE",
            body=b"",
            url="/api/sections/writing/items/test-slug",
            params={},
            headers={},
            route_params={"section": "writing", "slug": "test-slug"},
        )
        resp = function_app.delete_item(req)

    assert resp.status_code == 403


def test_delete_item_not_found():
    """DELETE with auth but missing item returns 404."""
    get_resp = MagicMock()
    get_resp.status_code = 404

    with patch.dict(os.environ, _ENV), \
         _auth_patch(), \
         patch("requests.get", return_value=get_resp):
        req = func.HttpRequest(
            method="DELETE",
            body=b"",
            url="/api/sections/writing/items/missing",
            params={},
            headers={},
            route_params={"section": "writing", "slug": "missing"},
        )
        resp = function_app.delete_item(req)

    assert resp.status_code == 404


def test_delete_item_requires_auth():
    """DELETE without Authorization header returns 401."""
    req = func.HttpRequest(
        method="DELETE",
        body=b"",
        url="/api/sections/writing/items/test-slug",
        params={},
        headers={},
        route_params={"section": "writing", "slug": "test-slug"},
    )
    resp = function_app.delete_item(req)

    assert resp.status_code == 401
