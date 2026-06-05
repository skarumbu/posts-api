import json
import os
import re

import azure.functions as func
from azure.core.exceptions import ResourceNotFoundError

from schema import parse_post
from slugs import get_container_client
from auth import require_auth
from schema import build_post, validate_post, serialize_post
from slugs import generate_slug
from datetime import datetime, timezone

# ANONYMOUS is intentional: read routes (Phase 2/3) are public.
# Write routes (Phase 4) must validate the Bearer token in the handler before mutating any data.
app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)

ALLOWED_ORIGIN = "https://www.quixotry.me"
SLUG_RE = re.compile(r"^[a-z0-9-]+$")


def _json_response(data: dict, status_code: int = 200) -> func.HttpResponse:
    return func.HttpResponse(
        json.dumps(data),
        status_code=status_code,
        mimetype="application/json",
        headers={"Access-Control-Allow-Origin": ALLOWED_ORIGIN},
    )


def _unauthorized(message: str = "Unauthorized") -> func.HttpResponse:
    return func.HttpResponse(
        json.dumps({"error": message}),
        status_code=401,
        mimetype="application/json",
        headers={"Access-Control-Allow-Origin": ALLOWED_ORIGIN},
    )


@app.route(route="posts", methods=["POST"])
def create_post(req: func.HttpRequest) -> func.HttpResponse:
    """Create a new post (API-03). Requires X-MS-CLIENT-PRINCIPAL (Easy Auth)."""
    # 1. Auth gate — must be first; no body parsing before auth check
    try:
        require_auth(req)
    except ValueError:
        return _unauthorized()

    # 2. Parse JSON body
    try:
        body = req.get_json()
    except Exception:
        return _json_response({"error": "Invalid JSON body"}, status_code=400)

    # 3. Extract fields
    title = (body.get("title") or "").strip()
    description = (body.get("description") or "").strip()
    post_body = (body.get("body") or "").strip()
    published = bool(body.get("published", False))

    # 4. Validate required fields
    if not title or not description:
        return _json_response({"error": "title and description are required"}, status_code=400)

    # 5. Generate slug, build post, validate, serialize, upload
    try:
        client = get_container_client()
        slug = generate_slug(title, client)
        post = build_post(
            title=title,
            slug=slug,
            date=datetime.now(timezone.utc).isoformat(),
            description=description,
            body=post_body,
            published=published,
        )
        errors = validate_post(post)
        if errors:
            return _json_response({"error": errors[0]}, status_code=400)
        content = serialize_post(post)
        client.get_blob_client(f"{slug}.md").upload_blob(content.encode(), overwrite=True)
        return _json_response({"slug": slug}, status_code=201)
    except Exception:
        return _json_response({"error": "storage error"}, status_code=500)


@app.route(route="posts/{slug}", methods=["PUT"])
def update_post(req: func.HttpRequest) -> func.HttpResponse:
    """Update an existing post (API-04). Requires X-MS-CLIENT-PRINCIPAL (Easy Auth).
    Preserves original creation date — does not reset to now() on update.
    """
    # 1. Auth gate — must be first (T-04-05)
    try:
        require_auth(req)
    except ValueError:
        return _unauthorized()

    # 2. Slug validation — checked before any storage access (T-04-08)
    slug = req.route_params.get("slug")
    if not slug or not SLUG_RE.match(slug):
        return _json_response({"error": "invalid slug"}, status_code=400)

    # 3. Parse JSON body
    try:
        body = req.get_json()
    except Exception:
        return _json_response({"error": "Invalid JSON body"}, status_code=400)

    # 4. Read existing blob — serves as 404 check AND preserves original date (T-04-09, Pitfall 3)
    try:
        client = get_container_client()
        blob_client = client.get_blob_client(f"{slug}.md")
        raw = blob_client.download_blob().readall().decode("utf-8")
        existing = parse_post(raw)
        original_date = existing.metadata.get("date")
    except ResourceNotFoundError:
        return _json_response({"error": "not found"}, status_code=404)
    except Exception:
        return _json_response({"error": "storage error"}, status_code=500)

    # 5. Extract and validate fields
    title = (body.get("title") or "").strip()
    description = (body.get("description") or "").strip()
    post_body = body.get("body", "")
    published = bool(body.get("published", False))
    if not title or not description:
        return _json_response({"error": "title and description are required"}, status_code=400)

    # 6. Build, validate, serialize, upload
    try:
        post = build_post(
            title=title,
            slug=slug,
            date=original_date,   # preserve original creation date
            description=description,
            body=post_body,
            published=published,
            # updated_at omitted → build_post auto-sets to now()
        )
        errors = validate_post(post)
        if errors:
            return _json_response({"error": errors[0]}, status_code=400)
        content = serialize_post(post)
        blob_client.upload_blob(content.encode(), overwrite=True)
        date_val = post.metadata.get("date")
        updated_val = post.metadata.get("updatedAt")
        return _json_response({
            "title": post.metadata.get("title"),
            "slug": post.metadata.get("slug"),
            "date": date_val.isoformat() if hasattr(date_val, "isoformat") else str(date_val) if date_val is not None else "",
            "description": post.metadata.get("description"),
            "updatedAt": updated_val.isoformat() if hasattr(updated_val, "isoformat") else str(updated_val) if updated_val is not None else "",
            "published": post.metadata.get("published"),
        }, status_code=200)
    except Exception:
        return _json_response({"error": "storage error"}, status_code=500)


@app.route(route="posts/{slug}", methods=["DELETE"])
def delete_post(req: func.HttpRequest) -> func.HttpResponse:
    """Delete a post by slug (API-05). Requires X-MS-CLIENT-PRINCIPAL (Easy Auth).
    Returns 204 No Content on success — bare HttpResponse, not _json_response().
    """
    # 1. Auth gate — must be first (T-04-06)
    try:
        require_auth(req)
    except ValueError:
        return _unauthorized()

    # 2. Slug validation (T-04-08)
    slug = req.route_params.get("slug")
    if not slug or not SLUG_RE.match(slug):
        return _json_response({"error": "invalid slug"}, status_code=400)

    # 3. Delete blob
    try:
        client = get_container_client()
        client.get_blob_client(f"{slug}.md").delete_blob()
    except ResourceNotFoundError:
        return _json_response({"error": "not found"}, status_code=404)
    except Exception:
        return _json_response({"error": "storage error"}, status_code=500)

    # 4. Return 204 No Content — NOT _json_response() (would write a body)
    return func.HttpResponse(
        status_code=204,
        headers={"Access-Control-Allow-Origin": ALLOWED_ORIGIN},
    )


@app.route(route="health", methods=["GET"])
def health(req: func.HttpRequest) -> func.HttpResponse:
    return _json_response({"status": "ok", "service": "posts-api"})


@app.route(route="posts", methods=["GET"])
def list_posts(req: func.HttpRequest) -> func.HttpResponse:
    """Return all published posts as a JSON array sorted newest-first (API-01)."""
    try:
        client = get_container_client()
        posts = []
        for blob in client.list_blobs():
            raw = client.get_blob_client(blob.name).download_blob().readall().decode("utf-8")
            post = parse_post(raw)
            if post.metadata.get("published") is True:
                date_val = post.metadata.get("date")
                updated_val = post.metadata.get("updatedAt")
                posts.append({
                    "title": post.metadata.get("title"),
                    "slug": post.metadata.get("slug"),
                    "date": date_val.isoformat() if hasattr(date_val, "isoformat") else str(date_val) if date_val is not None else "",
                    "description": post.metadata.get("description"),
                    "updatedAt": updated_val.isoformat() if hasattr(updated_val, "isoformat") else str(updated_val) if updated_val is not None else "",
                })
        posts.sort(key=lambda p: p["date"], reverse=True)
        return _json_response({"posts": posts})
    except Exception:
        return _json_response({"error": "storage error"}, status_code=500)


@app.route(route="posts/{slug}", methods=["GET"])
def get_post(req: func.HttpRequest) -> func.HttpResponse:
    """Return a single published post by slug (API-02)."""
    slug = req.route_params.get("slug")
    if not slug or not SLUG_RE.match(slug):
        return _json_response({"error": "invalid slug"}, status_code=400)
    try:
        client = get_container_client()
        raw = client.get_blob_client(f"{slug}.md").download_blob().readall().decode("utf-8")
        post = parse_post(raw)
        if post.metadata.get("published") is not True:
            return _json_response({"error": "not found"}, status_code=404)
        date_val = post.metadata.get("date")
        updated_val = post.metadata.get("updatedAt")
        return _json_response({
            "title": post.metadata.get("title"),
            "slug": post.metadata.get("slug"),
            "date": date_val.isoformat() if hasattr(date_val, "isoformat") else str(date_val) if date_val is not None else "",
            "description": post.metadata.get("description"),
            "updatedAt": updated_val.isoformat() if hasattr(updated_val, "isoformat") else str(updated_val) if updated_val is not None else "",
            "body": post.content,
        })
    except ResourceNotFoundError:
        return _json_response({"error": "not found"}, status_code=404)
    except Exception:
        return _json_response({"error": "storage error"}, status_code=500)
