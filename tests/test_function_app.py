"""
Tests for function_app.py HTTP handlers.

Covers API-01 (GET /api/posts — list published posts) and
API-02 (GET /api/posts/{slug} — retrieve single published post).

Unit tests use monkeypatch to avoid Azurite.
Integration tests use the container_client fixture (Azurite required).
"""
import json

import pytest
import azure.functions as func
from unittest.mock import MagicMock
from azure.core.exceptions import ResourceNotFoundError

import function_app
from schema import build_post, serialize_post


# ---------------------------------------------------------------------------
# Unit tests (monkeypatch only, no Azurite)
# ---------------------------------------------------------------------------


def test_list_posts_empty(monkeypatch):
    """GET /api/posts with an empty container returns 200 and an empty posts array."""
    mock_client = MagicMock()
    mock_client.list_blobs.return_value = []
    monkeypatch.setattr("function_app.get_container_client", lambda: mock_client)

    req = func.HttpRequest(method="GET", body=b"", url="/api/posts", params={})
    resp = function_app.list_posts(req)

    assert resp.status_code == 200
    body = json.loads(resp.get_body())
    assert body == {"posts": []}


def test_get_post_not_found(monkeypatch):
    """GET /api/posts/missing-slug returns 404 when download_blob raises ResourceNotFoundError."""
    mock_client = MagicMock()
    mock_blob = MagicMock()
    mock_blob.download_blob.side_effect = ResourceNotFoundError("not found")
    mock_client.get_blob_client.return_value = mock_blob
    monkeypatch.setattr("function_app.get_container_client", lambda: mock_client)

    req = func.HttpRequest(
        method="GET",
        body=b"",
        url="/api/posts/missing-slug",
        params={},
        route_params={"slug": "missing-slug"},
    )
    resp = function_app.get_post(req)

    assert resp.status_code == 404
    body = json.loads(resp.get_body())
    assert body == {"error": "not found"}


def test_get_post_invalid_slug(monkeypatch):
    """GET /api/posts/INVALID SLUG returns 400 when slug does not match ^[a-z0-9-]+$."""
    mock_client = MagicMock()
    monkeypatch.setattr("function_app.get_container_client", lambda: mock_client)

    req = func.HttpRequest(
        method="GET",
        body=b"",
        url="/api/posts/INVALID%20SLUG",
        params={},
        route_params={"slug": "INVALID SLUG"},
    )
    resp = function_app.get_post(req)

    assert resp.status_code == 400
    body = json.loads(resp.get_body())
    assert body == {"error": "invalid slug"}


# ---------------------------------------------------------------------------
# Integration tests (container_client fixture, Azurite required)
# ---------------------------------------------------------------------------


def test_list_posts_returns_published_only(monkeypatch, container_client):
    """GET /api/posts returns only published posts when both published and draft blobs exist."""
    monkeypatch.setattr("function_app.get_container_client", lambda: container_client)

    published = build_post(
        title="Published Post",
        slug="published-post",
        date="2026-05-30T00:00:00+00:00",
        description="A published post",
        body="Hello world",
        published=True,
    )
    draft = build_post(
        title="Draft Post",
        slug="draft-post",
        date="2026-05-29T00:00:00+00:00",
        description="A draft post",
        body="Not ready",
        published=False,
    )
    container_client.upload_blob("published-post.md", serialize_post(published).encode(), overwrite=True)
    container_client.upload_blob("draft-post.md", serialize_post(draft).encode(), overwrite=True)

    req = func.HttpRequest(method="GET", body=b"", url="/api/posts", params={})
    resp = function_app.list_posts(req)

    assert resp.status_code == 200
    body = json.loads(resp.get_body())
    slugs = [p["slug"] for p in body["posts"]]
    assert "published-post" in slugs
    assert "draft-post" not in slugs


def test_list_posts_excludes_drafts(monkeypatch, container_client):
    """GET /api/posts returns an empty posts array when the container contains only draft posts."""
    monkeypatch.setattr("function_app.get_container_client", lambda: container_client)

    draft = build_post(
        title="Draft Only",
        slug="draft-only",
        date="2026-05-30T00:00:00+00:00",
        description="Just a draft",
        body="Work in progress",
        published=False,
    )
    container_client.upload_blob("draft-only.md", serialize_post(draft).encode(), overwrite=True)

    req = func.HttpRequest(method="GET", body=b"", url="/api/posts", params={})
    resp = function_app.list_posts(req)

    assert resp.status_code == 200
    body = json.loads(resp.get_body())
    assert body == {"posts": []}


def test_list_posts_sorted_by_date(monkeypatch, container_client):
    """GET /api/posts returns posts sorted newest-first by date."""
    monkeypatch.setattr("function_app.get_container_client", lambda: container_client)

    newer = build_post(
        title="Newer Post",
        slug="newer-post",
        date="2026-05-30T00:00:00+00:00",
        description="The newer post",
        body="Newer content",
        published=True,
    )
    older = build_post(
        title="Older Post",
        slug="older-post",
        date="2026-05-29T00:00:00+00:00",
        description="The older post",
        body="Older content",
        published=True,
    )
    container_client.upload_blob("newer-post.md", serialize_post(newer).encode(), overwrite=True)
    container_client.upload_blob("older-post.md", serialize_post(older).encode(), overwrite=True)

    req = func.HttpRequest(method="GET", body=b"", url="/api/posts", params={})
    resp = function_app.list_posts(req)

    assert resp.status_code == 200
    body = json.loads(resp.get_body())
    assert len(body["posts"]) == 2
    assert body["posts"][0]["slug"] == "newer-post"


def test_get_post_published(monkeypatch, container_client):
    """GET /api/posts/{slug} returns 200 with all six fields for a published post."""
    monkeypatch.setattr("function_app.get_container_client", lambda: container_client)

    post = build_post(
        title="My Published Post",
        slug="my-published-post",
        date="2026-05-30T00:00:00+00:00",
        description="A great post",
        body="Post body content here",
        published=True,
        updated_at="2026-05-30T12:00:00+00:00",
    )
    container_client.upload_blob("my-published-post.md", serialize_post(post).encode(), overwrite=True)

    req = func.HttpRequest(
        method="GET",
        body=b"",
        url="/api/posts/my-published-post",
        params={},
        route_params={"slug": "my-published-post"},
    )
    resp = function_app.get_post(req)

    assert resp.status_code == 200
    body = json.loads(resp.get_body())
    for field in ("title", "slug", "date", "description", "updatedAt", "body"):
        assert field in body, f"Missing field: {field}"


def test_get_post_draft_returns_404(monkeypatch, container_client):
    """GET /api/posts/{slug} returns 404 when the post exists but is not published."""
    monkeypatch.setattr("function_app.get_container_client", lambda: container_client)

    draft = build_post(
        title="Secret Draft",
        slug="secret-draft",
        date="2026-05-30T00:00:00+00:00",
        description="Not ready",
        body="Draft content",
        published=False,
    )
    container_client.upload_blob("secret-draft.md", serialize_post(draft).encode(), overwrite=True)

    req = func.HttpRequest(
        method="GET",
        body=b"",
        url="/api/posts/secret-draft",
        params={},
        route_params={"slug": "secret-draft"},
    )
    resp = function_app.get_post(req)

    assert resp.status_code == 404
    body = json.loads(resp.get_body())
    assert body == {"error": "not found"}


# ---------------------------------------------------------------------------
# Write handler tests (Phase 4: API-03, API-04, API-05, SEC-02)
# ---------------------------------------------------------------------------

import base64
import json as _json


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


def test_create_post_requires_auth(monkeypatch):
    """POST /api/posts without X-MS-CLIENT-PRINCIPAL returns 401."""
    mock_client = MagicMock()
    monkeypatch.setattr("function_app.get_container_client", lambda: mock_client)

    req = func.HttpRequest(
        method="POST",
        body=_json.dumps({"title": "Test", "description": "Desc"}).encode(),
        url="/api/posts",
        params={},
        headers={},
    )
    resp = function_app.create_post(req)

    assert resp.status_code == 401
    assert mock_client.get_blob_client.called is False


def test_create_post_missing_title(monkeypatch):
    """POST /api/posts with auth but no title returns 400."""
    mock_client = MagicMock()
    monkeypatch.setattr("function_app.get_container_client", lambda: mock_client)

    req = func.HttpRequest(
        method="POST",
        body=_json.dumps({"description": "Desc"}).encode(),
        url="/api/posts",
        params={},
        headers=_make_auth_header(),
    )
    resp = function_app.create_post(req)

    assert resp.status_code == 400
    body = _json.loads(resp.get_body())
    assert "title" in body.get("error", "").lower() or "description" in body.get("error", "").lower()


def test_create_post_missing_description(monkeypatch):
    """POST /api/posts with auth but no description returns 400."""
    mock_client = MagicMock()
    monkeypatch.setattr("function_app.get_container_client", lambda: mock_client)

    req = func.HttpRequest(
        method="POST",
        body=_json.dumps({"title": "Test Title"}).encode(),
        url="/api/posts",
        params={},
        headers=_make_auth_header(),
    )
    resp = function_app.create_post(req)

    assert resp.status_code == 400
    body = _json.loads(resp.get_body())
    assert "title" in body.get("error", "").lower() or "description" in body.get("error", "").lower()


def test_create_post_invalid_json(monkeypatch):
    """POST /api/posts with auth but invalid JSON body returns 400."""
    mock_client = MagicMock()
    monkeypatch.setattr("function_app.get_container_client", lambda: mock_client)

    req = func.HttpRequest(
        method="POST",
        body=b"not-json",
        url="/api/posts",
        params={},
        headers=_make_auth_header(),
    )
    resp = function_app.create_post(req)

    assert resp.status_code == 400
    body = _json.loads(resp.get_body())
    assert "json" in body.get("error", "").lower()


def test_create_post_success(monkeypatch):
    """POST /api/posts with valid auth and body returns 201 with slug (unit test)."""
    mock_client = MagicMock()
    mock_blob = MagicMock()
    mock_client.get_blob_client.return_value = mock_blob
    mock_client.list_blobs.return_value = []
    monkeypatch.setattr("function_app.get_container_client", lambda: mock_client)

    req = func.HttpRequest(
        method="POST",
        body=_json.dumps({
            "title": "My Test Post",
            "description": "A test description",
            "body": "Post content here",
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


def test_create_post_integration(monkeypatch, container_client):
    """POST /api/posts with valid auth and body creates blob in Azurite (integration test)."""
    monkeypatch.setattr("function_app.get_container_client", lambda: container_client)

    req = func.HttpRequest(
        method="POST",
        body=_json.dumps({
            "title": "Integration Test Post",
            "description": "Integration test description",
            "body": "Integration post content",
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
    slug = body["slug"]
    # Verify blob was actually created in Azurite
    blob_client = container_client.get_blob_client(f"{slug}.md")
    blob_data = blob_client.download_blob().readall()
    assert len(blob_data) > 0


# -- update_post (PUT /api/posts/{slug}) --

def test_update_post_requires_auth(monkeypatch):
    """PUT /api/posts/{slug} without X-MS-CLIENT-PRINCIPAL returns 401."""
    mock_client = MagicMock()
    monkeypatch.setattr("function_app.get_container_client", lambda: mock_client)

    req = func.HttpRequest(
        method="PUT",
        body=_json.dumps({"title": "Updated", "description": "Updated desc"}).encode(),
        url="/api/posts/test-slug",
        params={},
        headers={},
        route_params={"slug": "test-slug"},
    )
    resp = function_app.update_post(req)

    assert resp.status_code == 401
    assert mock_client.get_blob_client.called is False


def test_update_post_not_found(monkeypatch):
    """PUT /api/posts/{slug} with auth but missing blob returns 404."""
    mock_client = MagicMock()
    mock_blob = MagicMock()
    mock_blob.download_blob.side_effect = ResourceNotFoundError("not found")
    mock_client.get_blob_client.return_value = mock_blob
    monkeypatch.setattr("function_app.get_container_client", lambda: mock_client)

    req = func.HttpRequest(
        method="PUT",
        body=_json.dumps({"title": "Updated", "description": "Updated desc"}).encode(),
        url="/api/posts/test-slug",
        params={},
        headers=_make_auth_header(),
        route_params={"slug": "test-slug"},
    )
    resp = function_app.update_post(req)

    assert resp.status_code == 404


def test_update_post_preserves_date(monkeypatch, container_client):
    """PUT /api/posts/{slug} preserves the original creation date — does not reset to now()."""
    from schema import build_post as _build_post, serialize_post as _serialize_post

    monkeypatch.setattr("function_app.get_container_client", lambda: container_client)

    original_date = "2026-01-15T00:00:00+00:00"
    original_post = _build_post(
        title="Original Title",
        slug="test-slug",
        date=original_date,
        description="Original description",
        body="Original body",
        published=False,
    )
    container_client.upload_blob("test-slug.md", _serialize_post(original_post).encode(), overwrite=True)

    req = func.HttpRequest(
        method="PUT",
        body=_json.dumps({
            "title": "Updated Title",
            "description": "Updated description",
            "body": "Updated body",
            "published": False,
        }).encode(),
        url="/api/posts/test-slug",
        params={},
        headers=_make_auth_header(),
        route_params={"slug": "test-slug"},
    )
    resp = function_app.update_post(req)

    assert resp.status_code == 200
    body = _json.loads(resp.get_body())
    assert body["date"] == original_date, f"Expected {original_date!r}, got {body['date']!r}"


def test_update_post_success(monkeypatch):
    """PUT /api/posts/{slug} with auth and valid body returns 200 with all required fields (unit test)."""
    from schema import build_post as _build_post, serialize_post as _serialize_post

    original_date = "2026-01-15T00:00:00+00:00"
    original_post = _build_post(
        title="Original",
        slug="test-slug",
        date=original_date,
        description="Original desc",
        body="Original body",
        published=False,
    )
    raw_content = _serialize_post(original_post).encode()

    mock_blob = MagicMock()
    mock_blob.download_blob.return_value.readall.return_value = raw_content
    mock_client = MagicMock()
    mock_client.get_blob_client.return_value = mock_blob
    monkeypatch.setattr("function_app.get_container_client", lambda: mock_client)

    req = func.HttpRequest(
        method="PUT",
        body=_json.dumps({
            "title": "Updated Title",
            "description": "Updated description",
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


def test_update_post_integration(monkeypatch, container_client):
    """PUT /api/posts/{slug} with auth and valid body updates blob in Azurite (integration test)."""
    from schema import build_post as _build_post, serialize_post as _serialize_post

    monkeypatch.setattr("function_app.get_container_client", lambda: container_client)

    original_post = _build_post(
        title="Original Title",
        slug="test-slug",
        date="2026-01-15T00:00:00+00:00",
        description="Original description",
        body="Original body",
        published=False,
    )
    container_client.upload_blob("test-slug.md", _serialize_post(original_post).encode(), overwrite=True)

    req = func.HttpRequest(
        method="PUT",
        body=_json.dumps({
            "title": "Updated Title",
            "description": "Updated description",
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


# -- delete_post (DELETE /api/posts/{slug}) --

def test_delete_post_requires_auth(monkeypatch):
    """DELETE /api/posts/{slug} without X-MS-CLIENT-PRINCIPAL returns 401."""
    mock_client = MagicMock()
    monkeypatch.setattr("function_app.get_container_client", lambda: mock_client)

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
    assert mock_client.get_blob_client.called is False


def test_delete_post_not_found(monkeypatch):
    """DELETE /api/posts/{slug} with auth but missing blob returns 404."""
    mock_blob = MagicMock()
    mock_blob.delete_blob.side_effect = ResourceNotFoundError("not found")
    mock_client = MagicMock()
    mock_client.get_blob_client.return_value = mock_blob
    monkeypatch.setattr("function_app.get_container_client", lambda: mock_client)

    req = func.HttpRequest(
        method="DELETE",
        body=b"",
        url="/api/posts/test-slug",
        params={},
        headers=_make_auth_header(),
        route_params={"slug": "test-slug"},
    )
    resp = function_app.delete_post(req)

    assert resp.status_code == 404


def test_delete_post_success(monkeypatch):
    """DELETE /api/posts/{slug} with auth and existing blob returns 204 with empty body (unit test)."""
    mock_blob = MagicMock()
    mock_client = MagicMock()
    mock_client.get_blob_client.return_value = mock_blob
    monkeypatch.setattr("function_app.get_container_client", lambda: mock_client)

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


def test_delete_post_integration(monkeypatch, container_client):
    """DELETE /api/posts/{slug} with auth deletes blob from Azurite (integration test)."""
    from schema import build_post as _build_post, serialize_post as _serialize_post

    monkeypatch.setattr("function_app.get_container_client", lambda: container_client)

    post = _build_post(
        title="Post to Delete",
        slug="test-slug",
        date="2026-01-15T00:00:00+00:00",
        description="Will be deleted",
        body="Delete me",
        published=False,
    )
    container_client.upload_blob("test-slug.md", _serialize_post(post).encode(), overwrite=True)

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
    # Verify blob no longer exists in Azurite
    blob_client = container_client.get_blob_client("test-slug.md")
    assert blob_client.exists() is False
