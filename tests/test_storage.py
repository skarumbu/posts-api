"""
Storage integration tests for the posts-api blob storage layer.
STOR-01: Container accepts .md writes and reads via Azurite.

Requires Azurite running on default port (10000) before running.
Uses the container_client fixture from conftest.py (creates/destroys a test container).
"""
import pytest
from schema import build_post, serialize_post, parse_post


def test_upload_sample_post(container_client):
    """build_post -> serialize_post -> upload blob -> blob_client.exists() returns True."""
    post = build_post(
        title="Test Post",
        slug="test-post",
        date="2026-05-30T00:00:00+00:00",
        description="A description",
        body="Body text",
    )
    md_content = serialize_post(post)
    container_client.upload_blob("test-post.md", md_content.encode("utf-8"), overwrite=True)

    blob_client = container_client.get_blob_client("test-post.md")
    assert blob_client.exists(), "Blob 'test-post.md' should exist after upload"


def test_download_sample_post(container_client):
    """Upload then download 'test-post.md' -> parse_post recovers title and slug correctly."""
    post = build_post(
        title="Test Post",
        slug="test-post",
        date="2026-05-30T00:00:00+00:00",
        description="A description",
        body="Body text",
    )
    md_content = serialize_post(post)
    container_client.upload_blob("test-post.md", md_content.encode("utf-8"), overwrite=True)

    blob_client = container_client.get_blob_client("test-post.md")
    raw = blob_client.download_blob().readall().decode("utf-8")
    recovered = parse_post(raw)

    assert recovered["title"] == "Test Post", (
        f"Expected title 'Test Post', got {recovered['title']!r}"
    )
    assert recovered["slug"] == "test-post", (
        f"Expected slug 'test-post', got {recovered['slug']!r}"
    )


def test_container_exists(container_client):
    """The container_client fixture creates the container; get_container_properties() succeeds."""
    props = container_client.get_container_properties()
    assert props is not None, "get_container_properties() should return properties, not None"
