import json
import os
import re

import azure.functions as func
from azure.core.exceptions import ResourceNotFoundError

from schema import parse_post
from slugs import get_container_client

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
