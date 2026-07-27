import json
import os
import re
from datetime import datetime, timezone

import azure.functions as func
import requests
from azure.core.exceptions import ResourceExistsError, ResourceModifiedError

from auth import require_auth
from sections import SECTIONS

# ANONYMOUS is intentional: read routes for public sections are public.
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


def _resolve_section(req: func.HttpRequest):
    """Look up the SectionConfig for this request's {section} route param.

    Returns (cfg, None) on success, or (None, error_response) if the section
    is unknown. Checked before auth — section validity isn't a secret.
    """
    section = req.route_params.get("section")
    cfg = SECTIONS.get(section)
    if cfg is None:
        return None, _json_response({"error": "unknown section"}, status_code=404)
    return cfg, None


# ---------------------------------------------------------------------------
# Content-type dispatch — the one place "markdown" (writing) vs "blocks"
# (diary) logic branches, since their field shapes genuinely differ. Everything
# else in this file is section-generic.
# ---------------------------------------------------------------------------

def _parse_item(cfg, raw: str):
    if cfg.content_type == "markdown":
        return cfg.schema.parse_post(raw)
    return cfg.schema.parse_entry(raw)


def _validate_item(cfg, item) -> list:
    if cfg.content_type == "markdown":
        return cfg.schema.validate_post(item)
    return cfg.schema.validate_entry(item)


def _serialize_item(cfg, item) -> str:
    if cfg.content_type == "markdown":
        return cfg.schema.serialize_post(item)
    return cfg.schema.serialize_entry(item)


def _extract_fields(cfg, body: dict) -> dict:
    title = (body.get("title") or "").strip()
    if cfg.content_type == "markdown":
        return {
            "title": title,
            "description": (body.get("description") or "").strip(),
            "body": body.get("body") or "",
            "published": bool(body.get("published", False)),
        }
    blocks = body.get("blocks")
    return {"title": title, "blocks": blocks if isinstance(blocks, list) else []}


def _build_item(cfg, fields: dict, slug: str, date, author_email: str):
    if cfg.content_type == "markdown":
        return cfg.schema.build_post(
            title=fields["title"],
            slug=slug,
            date=date,
            description=fields["description"],
            body=fields["body"],
            published=fields["published"],
            author_email=author_email,
        )
    return cfg.schema.build_entry(
        title=fields["title"],
        slug=slug,
        date=date,
        blocks=fields["blocks"],
        author_email=author_email,
    )


def _shape_item(cfg, item) -> dict:
    date_val = item.metadata.get("date")
    updated_val = item.metadata.get("updatedAt")
    shaped = {
        "title": item.metadata.get("title"),
        "slug": item.metadata.get("slug"),
        "date": date_val.isoformat() if hasattr(date_val, "isoformat") else str(date_val) if date_val is not None else "",
        "updatedAt": updated_val.isoformat() if hasattr(updated_val, "isoformat") else str(updated_val) if updated_val is not None else "",
    }
    if cfg.content_type == "markdown":
        shaped["description"] = item.metadata.get("description")
        shaped["published"] = item.metadata.get("published")
    else:
        shaped["blocks"] = item.metadata.get("blocks", [])
    return shaped


@app.route(route="sections/{section}/items", methods=["POST"])
def create_item(req: func.HttpRequest) -> func.HttpResponse:
    """Create a new item in a section. Requires Google ID token (Authorization: Bearer)."""
    cfg, err = _resolve_section(req)
    if err:
        return err

    # 1. Auth gate — must be first; no body parsing before auth check (T-06-05)
    try:
        _, requester_email = require_auth(req)
    except ValueError:
        return _unauthorized()

    # 2. Allowlist check — only permitted writers can create items
    if not _check_allowlist(requester_email):
        return _json_response({"error": "Forbidden"}, status_code=403)

    # 3. Parse JSON body
    try:
        body = req.get_json()
    except Exception:
        return _json_response({"error": "Invalid JSON body"}, status_code=400)

    # 4. Extract fields (content-type specific)
    fields = _extract_fields(cfg, body)
    if not fields["title"]:
        return _json_response({"error": "title is required"}, status_code=400)

    # 5. Generate slug, build item, validate, serialize, upload to storage
    try:
        slug = cfg.storage.generate_slug(fields["title"])
        item = _build_item(cfg, fields, slug=slug, date=datetime.now(timezone.utc).isoformat(), author_email=requester_email)
        errors = _validate_item(cfg, item)
        if errors:
            return _json_response({"error": errors[0]}, status_code=400)
        content = _serialize_item(cfg, item)
        cfg.storage.create(slug, content, f"{cfg.name}: add {slug}")
        return _json_response({"slug": slug}, status_code=201)
    except (ResourceExistsError, ResourceModifiedError):
        return _json_response({"error": "conflict"}, status_code=409)
    except requests.exceptions.HTTPError as e:
        if e.response is not None and e.response.status_code == 422:
            return _json_response({"error": "conflict"}, status_code=409)
        return _json_response({"error": "storage error"}, status_code=502)
    except Exception:
        return _json_response({"error": "storage error"}, status_code=502)


@app.route(route="sections/{section}/items/{slug}", methods=["PUT"])
def update_item(req: func.HttpRequest) -> func.HttpResponse:
    """Update an existing item. Requires Google ID token (Authorization: Bearer).
    Preserves original creation date and author_email.
    """
    cfg, err = _resolve_section(req)
    if err:
        return err

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

    # 4. GET existing item — 404 check, version token, original date, and author (T-06-09, D-14)
    try:
        version_token, raw = cfg.storage.get(slug)
        if version_token is None:
            return _json_response({"error": "not found"}, status_code=404)
        existing = _parse_item(cfg, raw)
        original_date = existing.metadata.get("date")
        stored_author = existing.metadata.get("author_email", "")
    except Exception:
        return _json_response({"error": "storage error"}, status_code=502)

    # 5. Ownership check
    if stored_author:
        if requester_email.lower() != stored_author.lower():
            return _json_response({"error": "Forbidden"}, status_code=403)
    else:
        # Legacy item with no author_email: allowlist members may edit
        if not _check_allowlist(requester_email):
            return _json_response({"error": "Forbidden"}, status_code=403)

    # 6. Extract and validate fields (content-type specific)
    fields = _extract_fields(cfg, body)
    if not fields["title"]:
        return _json_response({"error": "title is required"}, status_code=400)

    # 7. Build, validate, serialize, upload to storage
    try:
        item = _build_item(
            cfg, fields, slug=slug, date=original_date,
            # updated_at omitted → build auto-sets to now()
            author_email=stored_author or requester_email,  # stamp requester on first edit of legacy item
        )
        errors = _validate_item(cfg, item)
        if errors:
            return _json_response({"error": errors[0]}, status_code=400)
        content = _serialize_item(cfg, item)
        cfg.storage.update(slug, content, version_token, f"{cfg.name}: update {slug}")
        return _json_response(_shape_item(cfg, item), status_code=200)
    except (ResourceExistsError, ResourceModifiedError):
        return _json_response({"error": "conflict"}, status_code=409)
    except requests.exceptions.HTTPError as e:
        if e.response is not None and e.response.status_code == 422:
            return _json_response({"error": "conflict"}, status_code=409)
        return _json_response({"error": "storage error"}, status_code=502)
    except Exception:
        return _json_response({"error": "storage error"}, status_code=502)


@app.route(route="sections/{section}/items/{slug}", methods=["DELETE"])
def delete_item(req: func.HttpRequest) -> func.HttpResponse:
    """Delete an item by slug. Requires Google ID token (Authorization: Bearer).
    Returns 204 No Content on success.
    """
    cfg, err = _resolve_section(req)
    if err:
        return err

    # 1. Auth gate — must be first (T-06-05)
    try:
        _, requester_email = require_auth(req)
    except ValueError:
        return _unauthorized()

    # 2. Slug validation (T-06-06)
    slug = req.route_params.get("slug")
    if not slug or not SLUG_RE.match(slug):
        return _json_response({"error": "invalid slug"}, status_code=400)

    # 3. GET version token and content (storage backends require the current
    #    version token for delete — D-14), check ownership
    try:
        version_token, raw = cfg.storage.get(slug)
        if version_token is None:
            return _json_response({"error": "not found"}, status_code=404)
        existing = _parse_item(cfg, raw)
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
        cfg.storage.delete(slug, version_token, f"{cfg.name}: delete {slug}")
    except (ResourceExistsError, ResourceModifiedError):
        return _json_response({"error": "conflict"}, status_code=409)
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


@app.route(route="sections/{section}/items", methods=["GET"])
def list_items(req: func.HttpRequest) -> func.HttpResponse:
    """Return items in a section as a JSON array sorted newest-first (API-01).

    Public sections (e.g. "writing") return only publicly-visible items,
    with no auth required. Private sections (e.g. "diary") require auth for
    every read, and additionally only ever return items the requester wrote
    themselves — there is no "share with other writers" concept for those.
    """
    cfg, err = _resolve_section(req)
    if err:
        return err

    requester_email = None
    if not cfg.public:
        try:
            _, requester_email = require_auth(req)
        except ValueError:
            return _unauthorized()
        if not _check_allowlist(requester_email):
            return _json_response({"error": "Forbidden"}, status_code=403)

    try:
        raw_items = cfg.storage.list_all()
        items = []
        for raw in raw_items:
            item = _parse_item(cfg, raw)
            if cfg.public:
                if item.metadata.get("published") is not True:
                    continue
            else:
                # Legacy items with no stamped author (pre-dates per-item
                # ownership) remain visible to any allowlisted writer.
                stored_author = item.metadata.get("author_email", "")
                if stored_author and stored_author.lower() != requester_email.lower():
                    continue
            items.append(_shape_item(cfg, item))
        items.sort(key=lambda p: p["date"], reverse=True)
        return _json_response({"items": items})
    except Exception:
        return _json_response({"error": "storage error"}, status_code=500)


@app.route(route="sections/{section}/items/{slug}", methods=["GET"])
def get_item(req: func.HttpRequest) -> func.HttpResponse:
    """Return a single item by slug (API-02). Public sections only return
    publicly-visible items anonymously; private sections require auth and
    only ever return an item to the account that wrote it — a wrong-owner
    request 404s rather than 403ing, so it doesn't confirm the slug exists."""
    cfg, err = _resolve_section(req)
    if err:
        return err

    slug = req.route_params.get("slug")
    if not slug or not SLUG_RE.match(slug):
        return _json_response({"error": "invalid slug"}, status_code=400)

    requester_email = None
    if not cfg.public:
        try:
            _, requester_email = require_auth(req)
        except ValueError:
            return _unauthorized()
        if not _check_allowlist(requester_email):
            return _json_response({"error": "Forbidden"}, status_code=403)

    try:
        version_token, raw = cfg.storage.get(slug)
        if version_token is None:
            return _json_response({"error": "not found"}, status_code=404)
        item = _parse_item(cfg, raw)
        if cfg.public:
            if item.metadata.get("published") is not True:
                return _json_response({"error": "not found"}, status_code=404)
        else:
            stored_author = item.metadata.get("author_email", "")
            if stored_author and stored_author.lower() != requester_email.lower():
                return _json_response({"error": "not found"}, status_code=404)
        response = _shape_item(cfg, item)
        if cfg.content_type == "markdown":
            response["body"] = item.content
        return _json_response(response)
    except requests.exceptions.HTTPError:
        return _json_response({"error": "storage error"}, status_code=502)
    except Exception:
        return _json_response({"error": "storage error"}, status_code=500)
