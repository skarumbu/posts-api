"""
Tests for function_app.py HTTP handlers.

Covers all section-generic route handlers using unittest.mock.patch on
requests.get/post/delete — both "writing" and "diary" sections are backed by
the same HistoryApiStorage class (different `section=` values), so both are
mocked at the same requests-level HTTP contract. Pure unit tests with mocked
storage — no external services required.
"""
from unittest.mock import patch, MagicMock
import json as _json
import os

import pytest
import requests
import azure.functions as func

import function_app
from schema_writing import build_post, serialize_post
from schema_diary import build_entry, serialize_entry
from sections import SECTIONS


def _auth_patch(email: str = "test@example.com"):
    """Patch require_auth to return the given Google identity without touching real JWT logic."""
    return patch("function_app.require_auth", return_value=("google-sub-123", email))


_ENV = {
    "HISTORY_API_URL": "https://history-api-prod.azurewebsites.net/api",
    "HISTORY_API_KEY": "test-machine-key",
}


# ---------------------------------------------------------------------------
# HistoryApiStorage response-shape helpers — mirror the request/response
# contract exercised directly in tests/test_history_api_storage.py.
# ---------------------------------------------------------------------------

def _storage_get_success(version_id: str, content: str):
    """A 200 response shaped like GET /sections/{section}/documents/{slug}."""
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"version_id": version_id, "content": content}
    return resp


def _storage_get_not_found():
    """A 404 response — HistoryApiStorage.get()/slug_exists() only inspect status_code."""
    resp = MagicMock()
    resp.status_code = 404
    return resp


def _storage_list_success(slugs: list):
    """A 200 response shaped like GET /sections/{section}/documents."""
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"documents": [{"slug": s, "latest_version_id": "v1", "content_type": "markdown"} for s in slugs]}
    return resp


def _storage_write_success(status_code: int = 201):
    """A successful POST (create/update) or DELETE response."""
    resp = MagicMock()
    resp.status_code = status_code
    return resp


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

def test_get_item_requires_auth():
    """GET /api/sections/writing/items/:slug without auth returns 401 — writing is private now."""
    req = func.HttpRequest(
        method="GET", body=b"", url="/api/sections/writing/items/test", params={},
        route_params={"section": "writing", "slug": "test"},
    )
    resp = function_app.get_item(req)

    assert resp.status_code == 401


def test_get_item_success():
    """GET /api/sections/writing/items/test returns 200 with item data when history-api returns 200."""
    post = build_post(
        title="Test Post",
        slug="test",
        date="2026-01-01T00:00:00+00:00",
        description="A test",
        body="Test body",
        published=True,
    )
    raw = serialize_post(post)
    mock_resp = _storage_get_success("v1", raw)

    with patch.dict(os.environ, _ENV), patch("requests.get", return_value=mock_resp) as mock_get, _auth_patch():
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
    assert "sections/writing/documents/test" in mock_get.call_args[0][0]


def test_get_item_not_found():
    """GET /api/sections/writing/items/missing returns 404 when history-api returns 404."""
    mock_resp = _storage_get_not_found()

    with patch.dict(os.environ, _ENV), patch("requests.get", return_value=mock_resp), _auth_patch():
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

def test_list_items_requires_auth():
    """GET /api/sections/writing/items without auth returns 401 — writing is private now."""
    req = func.HttpRequest(
        method="GET", body=b"", url="/api/sections/writing/items", params={},
        route_params={"section": "writing"},
    )
    resp = function_app.list_items(req)

    assert resp.status_code == 401


def test_list_items_empty():
    """GET /api/sections/writing/items with no documents returns 200 and empty items array."""
    list_resp = _storage_list_success([])

    with patch.dict(os.environ, _ENV), patch("requests.get", return_value=list_resp), _auth_patch():
        req = func.HttpRequest(
            method="GET", body=b"", url="/api/sections/writing/items", params={},
            route_params={"section": "writing"},
        )
        resp = function_app.list_items(req)

    assert resp.status_code == 200
    body = _json.loads(resp.get_body())
    assert body == {"items": []}


def test_list_items_includes_drafts_no_published_filter():
    """GET /api/sections/writing/items with auth returns drafts too — now that writing is
    private, visibility is ownership-based, not published-based (matching diary)."""
    published = build_post(
        title="Published Post", slug="published-post", date="2026-05-30T00:00:00+00:00",
        description="A published post", body="Hello world", published=True,
    )
    draft = build_post(
        title="Draft Post", slug="draft-post", date="2026-05-29T00:00:00+00:00",
        description="A draft", body="Draft body", published=False,
    )

    list_resp = _storage_list_success(["published-post", "draft-post"])
    published_resp = _storage_get_success("v1", serialize_post(published))
    draft_resp = _storage_get_success("v1", serialize_post(draft))

    with patch.dict(os.environ, _ENV), \
         patch("requests.get", side_effect=[list_resp, published_resp, draft_resp]), \
         _auth_patch():
        req = func.HttpRequest(
            method="GET", body=b"", url="/api/sections/writing/items", params={},
            route_params={"section": "writing"},
        )
        resp = function_app.list_items(req)

    assert resp.status_code == 200
    body = _json.loads(resp.get_body())
    assert {i["slug"] for i in body["items"]} == {"published-post", "draft-post"}


# ---------------------------------------------------------------------------
# POST /api/sections/writing/items (create_item)
# ---------------------------------------------------------------------------

def test_create_item_success():
    """POST /api/sections/writing/items with valid Google auth returns 201 with slug."""
    get_resp = _storage_get_not_found()  # slug not taken — generate_slug() + create()'s own check
    post_resp = _storage_write_success(201)

    with patch.dict(os.environ, _ENV), \
         _auth_patch(), \
         patch("requests.get", return_value=get_resp), \
         patch("requests.post", return_value=post_resp) as mock_post:
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
    assert "writing::" in mock_post.call_args[0][0]


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
    get_resp = _storage_get_not_found()

    captured_body = {}

    def capture_post(url, **kwargs):
        data = kwargs.get("json") or {}
        content = data.get("content", "")
        if content:
            captured_body["content"] = content
        return _storage_write_success(201)

    with patch.dict(os.environ, _ENV), \
         _auth_patch(email="owner@example.com"), \
         patch("requests.get", return_value=get_resp), \
         patch("requests.post", side_effect=capture_post):
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

    get_resp = _storage_get_success("v1", raw)
    post_resp = _storage_write_success(201)

    with patch.dict(os.environ, _ENV), \
         _auth_patch(), \
         patch("requests.get", return_value=get_resp), \
         patch("requests.post", return_value=post_resp) as mock_post:
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
    assert "writing::" in mock_post.call_args[0][0]


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

    get_resp = _storage_get_success("v1", raw)

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

    get_resp = _storage_get_success("v1", raw)
    post_resp = _storage_write_success(200)

    with patch.dict(os.environ, {**_ENV, "ALLOWED_WRITERS": "owner@example.com"}), \
         _auth_patch(email="owner@example.com"), \
         patch("requests.get", return_value=get_resp), \
         patch("requests.post", return_value=post_resp):
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
    get_resp = _storage_get_not_found()

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

    get_resp = _storage_get_success("v1", raw)
    del_resp = _storage_write_success(204)

    with patch.dict(os.environ, _ENV), \
         _auth_patch(), \
         patch("requests.get", return_value=get_resp), \
         patch("requests.delete", return_value=del_resp) as mock_delete:
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
    assert "sections/writing/documents/test-slug" in mock_delete.call_args[0][0]


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

    get_resp = _storage_get_success("v1", raw)

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
    get_resp = _storage_get_not_found()

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
# Diary section (HistoryApiStorage-backed, always private — no per-item
# visibility flag). Same storage class and mocking approach as "writing"
# above, just section="diary".
# ---------------------------------------------------------------------------

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

    list_resp = _storage_list_success(["today"])
    content_resp = _storage_get_success("v1", raw)

    with patch.dict(os.environ, _ENV), patch("requests.get", side_effect=[list_resp, content_resp]), _auth_patch():
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


def test_list_items_diary_excludes_other_authors_entries():
    """GET /api/sections/diary/items only returns entries the requester wrote themselves."""
    mine = build_entry(
        title="Mine", slug="mine", date="2026-05-30T00:00:00+00:00",
        blocks=[], author_email="owner@example.com",
    )
    theirs = build_entry(
        title="Theirs", slug="theirs", date="2026-05-30T00:00:00+00:00",
        blocks=[], author_email="other@example.com",
    )

    list_resp = _storage_list_success(["mine", "theirs"])
    mine_resp = _storage_get_success("v1", serialize_entry(mine))
    theirs_resp = _storage_get_success("v1", serialize_entry(theirs))

    with patch.dict(os.environ, _ENV), \
         patch("requests.get", side_effect=[list_resp, mine_resp, theirs_resp]), \
         _auth_patch(email="owner@example.com"):
        req = func.HttpRequest(
            method="GET", body=b"", url="/api/sections/diary/items", params={},
            route_params={"section": "diary"},
        )
        resp = function_app.list_items(req)

    assert resp.status_code == 200
    body = _json.loads(resp.get_body())
    assert [i["slug"] for i in body["items"]] == ["mine"]


def test_get_item_diary_other_authors_entry_404s():
    """GET /api/sections/diary/items/:slug 404s (not 403) when the entry belongs to a different author."""
    entry = build_entry(
        title="Theirs", slug="theirs", date="2026-05-30T00:00:00+00:00",
        blocks=[], author_email="other@example.com",
    )
    raw = serialize_entry(entry)

    get_resp = _storage_get_success("v1", raw)

    with patch.dict(os.environ, _ENV), patch("requests.get", return_value=get_resp) as mock_get, _auth_patch(email="owner@example.com"):
        req = func.HttpRequest(
            method="GET", body=b"", url="/api/sections/diary/items/theirs", params={},
            route_params={"section": "diary", "slug": "theirs"},
        )
        resp = function_app.get_item(req)

    assert resp.status_code == 404
    assert "sections/diary/documents/theirs" in mock_get.call_args[0][0]


# ---------------------------------------------------------------------------
# GET .../versions, .../versions/:id, .../versions/:v1/diff/:v2
# (list_versions, get_version, diff_versions) — proxy straight through to
# history-api, with the same auth/visibility rules as get_item.
# ---------------------------------------------------------------------------

def _published_writing_item_resp():
    """A history-api-shaped 200 response for a writing-section post, for mocking the
    storage.get() call that _authorize_version_read makes to check ownership."""
    post = build_post(
        title="Test Post", slug="my-post", date="2026-01-01T00:00:00+00:00",
        description="A test", body="Test body", published=True,
    )
    return _storage_get_success("v1", serialize_post(post))


def test_list_versions_writing_requires_auth():
    """GET /api/sections/writing/items/:slug/versions without auth returns 401 — writing
    is private now, matching diary."""
    req = func.HttpRequest(
        method="GET", body=b"", url="/api/sections/writing/items/my-post/versions", params={},
        route_params={"section": "writing", "slug": "my-post"},
    )
    resp = function_app.list_versions(req)

    assert resp.status_code == 401


def test_list_versions_writing_success_with_auth():
    """GET /api/sections/writing/items/:slug/versions succeeds for an authenticated
    owner, regardless of the post's published state."""
    history_resp = MagicMock(status_code=200, json=lambda: {"versions": [{"version_id": "v1"}]})

    with patch.dict(os.environ, _ENV), \
         patch("requests.get", side_effect=[_published_writing_item_resp(), history_resp]), \
         _auth_patch():
        req = func.HttpRequest(
            method="GET", body=b"", url="/api/sections/writing/items/my-post/versions", params={},
            route_params={"section": "writing", "slug": "my-post"},
        )
        resp = function_app.list_versions(req)

    assert resp.status_code == 200
    body = _json.loads(resp.get_body())
    assert body == {"versions": [{"version_id": "v1"}]}


def test_list_versions_diary_requires_auth():
    """GET /api/sections/diary/items/:slug/versions without auth returns 401."""
    req = func.HttpRequest(
        method="GET", body=b"", url="/api/sections/diary/items/my-entry/versions", params={},
        route_params={"section": "diary", "slug": "my-entry"},
    )
    resp = function_app.list_versions(req)

    assert resp.status_code == 401


def test_list_versions_history_api_error_returns_502():
    """A real upstream HTTP failure from history-api degrades to 502, not 500."""
    error_resp = MagicMock(status_code=500)
    error_resp.raise_for_status.side_effect = requests.exceptions.HTTPError("boom")
    with patch.dict(os.environ, _ENV), \
         patch("requests.get", side_effect=[_published_writing_item_resp(), error_resp]), \
         _auth_patch():
        req = func.HttpRequest(
            method="GET", body=b"", url="/api/sections/writing/items/my-post/versions", params={},
            route_params={"section": "writing", "slug": "my-post"},
        )
        resp = function_app.list_versions(req)

    assert resp.status_code == 502
    body = _json.loads(resp.get_body())
    assert body == {"error": "storage error"}


def test_list_versions_diary_wrong_author_404s():
    """GET .../versions 404s (not 403) when the entry belongs to a different author."""
    entry = build_entry(
        title="Theirs", slug="theirs", date="2026-05-30T00:00:00+00:00",
        blocks=[], author_email="other@example.com",
    )
    raw = serialize_entry(entry)

    get_resp = _storage_get_success("v1", raw)

    with patch.dict(os.environ, _ENV), patch("requests.get", return_value=get_resp), _auth_patch(email="owner@example.com"):
        req = func.HttpRequest(
            method="GET", body=b"", url="/api/sections/diary/items/theirs/versions", params={},
            route_params={"section": "diary", "slug": "theirs"},
        )
        resp = function_app.list_versions(req)

    assert resp.status_code == 404


def test_list_versions_invalid_slug():
    """GET .../versions with invalid slug returns 400 — no requests call needed."""
    req = func.HttpRequest(
        method="GET", body=b"", url="/api/sections/writing/items/../etc/versions", params={},
        route_params={"section": "writing", "slug": "../etc"},
    )
    resp = function_app.list_versions(req)

    assert resp.status_code == 400


def test_get_version_writing_requires_auth():
    """GET /api/sections/writing/items/:slug/versions/:id without auth returns 401 —
    writing is private now, matching diary."""
    req = func.HttpRequest(
        method="GET", body=b"", url="/api/sections/writing/items/my-post/versions/v1", params={},
        route_params={"section": "writing", "slug": "my-post", "version_id": "v1"},
    )
    resp = function_app.get_version(req)

    assert resp.status_code == 401


def test_get_version_writing_success_with_auth():
    """GET /api/sections/writing/items/:slug/versions/:id succeeds for an authenticated
    owner, regardless of the post's published state."""
    history_resp = MagicMock(status_code=200, json=lambda: {"version_id": "v1", "content": "hello"})

    with patch.dict(os.environ, _ENV), \
         patch("requests.get", side_effect=[_published_writing_item_resp(), history_resp]), \
         _auth_patch():
        req = func.HttpRequest(
            method="GET", body=b"", url="/api/sections/writing/items/my-post/versions/v1", params={},
            route_params={"section": "writing", "slug": "my-post", "version_id": "v1"},
        )
        resp = function_app.get_version(req)

    assert resp.status_code == 200
    body = _json.loads(resp.get_body())
    assert body == {"version_id": "v1", "content": "hello"}


def test_get_version_missing_version_relays_404():
    """A well-formed request for a version_id history-api doesn't have relays
    history-api's 404 body cleanly, instead of being converted into a 502."""
    history_resp = MagicMock(status_code=404, json=lambda: {"error": "version not found"})

    with patch.dict(os.environ, _ENV), \
         patch("requests.get", side_effect=[_published_writing_item_resp(), history_resp]), \
         _auth_patch():
        req = func.HttpRequest(
            method="GET", body=b"", url="/api/sections/writing/items/my-post/versions/missing", params={},
            route_params={"section": "writing", "slug": "my-post", "version_id": "missing"},
        )
        resp = function_app.get_version(req)

    assert resp.status_code == 404
    body = _json.loads(resp.get_body())
    assert body == {"error": "version not found"}


def test_get_version_invalid_version_id():
    """GET .../versions/:id with a version_id containing path-unsafe characters returns 400."""
    req = func.HttpRequest(
        method="GET", body=b"", url="/api/sections/writing/items/my-post/versions/../etc", params={},
        route_params={"section": "writing", "slug": "my-post", "version_id": "../etc"},
    )
    resp = function_app.get_version(req)

    assert resp.status_code == 400


def test_get_version_diary_requires_auth():
    """GET .../versions/:id without auth returns 401."""
    req = func.HttpRequest(
        method="GET", body=b"", url="/api/sections/diary/items/my-entry/versions/v1", params={},
        route_params={"section": "diary", "slug": "my-entry", "version_id": "v1"},
    )
    resp = function_app.get_version(req)

    assert resp.status_code == 401


def test_get_version_diary_wrong_author_404s():
    """GET .../versions/:id 404s (not 403) when the entry belongs to a different author."""
    entry = build_entry(
        title="Theirs", slug="theirs", date="2026-05-30T00:00:00+00:00",
        blocks=[], author_email="other@example.com",
    )
    raw = serialize_entry(entry)

    get_resp = _storage_get_success("v1", raw)

    with patch.dict(os.environ, _ENV), patch("requests.get", return_value=get_resp), _auth_patch(email="owner@example.com"):
        req = func.HttpRequest(
            method="GET", body=b"", url="/api/sections/diary/items/theirs/versions/v1", params={},
            route_params={"section": "diary", "slug": "theirs", "version_id": "v1"},
        )
        resp = function_app.get_version(req)

    assert resp.status_code == 404


def test_diff_versions_writing_requires_auth():
    """GET .../versions/:v1/diff/:v2 without auth returns 401 — writing is private now,
    matching diary."""
    req = func.HttpRequest(
        method="GET", body=b"", url="/api/sections/writing/items/my-post/versions/v1/diff/v2", params={},
        route_params={"section": "writing", "slug": "my-post", "v1": "v1", "v2": "v2"},
    )
    resp = function_app.diff_versions(req)

    assert resp.status_code == 401


def test_diff_versions_writing_success_with_auth():
    """GET .../versions/:v1/diff/:v2 succeeds for an authenticated owner, regardless of
    the post's published state."""
    history_resp = MagicMock(status_code=200, json=lambda: {"diff": "..."})

    with patch.dict(os.environ, _ENV), \
         patch("requests.get", side_effect=[_published_writing_item_resp(), history_resp]), \
         _auth_patch():
        req = func.HttpRequest(
            method="GET", body=b"", url="/api/sections/writing/items/my-post/versions/v1/diff/v2", params={},
            route_params={"section": "writing", "slug": "my-post", "v1": "v1", "v2": "v2"},
        )
        resp = function_app.diff_versions(req)

    assert resp.status_code == 200
    body = _json.loads(resp.get_body())
    assert body == {"diff": "..."}


def test_diff_versions_invalid_version_id():
    """GET .../versions/:v1/diff/:v2 with a path-unsafe v1/v2 returns 400."""
    req = func.HttpRequest(
        method="GET", body=b"", url="/api/sections/writing/items/my-post/versions/../etc/diff/v2", params={},
        route_params={"section": "writing", "slug": "my-post", "v1": "../etc", "v2": "v2"},
    )
    resp = function_app.diff_versions(req)

    assert resp.status_code == 400


def test_diff_versions_diary_requires_auth():
    """GET .../versions/:v1/diff/:v2 without auth returns 401."""
    req = func.HttpRequest(
        method="GET", body=b"", url="/api/sections/diary/items/my-entry/versions/v1/diff/v2", params={},
        route_params={"section": "diary", "slug": "my-entry", "v1": "v1", "v2": "v2"},
    )
    resp = function_app.diff_versions(req)

    assert resp.status_code == 401


def test_diff_versions_diary_wrong_author_404s():
    """GET .../versions/:v1/diff/:v2 404s (not 403) when the entry belongs to a different author."""
    entry = build_entry(
        title="Theirs", slug="theirs", date="2026-05-30T00:00:00+00:00",
        blocks=[], author_email="other@example.com",
    )
    raw = serialize_entry(entry)

    get_resp = _storage_get_success("v1", raw)

    with patch.dict(os.environ, _ENV), patch("requests.get", return_value=get_resp), _auth_patch(email="owner@example.com"):
        req = func.HttpRequest(
            method="GET", body=b"", url="/api/sections/diary/items/theirs/versions/v1/diff/v2", params={},
            route_params={"section": "diary", "slug": "theirs", "v1": "v1", "v2": "v2"},
        )
        resp = function_app.diff_versions(req)

    assert resp.status_code == 404


def test_create_item_diary_success():
    """POST /api/sections/diary/items with auth creates an entry with text+sticker blocks."""
    get_resp = _storage_get_not_found()  # slug not taken — generate_slug() + create()'s own check
    post_resp = _storage_write_success(201)

    with patch.dict(os.environ, _ENV), \
         _auth_patch(email="owner@example.com"), \
         patch("requests.get", return_value=get_resp), \
         patch("requests.post", return_value=post_resp) as mock_post:
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
    mock_post.assert_called_once()
    assert "diary::" in mock_post.call_args[0][0]


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

    get_resp = _storage_get_success("v1", raw)
    post_resp = _storage_write_success(200)

    with patch.dict(os.environ, _ENV), \
         _auth_patch(email="owner@example.com"), \
         patch("requests.get", return_value=get_resp), \
         patch("requests.post", return_value=post_resp) as mock_post:
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
    assert "diary::" in mock_post.call_args[0][0]


def test_update_item_diary_ownership_mismatch():
    """PUT on a diary entry returns 403 when requester is not the author."""
    entry = build_entry(
        title="Original", slug="today", date="2026-05-30T00:00:00+00:00",
        blocks=[], author_email="alice@example.com",
    )
    raw = serialize_entry(entry)

    get_resp = _storage_get_success("v1", raw)

    with patch.dict(os.environ, _ENV), \
         _auth_patch(email="bob@example.com"), \
         patch("requests.get", return_value=get_resp):
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

    get_resp = _storage_get_success("v1", raw)
    del_resp = _storage_write_success(204)

    with patch.dict(os.environ, _ENV), \
         _auth_patch(email="owner@example.com"), \
         patch("requests.get", return_value=get_resp), \
         patch("requests.delete", return_value=del_resp) as mock_delete:
        req = func.HttpRequest(
            method="DELETE", body=b"", url="/api/sections/diary/items/today", params={},
            headers={}, route_params={"section": "diary", "slug": "today"},
        )
        resp = function_app.delete_item(req)

    assert resp.status_code == 204
    mock_delete.assert_called_once()
    assert "sections/diary/documents/today" in mock_delete.call_args[0][0]
