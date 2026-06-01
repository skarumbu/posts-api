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
