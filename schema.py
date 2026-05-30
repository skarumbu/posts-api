"""
schema.py — Post frontmatter schema utilities for posts-api.

Exports: build_post, validate_post, serialize_post, parse_post, REQUIRED_FIELDS
"""
import frontmatter
from datetime import datetime, timezone
from typing import Optional

REQUIRED_FIELDS: set = {"title", "slug", "date", "published", "description", "updatedAt"}


def build_post(
    title: str,
    slug: str,
    date: str,
    description: str,
    body: str,
    published: bool = False,
    updated_at: Optional[str] = None,
) -> frontmatter.Post:
    """
    Build a frontmatter.Post object with all 6 required fields set.

    Sets updatedAt to datetime.now(timezone.utc).isoformat() if updated_at is not provided.
    All fields are set via post["field"] = value (never f-string YAML) so python-frontmatter
    handles YAML serialization with proper quoting (e.g., titles containing colons).
    """
    post = frontmatter.Post(body)
    post["title"] = title
    post["slug"] = slug
    post["date"] = date
    post["published"] = published
    post["description"] = description
    post["updatedAt"] = updated_at if updated_at is not None else datetime.now(timezone.utc).isoformat()
    return post


def validate_post(post: frontmatter.Post) -> list:
    """
    Validate a frontmatter.Post object against the required schema.

    Returns a list of error strings. An empty list means the post is valid.
    Checks: all REQUIRED_FIELDS present; title is str; published is bool.
    """
    errors = []
    for field in REQUIRED_FIELDS:
        if field not in post.metadata:
            errors.append(f"Missing required field: {field}")
    if "title" in post.metadata and not isinstance(post.metadata["title"], str):
        errors.append("title must be a string")
    if "published" in post.metadata and not isinstance(post.metadata["published"], bool):
        errors.append("published must be a boolean")
    return errors


def serialize_post(post: frontmatter.Post) -> str:
    """
    Serialize a frontmatter.Post to a YAML-frontmatter markdown string.

    Returns frontmatter.dumps(post) — the library handles all YAML escaping.
    """
    return frontmatter.dumps(post)


def parse_post(content: str) -> frontmatter.Post:
    """
    Parse a YAML-frontmatter markdown string into a frontmatter.Post object.

    Returns frontmatter.loads(content).
    """
    return frontmatter.loads(content)
