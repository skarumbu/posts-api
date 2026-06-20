import json
import os
import re

import azure.functions as func
import requests

from schema import parse_post
from auth import require_auth
from schema import build_post, validate_post, serialize_post
from slugs import generate_slug, get_file_sha, list_posts_dir, create_file, update_file, delete_file
from datetime import datetime, timezone

# ANONYMOUS is intentional: read routes are public.
# Write routes validate the Bearer token in the handler before mutating any data.
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


def _check_allowlist(email: str) -> bool:
    """Return True if email is in ALLOWED_WRITERS, or no allowlist is configured."""
    raw = os.environ.get("ALLOWED_WRITERS", "").strip()
    if not raw:
        return True
    return email.lower() in {e.strip().lower() for e in raw.split(",") if e.strip()}


@app.route(route="posts", methods=["POST"])
def create_post(req: func.HttpRequest) -> func.HttpResponse:
    """Create a new post. Requires Google ID token (Authorization: Bearer)."""
    # 1. Auth gate — must be first; no body parsing before auth check (T-06-05)
    try:
        _, requester_email = require_auth(req)
    except ValueError:
        return _unauthorized()

    # 2. Allowlist check — only permitted writers can create posts
    if not _check_allowlist(requester_email):
        return _json_response({"error": "Forbidden"}, status_code=403)

    # 3. Parse JSON body
    try:
        body = req.get_json()
    except Exception:
        return _json_response({"error": "Invalid JSON body"}, status_code=400)

    # 4. Extract fields
    title = (body.get("title") or "").strip()
    description = (body.get("description") or "").strip()
    post_body = (body.get("body") or "").strip()
    published = bool(body.get("published", False))

    # 5. Validate required fields
    if not title or not description:
        return _json_response({"error": "title and description are required"}, status_code=400)

    # 6. Generate slug, build post, validate, serialize, upload to GitHub
    try:
        slug = generate_slug(title)
        post = build_post(
            title=title,
            slug=slug,
            date=datetime.now(timezone.utc).isoformat(),
            description=description,
            body=post_body,
            published=published,
            author_email=requester_email,
        )
        errors = validate_post(post)
        if errors:
            return _json_response({"error": errors[0]}, status_code=400)
        content = serialize_post(post)
        create_file(slug, content, f"post: add {slug}")
        return _json_response({"slug": slug}, status_code=201)
    except requests.exceptions.HTTPError as e:
        if e.response is not None and e.response.status_code == 422:
            return _json_response({"error": "conflict"}, status_code=409)
        return _json_response({"error": "storage error"}, status_code=502)
    except Exception:
        return _json_response({"error": "storage error"}, status_code=502)


@app.route(route="posts/{slug}", methods=["PUT"])
def update_post(req: func.HttpRequest) -> func.HttpResponse:
    """Update an existing post. Requires Google ID token (Authorization: Bearer).
    Preserves original creation date and author_email.
    """
    # 1. Auth gate — must be first (T-06-05)
    try:
        _, requester_email = require_auth(req)
    except ValueError:
        return _unauthorized()

    # 2. Slug validation — checked before any storage access (T-06-06)
    slug = req.route_params.get("slug")
    if not slug or not SLUG_RE.match(slug):
        return _json_response({"error": "invalid slug"}, status_code=400)

    # 3. Parse JSON body
    try:
        body = req.get_json()
    except Exception:
        return _json_response({"error": "Invalid JSON body"}, status_code=400)

    # 4. GET existing file from GitHub — 404 check, SHA, original date, and author (T-06-09, D-14)
    try:
        sha, raw = get_file_sha(slug)
        if sha is None:
            return _json_response({"error": "not found"}, status_code=404)
        existing = parse_post(raw)
        original_date = existing.metadata.get("date")
        stored_author = existing.metadata.get("author_email", "")
    except Exception:
        return _json_response({"error": "storage error"}, status_code=502)

    # 5. Ownership check
    if stored_author:
        if requester_email.lower() != stored_author.lower():
            return _json_response({"error": "Forbidden"}, status_code=403)
    else:
        # Legacy post with no author_email: allowlist members may edit
        if not _check_allowlist(requester_email):
            return _json_response({"error": "Forbidden"}, status_code=403)

    # 6. Extract and validate fields
    title = (body.get("title") or "").strip()
    description = (body.get("description") or "").strip()
    post_body = body.get("body", "")
    published = bool(body.get("published", False))
    if not title or not description:
        return _json_response({"error": "title and description are required"}, status_code=400)

    # 7. Build, validate, serialize, upload to GitHub
    try:
        post = build_post(
            title=title,
            slug=slug,
            date=original_date,   # preserve original creation date
            description=description,
            body=post_body,
            published=published,
            # updated_at omitted → build_post auto-sets to now()
            author_email=stored_author or requester_email,  # stamp requester on first edit of legacy post
        )
        errors = validate_post(post)
        if errors:
            return _json_response({"error": errors[0]}, status_code=400)
        content = serialize_post(post)
        update_file(slug, content, sha, f"post: update {slug}")
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
    except requests.exceptions.HTTPError as e:
        if e.response is not None and e.response.status_code == 422:
            return _json_response({"error": "conflict"}, status_code=409)
        return _json_response({"error": "storage error"}, status_code=502)
    except Exception:
        return _json_response({"error": "storage error"}, status_code=502)


@app.route(route="posts/{slug}", methods=["DELETE"])
def delete_post(req: func.HttpRequest) -> func.HttpResponse:
    """Delete a post by slug. Requires Google ID token (Authorization: Bearer).
    Returns 204 No Content on success.
    """
    # 1. Auth gate — must be first (T-06-05)
    try:
        _, requester_email = require_auth(req)
    except ValueError:
        return _unauthorized()

    # 2. Slug validation (T-06-06)
    slug = req.route_params.get("slug")
    if not slug or not SLUG_RE.match(slug):
        return _json_response({"error": "invalid slug"}, status_code=400)

    # 3. GET SHA and content (GitHub DELETE requires current SHA — D-14), check ownership
    try:
        sha, raw = get_file_sha(slug)
        if sha is None:
            return _json_response({"error": "not found"}, status_code=404)
        existing = parse_post(raw)
        stored_author = existing.metadata.get("author_email", "")
    except requests.exceptions.HTTPError as e:
        if e.response is not None and e.response.status_code == 422:
            return _json_response({"error": "conflict"}, status_code=409)
        return _json_response({"error": "storage error"}, status_code=502)
    except Exception:
        return _json_response({"error": "storage error"}, status_code=502)

    # 4. Ownership check
    if stored_author:
        if requester_email.lower() != stored_author.lower():
            return _json_response({"error": "Forbidden"}, status_code=403)
    else:
        if not _check_allowlist(requester_email):
            return _json_response({"error": "Forbidden"}, status_code=403)

    # 5. DELETE
    try:
        delete_file(slug, sha, f"post: delete {slug}")
    except requests.exceptions.HTTPError as e:
        if e.response is not None and e.response.status_code == 422:
            return _json_response({"error": "conflict"}, status_code=409)
        return _json_response({"error": "storage error"}, status_code=502)
    except Exception:
        return _json_response({"error": "storage error"}, status_code=502)

    # 6. Return 204 No Content — NOT _json_response() (would write a body)
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
        all_posts = list_posts_dir()
        posts = []
        for post in all_posts:
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
        sha, raw = get_file_sha(slug)
        if sha is None:
            return _json_response({"error": "not found"}, status_code=404)
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
    except requests.exceptions.HTTPError:
        return _json_response({"error": "storage error"}, status_code=502)
    except Exception:
        return _json_response({"error": "storage error"}, status_code=500)
