"""
schema_diary.py — JSON entry schema for the "diary" section of posts-api.

Diary entries are private-only, block-based content (a scrapbook of stacked
text/sticker blocks), not frontmatter+markdown like "writing". There is no
`published` field — the whole diary section is gated private via
SectionConfig.public, not a per-item visibility flag.

DiaryEntry exposes a `.metadata` dict, mirroring frontmatter.Post's
duck-typed interface, so function_app.py can treat items from either
section uniformly.
"""
import json
from datetime import datetime, timezone
from typing import Optional

REQUIRED_FIELDS: set = {"title", "slug", "date", "updatedAt"}
BLOCK_TYPES = {"text", "sticker"}
STYLE_KEYS = {"font", "color", "rotation", "background"}


class DiaryEntry:
    def __init__(self, metadata: dict):
        self.metadata = metadata


def _clean_block(block: dict) -> dict:
    """Whitelist known style keys but keep unknown block fields out — avoids
    backend changes every time the frontend adds a new style knob, while
    still enforcing a strict block `type`."""
    block_type = block.get("type")
    style = block.get("style") if isinstance(block.get("style"), dict) else {}
    cleaned = {"type": block_type, "style": {k: v for k, v in style.items() if k in STYLE_KEYS}}
    if block_type == "text":
        cleaned["content"] = block.get("content", "")
    elif block_type == "sticker":
        cleaned["emoji"] = block.get("emoji", "")
    return cleaned


def build_entry(
    title: str,
    slug: str,
    date: str,
    blocks: list,
    updated_at: Optional[str] = None,
    author_email: Optional[str] = None,
) -> DiaryEntry:
    """Build a DiaryEntry with all required fields set. Sets updatedAt to
    datetime.now(timezone.utc).isoformat() if updated_at is not provided."""
    metadata = {
        "title": title,
        "slug": slug,
        "date": date,
        "updatedAt": updated_at if updated_at is not None else datetime.now(timezone.utc).isoformat(),
        "blocks": [_clean_block(b) for b in blocks if isinstance(b, dict)],
    }
    if author_email:
        metadata["author_email"] = author_email
    return DiaryEntry(metadata)


def validate_entry(entry: DiaryEntry) -> list:
    """Validate a DiaryEntry. Returns a list of error strings — [] means valid.
    Checks: all REQUIRED_FIELDS present; title is str; blocks is a list of
    entries with a recognized `type`."""
    errors = []
    for field in REQUIRED_FIELDS:
        if field not in entry.metadata:
            errors.append(f"Missing required field: {field}")
    if "title" in entry.metadata and not isinstance(entry.metadata["title"], str):
        errors.append("title must be a string")
    blocks = entry.metadata.get("blocks")
    if not isinstance(blocks, list):
        errors.append("blocks must be a list")
    else:
        for block in blocks:
            if not isinstance(block, dict) or block.get("type") not in BLOCK_TYPES:
                errors.append(f"Invalid block type: {block.get('type') if isinstance(block, dict) else block!r}")
                break
    return errors


def serialize_entry(entry: DiaryEntry) -> str:
    """Serialize a DiaryEntry to a JSON string."""
    return json.dumps(entry.metadata)


def parse_entry(content: str) -> DiaryEntry:
    """Parse a JSON string into a DiaryEntry."""
    return DiaryEntry(json.loads(content))
