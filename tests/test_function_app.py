"""
Tests for function_app.py HTTP handlers.

Covers all section-generic route handlers using unittest.mock.patch on
requests.get/put/delete for the "writing" section (GitHub-backed), and on
BlobStorage._container_client for the "diary" section (Blob-backed). Pure
unit tests with mocked storage — no external services required.
"""
from unittest.mock import patch, MagicMock
import base64
import json as _json
import os

import pytest
import azure.functions as func

import function_app
from schema_writing import build_post, serialize_post
from schema_diary import build_entry, serialize_entry
from sections import SECTIONS


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


# ---------------------------------------------------------------------------
# Diary section (Blob-backed, always private — no per-item visibility flag)
# ---------------------------------------------------------------------------

def _mock_diary_container():
    """Patch the diary section's BlobStorage to use a fresh mock container client."""
    mock_container = MagicMock()
    patcher = patch.object(SECTIONS["diary"].storage, "_container_client", return_value=mock_container)
    return patcher, mock_container


def test_list_items_diary_requires_auth():
    """GET /api/sections/diary/items without auth returns 401 — unlike writing, which is public."""
    req = func.HttpRequest(
        method="GET", body=b"", url="/api/sections/diary/items", params={},
        route_params={"section": "diary"},
    )
    resp = function_app.list_items(req)

    assert resp.status_code == 401


def test_list_items_diary_returns_all_entries_no_published_filter():
    """GET /api/sections/diary/items with auth returns every entry — diary has no published flag."""
    entry = build_entry(
        title="Today", slug="today", date="2026-05-30T00:00:00+00:00",
        blocks=[{"type": "text", "content": "Hi", "style": {}}],
    )
    raw = serialize_entry(entry)

    blob_entry = MagicMock()
    blob_entry.name = "today.json"
    patcher, mock_container = _mock_diary_container()
    mock_container.list_blobs.return_value = [blob_entry]
    mock_blob = MagicMock()
    downloaded = MagicMock()
    downloaded.readall.return_value = raw.encode("utf-8")
    mock_blob.download_blob.return_value = downloaded
    mock_container.get_blob_client.return_value = mock_blob

    with patcher, _auth_patch():
        req = func.HttpRequest(
            method="GET", body=b"", url="/api/sections/diary/items", params={},
            route_params={"section": "diary"},
        )
        resp = function_app.list_items(req)

    assert resp.status_code == 200
    body = _json.loads(resp.get_body())
    assert len(body["items"]) == 1
    assert body["items"][0]["slug"] == "today"
    assert body["items"][0]["blocks"] == [{"type": "text", "content": "Hi", "style": {}}]


def test_get_item_diary_requires_auth():
    """GET /api/sections/diary/items/:slug without auth returns 401, even for an existing entry."""
    req = func.HttpRequest(
        method="GET", body=b"", url="/api/sections/diary/items/today", params={},
        route_params={"section": "diary", "slug": "today"},
    )
    resp = function_app.get_item(req)

    assert resp.status_code == 401


def test_create_item_diary_success():
    """POST /api/sections/diary/items with auth creates an entry with text+sticker blocks."""
    patcher, mock_container = _mock_diary_container()
    mock_blob = MagicMock()
    mock_blob.exists.return_value = False
    mock_container.get_blob_client.return_value = mock_blob

    with patcher, _auth_patch(email="owner@example.com"):
        req = func.HttpRequest(
            method="POST",
            body=_json.dumps({
                "title": "A Good Day",
                "blocks": [
                    {"type": "text", "content": "Went for a walk", "style": {"rotation": -2}},
                    {"type": "sticker", "emoji": "🌻", "style": {}},
                ],
            }).encode(),
            url="/api/sections/diary/items",
            params={},
            headers={},
            route_params={"section": "diary"},
        )
        resp = function_app.create_item(req)

    assert resp.status_code == 201
    body = _json.loads(resp.get_body())
    assert body["slug"] == "a-good-day"
    mock_blob.upload_blob.assert_called_once()


def test_create_item_diary_missing_title():
    """POST /api/sections/diary/items with auth but no title returns 400 — no storage access needed."""
    with _auth_patch():
        req = func.HttpRequest(
            method="POST",
            body=_json.dumps({"blocks": []}).encode(),
            url="/api/sections/diary/items",
            params={},
            headers={},
            route_params={"section": "diary"},
        )
        resp = function_app.create_item(req)

    assert resp.status_code == 400


def test_update_item_diary_success():
    """PUT /api/sections/diary/items/:slug with auth updates blocks and returns the shaped entry."""
    entry = build_entry(
        title="Original", slug="today", date="2026-05-30T00:00:00+00:00",
        blocks=[], author_email="owner@example.com",
    )
    raw = serialize_entry(entry)

    patcher, mock_container = _mock_diary_container()
    mock_blob = MagicMock()
    downloaded = MagicMock()
    downloaded.readall.return_value = raw.encode("utf-8")
    downloaded.properties.etag = "etag-1"
    mock_blob.download_blob.return_value = downloaded
    mock_container.get_blob_client.return_value = mock_blob

    with patcher, _auth_patch(email="owner@example.com"):
        req = func.HttpRequest(
            method="PUT",
            body=_json.dumps({
                "title": "Updated Title",
                "blocks": [{"type": "text", "content": "New entry", "style": {}}],
            }).encode(),
            url="/api/sections/diary/items/today",
            params={},
            headers={},
            route_params={"section": "diary", "slug": "today"},
        )
        resp = function_app.update_item(req)

    assert resp.status_code == 200
    body = _json.loads(resp.get_body())
    assert body["title"] == "Updated Title"
    assert body["blocks"] == [{"type": "text", "content": "New entry", "style": {}}]
    assert "published" not in body


def test_update_item_diary_ownership_mismatch():
    """PUT on a diary entry returns 403 when requester is not the author."""
    entry = build_entry(
        title="Original", slug="today", date="2026-05-30T00:00:00+00:00",
        blocks=[], author_email="alice@example.com",
    )
    raw = serialize_entry(entry)

    patcher, mock_container = _mock_diary_container()
    mock_blob = MagicMock()
    downloaded = MagicMock()
    downloaded.readall.return_value = raw.encode("utf-8")
    downloaded.properties.etag = "etag-1"
    mock_blob.download_blob.return_value = downloaded
    mock_container.get_blob_client.return_value = mock_blob

    with patcher, _auth_patch(email="bob@example.com"):
        req = func.HttpRequest(
            method="PUT",
            body=_json.dumps({"title": "Hacked", "blocks": []}).encode(),
            url="/api/sections/diary/items/today",
            params={},
            headers={},
            route_params={"section": "diary", "slug": "today"},
        )
        resp = function_app.update_item(req)

    assert resp.status_code == 403


def test_delete_item_diary_success():
    """DELETE /api/sections/diary/items/:slug with auth returns 204."""
    entry = build_entry(
        title="Today", slug="today", date="2026-05-30T00:00:00+00:00",
        blocks=[], author_email="owner@example.com",
    )
    raw = serialize_entry(entry)

    patcher, mock_container = _mock_diary_container()
    mock_blob = MagicMock()
    downloaded = MagicMock()
    downloaded.readall.return_value = raw.encode("utf-8")
    downloaded.properties.etag = "etag-1"
    mock_blob.download_blob.return_value = downloaded
    mock_container.get_blob_client.return_value = mock_blob

    with patcher, _auth_patch(email="owner@example.com"):
        req = func.HttpRequest(
            method="DELETE", body=b"", url="/api/sections/diary/items/today", params={},
            headers={}, route_params={"section": "diary", "slug": "today"},
        )
        resp = function_app.delete_item(req)

    assert resp.status_code == 204
    mock_blob.delete_blob.assert_called_once()
