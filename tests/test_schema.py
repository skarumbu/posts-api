"""
Schema tests for the posts-api post frontmatter schema.
STOR-02: Frontmatter schema defined and validated.
"""
import pytest
from schema import build_post, validate_post, serialize_post, parse_post


def test_required_fields_present():
    """build_post with all args -> validate_post returns no errors."""
    post = build_post(
        title="T",
        slug="t",
        date="2026-05-30T00:00:00+00:00",
        description="d",
        body="body",
    )
    errors = validate_post(post)
    assert errors == [], f"Expected no errors but got: {errors}"


def test_title_with_colon_round_trips():
    """Title containing colon must survive serialize -> parse as a string, not a dict."""
    post = build_post(
        title="What I Built: A Summary",
        slug="what-i-built-a-summary",
        date="2026-05-30T00:00:00+00:00",
        description="desc",
        body="body text",
    )
    serialized = serialize_post(post)
    recovered = parse_post(serialized)
    assert isinstance(recovered["title"], str), (
        f"Expected title to be str after round-trip, got {type(recovered['title'])}: {recovered['title']!r}"
    )
    assert recovered["title"] == "What I Built: A Summary"


def test_published_is_bool():
    """build_post with published=False -> post['published'] is exactly False (bool, not str)."""
    post = build_post(
        title="Draft Post",
        slug="draft-post",
        date="2026-05-30T00:00:00+00:00",
        description="desc",
        body="body",
        published=False,
    )
    assert post["published"] is False, (
        f"Expected False (bool) but got {post['published']!r} ({type(post['published']).__name__})"
    )


def test_frontmatter_round_trip():
    """serialize_post produces a '---' prefixed string; parse_post recovers all 6 fields."""
    post = build_post(
        title="Round Trip Test",
        slug="round-trip-test",
        date="2026-05-30T00:00:00+00:00",
        description="A round-trip test post",
        body="The body content.",
        published=True,
    )
    serialized = serialize_post(post)
    assert serialized.startswith("---"), "Serialized post must start with YAML front-matter '---'"

    recovered = parse_post(serialized)
    assert recovered["title"] == "Round Trip Test"
    assert recovered["slug"] == "round-trip-test"
    assert recovered["date"] == "2026-05-30T00:00:00+00:00"
    assert recovered["description"] == "A round-trip test post"
    assert recovered["published"] is True
    assert "updatedAt" in recovered.metadata


def test_updated_at_auto_set():
    """build_post without updated_at kwarg -> post['updatedAt'] is a non-empty ISO 8601 string."""
    post = build_post(
        title="Auto Timestamp",
        slug="auto-timestamp",
        date="2026-05-30T00:00:00+00:00",
        description="desc",
        body="body",
    )
    updated_at = post["updatedAt"]
    assert isinstance(updated_at, str) and len(updated_at) > 0, (
        f"updatedAt must be a non-empty string, got {updated_at!r}"
    )
    # Must contain timezone info ('+' or 'Z')
    assert "+" in updated_at or "Z" in updated_at, (
        f"updatedAt must contain timezone info ('+' or 'Z'), got {updated_at!r}"
    )
