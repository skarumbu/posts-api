"""
Schema tests for the posts-api "diary" section entry schema.
"""
import pytest
from schema_diary import build_entry, validate_entry, serialize_entry, parse_entry


def test_required_fields_present():
    entry = build_entry(
        title="Today",
        slug="today",
        date="2026-05-30T00:00:00+00:00",
        blocks=[],
    )
    errors = validate_entry(entry)
    assert errors == [], f"Expected no errors but got: {errors}"


def test_updated_at_auto_set():
    entry = build_entry(
        title="Auto Timestamp",
        slug="auto-timestamp",
        date="2026-05-30T00:00:00+00:00",
        blocks=[],
    )
    updated_at = entry.metadata["updatedAt"]
    assert isinstance(updated_at, str) and len(updated_at) > 0
    assert "+" in updated_at or "Z" in updated_at


def test_text_and_sticker_blocks_round_trip():
    entry = build_entry(
        title="Scrapbook Entry",
        slug="scrapbook-entry",
        date="2026-05-30T00:00:00+00:00",
        blocks=[
            {"type": "text", "content": "Hello diary", "style": {"font": "handwriting", "rotation": -2}},
            {"type": "sticker", "emoji": "🌻", "style": {"rotation": 8, "background": "#fff8e1"}},
        ],
    )
    serialized = serialize_entry(entry)
    recovered = parse_entry(serialized)
    assert recovered.metadata["blocks"][0] == {
        "type": "text", "content": "Hello diary", "style": {"font": "handwriting", "rotation": -2},
    }
    assert recovered.metadata["blocks"][1] == {
        "type": "sticker", "emoji": "🌻", "style": {"rotation": 8, "background": "#fff8e1"},
    }


def test_unknown_style_keys_are_stripped():
    entry = build_entry(
        title="T", slug="t", date="2026-05-30T00:00:00+00:00",
        blocks=[{"type": "text", "content": "x", "style": {"font": "cursive", "evil_script": "alert(1)"}}],
    )
    assert entry.metadata["blocks"][0]["style"] == {"font": "cursive"}


def test_invalid_block_type_fails_validation():
    entry = build_entry(
        title="T", slug="t", date="2026-05-30T00:00:00+00:00",
        blocks=[{"type": "text", "content": "ok", "style": {}}],
    )
    # Bypass build_entry's cleaning to simulate a malformed stored entry
    entry.metadata["blocks"].append({"type": "video", "style": {}})
    errors = validate_entry(entry)
    assert any("Invalid block type" in e for e in errors)


def test_no_published_field():
    """Diary entries have no published/visibility field — validated separately from writing."""
    entry = build_entry(title="T", slug="t", date="2026-05-30T00:00:00+00:00", blocks=[])
    assert "published" not in entry.metadata
