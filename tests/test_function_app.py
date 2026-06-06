"""
Tests for function_app.py HTTP handlers.

Covers all 5 route handlers using unittest.mock.patch on requests.get/put/delete.
Pure unit tests with GitHub API mocks — no external services required.
"""
from unittest.mock import patch, MagicMock, call
import base64
import json as _json
import os

import pytest
import azure.functions as func

import function_app
from schema import build_post, serialize_post


def encode_content(content_str: str) -> str:
    """Encode a string as base64 the way GitHub API returns it (no embedded newlines)."""
    return base64.b64encode(content_str.encode("utf-8")).decode("ascii")


def _make_auth_header(email: str = "test@example.com") -> dict:
    """Build an X-MS-CLIENT-PRINCIPAL header for testing Easy Auth."""
    claims = [
        {"typ": "preferred_username", "val": email},
        {"typ": "http://schemas.microsoft.com/identity/claims/objectidentifier", "val": "test-oid-123"},
        {"typ": "name", "val": "Test User"},
    ]
    principal = {"claims": claims}
    encoded = base64.b64encode(_json.dumps(principal).encode()).decode()
    return {"X-MS-CLIENT-PRINCIPAL": encoded}


_ENV = {"GITHUB_TOKEN": "fake-token", "GITHUB_REPO": "owner/repo"}


# ---------------------------------------------------------------------------
# GET /api/posts/:slug (get_post)
# ---------------------------------------------------------------------------

def test_get_post_success():
    """GET /api/posts/test returns 200 with post data when GitHub returns 200."""
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
            url="/api/posts/test",
            params={},
            route_params={"slug": "test"},
        )
        resp = function_app.get_post(req)

    assert resp.status_code == 200
    body = _json.loads(resp.get_body())
    assert "slug" in body
    assert body["slug"] == "test"


def test_get_post_not_found():
    """GET /api/posts/missing returns 404 when GitHub returns 404."""
    mock_resp = MagicMock()
    mock_resp.status_code = 404

    with patch.dict(os.environ, _ENV), patch("requests.get", return_value=mock_resp):
        req = func.HttpRequest(
            method="GET",
            body=b"",
            url="/api/posts/missing",
            params={},
            route_params={"slug": "missing"},
        )
        resp = function_app.get_post(req)

    assert resp.status_code == 404
    body = _json.loads(resp.get_body())
    assert body == {"error": "not found"}


def test_get_post_invalid_slug():
    """GET /api/posts with invalid slug returns 400 — no requests call needed."""
    req = func.HttpRequest(
        method="GET",
        body=b"",
        url="/api/posts/../../etc/passwd",
        params={},
        route_params={"slug": "../../etc/passwd"},
    )
    resp = function_app.get_post(req)

    assert resp.status_code == 400
    body = _json.loads(resp.get_body())
    assert body == {"error": "invalid slug"}


# ---------------------------------------------------------------------------
# GET /api/posts (list_posts)
# ---------------------------------------------------------------------------

def test_list_posts_empty():
    """GET /api/posts with empty directory returns 200 and empty posts array."""
    mock_dir_resp = MagicMock()
    mock_dir_resp.status_code = 200
    mock_dir_resp.json.return_value = []

    with patch.dict(os.environ, _ENV), patch("requests.get", return_value=mock_dir_resp):
        req = func.HttpRequest(method="GET", body=b"", url="/api/posts", params={})
        resp = function_app.list_posts(req)

    assert resp.status_code == 200
    body = _json.loads(resp.get_body())
    assert body == {"posts": []}


def test_list_posts_returns_published_only():
    """GET /api/posts returns only published posts — directory has one published post."""
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
        req = func.HttpRequest(method="GET", body=b"", url="/api/posts", params={})
        resp = function_app.list_posts(req)

    assert resp.status_code == 200
    body = _json.loads(resp.get_body())
    assert len(body["posts"]) == 1
    assert body["posts"][0]["slug"] == "published-post"


def test_list_posts_excludes_drafts():
    """GET /api/posts returns empty array when directory has only a draft post."""
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
        req = func.HttpRequest(method="GET", body=b"", url="/api/posts", params={})
        resp = function_app.list_posts(req)

    assert resp.status_code == 200
    body = _json.loads(resp.get_body())
    assert body == {"posts": []}


# ---------------------------------------------------------------------------
# POST /api/posts (create_post)
# ---------------------------------------------------------------------------

def test_create_post_success():
    """POST /api/posts with valid auth and body returns 201 with slug."""
    # generate_slug hits GET (404 = free slug); create_file hits PUT (201 = created)
    get_resp = MagicMock()
    get_resp.status_code = 404
    put_resp = MagicMock()
    put_resp.status_code = 201

    with patch.dict(os.environ, _ENV), \
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
            url="/api/posts",
            params={},
            headers=_make_auth_header(),
        )
        resp = function_app.create_post(req)

    assert resp.status_code == 201
    body = _json.loads(resp.get_body())
    assert "slug" in body


def test_create_post_requires_auth():
    """POST /api/posts without X-MS-CLIENT-PRINCIPAL returns 401 — auth gate fires before any requests call."""
    req = func.HttpRequest(
        method="POST",
        body=_json.dumps({"title": "Test", "description": "Desc"}).encode(),
        url="/api/posts",
        params={},
        headers={},
    )
    resp = function_app.create_post(req)

    assert resp.status_code == 401


def test_create_post_missing_title():
    """POST /api/posts with auth but no title returns 400."""
    req = func.HttpRequest(
        method="POST",
        body=_json.dumps({"description": "Desc only"}).encode(),
        url="/api/posts",
        params={},
        headers=_make_auth_header(),
    )
    resp = function_app.create_post(req)

    assert resp.status_code == 400
    body = _json.loads(resp.get_body())
    assert "title" in body.get("error", "").lower() or "description" in body.get("error", "").lower()


# ---------------------------------------------------------------------------
# PUT /api/posts/:slug (update_post)
# ---------------------------------------------------------------------------

def test_update_post_success():
    """PUT /api/posts/test-slug with valid auth and body returns 200 with all required fields."""
    post = build_post(
        title="Original",
        slug="test-slug",
        date="2026-01-15T00:00:00+00:00",
        description="desc",
        body="body",
        published=False,
    )
    raw = serialize_post(post)

    get_resp = MagicMock()
    get_resp.status_code = 200
    get_resp.json.return_value = {"sha": "abc123", "content": encode_content(raw)}
    put_resp = MagicMock()
    put_resp.status_code = 200

    with patch.dict(os.environ, _ENV), \
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
            url="/api/posts/test-slug",
            params={},
            headers=_make_auth_header(),
            route_params={"slug": "test-slug"},
        )
        resp = function_app.update_post(req)

    assert resp.status_code == 200
    body = _json.loads(resp.get_body())
    for field in ("title", "slug", "date", "description", "updatedAt", "published"):
        assert field in body, f"Missing field: {field}"


def test_update_post_not_found():
    """PUT /api/posts/missing with auth but missing file returns 404."""
    get_resp = MagicMock()
    get_resp.status_code = 404

    with patch.dict(os.environ, _ENV), patch("requests.get", return_value=get_resp):
        req = func.HttpRequest(
            method="PUT",
            body=_json.dumps({"title": "Updated", "description": "Updated desc"}).encode(),
            url="/api/posts/missing",
            params={},
            headers=_make_auth_header(),
            route_params={"slug": "missing"},
        )
        resp = function_app.update_post(req)

    assert resp.status_code == 404


def test_update_post_requires_auth():
    """PUT /api/posts/test-slug without X-MS-CLIENT-PRINCIPAL returns 401."""
    req = func.HttpRequest(
        method="PUT",
        body=_json.dumps({"title": "Updated", "description": "Desc"}).encode(),
        url="/api/posts/test-slug",
        params={},
        headers={},
        route_params={"slug": "test-slug"},
    )
    resp = function_app.update_post(req)

    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# DELETE /api/posts/:slug (delete_post)
# ---------------------------------------------------------------------------

def test_delete_post_success():
    """DELETE /api/posts/test-slug with auth returns 204 with empty body."""
    get_resp = MagicMock()
    get_resp.status_code = 200
    get_resp.json.return_value = {"sha": "abc123", "content": encode_content("---\nslug: test-slug\n---\n")}
    del_resp = MagicMock()
    del_resp.status_code = 200

    with patch.dict(os.environ, _ENV), \
         patch("requests.get", return_value=get_resp), \
         patch("requests.delete", return_value=del_resp):
        req = func.HttpRequest(
            method="DELETE",
            body=b"",
            url="/api/posts/test-slug",
            params={},
            headers=_make_auth_header(),
            route_params={"slug": "test-slug"},
        )
        resp = function_app.delete_post(req)

    assert resp.status_code == 204
    assert resp.get_body() == b""


def test_delete_post_not_found():
    """DELETE /api/posts/missing with auth but missing file returns 404."""
    get_resp = MagicMock()
    get_resp.status_code = 404

    with patch.dict(os.environ, _ENV), patch("requests.get", return_value=get_resp):
        req = func.HttpRequest(
            method="DELETE",
            body=b"",
            url="/api/posts/missing",
            params={},
            headers=_make_auth_header(),
            route_params={"slug": "missing"},
        )
        resp = function_app.delete_post(req)

    assert resp.status_code == 404


def test_delete_post_requires_auth():
    """DELETE /api/posts/test-slug without X-MS-CLIENT-PRINCIPAL returns 401."""
    req = func.HttpRequest(
        method="DELETE",
        body=b"",
        url="/api/posts/test-slug",
        params={},
        headers={},
        route_params={"slug": "test-slug"},
    )
    resp = function_app.delete_post(req)

    assert resp.status_code == 401
