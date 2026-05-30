"""
Slug generation tests for the posts-api slugs module.
STOR-03: Slug generation with deduplication.
"""
import pytest
from slugs import generate_slug


def test_basic_slug(container_client):
    """generate_slug('Hello World', client) -> 'hello-world' with empty container."""
    result = generate_slug("Hello World", container_client)
    assert result == "hello-world", f"Expected 'hello-world', got {result!r}"


def test_unicode_slug(container_client):
    """generate_slug('Café Notes', client) -> 'cafe-notes' (unicode transliteration)."""
    result = generate_slug("Café Notes", container_client)
    assert result == "cafe-notes", f"Expected 'cafe-notes', got {result!r}"


def test_dedup_suffix(container_client):
    """With 'hello-world.md' already existing, generate_slug returns 'hello-world-2'."""
    container_client.upload_blob("hello-world.md", b"content", overwrite=True)
    result = generate_slug("Hello World", container_client)
    assert result == "hello-world-2", f"Expected 'hello-world-2', got {result!r}"


def test_dedup_triple(container_client):
    """With 'hello-world.md' and 'hello-world-2.md' existing, generate_slug returns 'hello-world-3'."""
    container_client.upload_blob("hello-world.md", b"content", overwrite=True)
    container_client.upload_blob("hello-world-2.md", b"content", overwrite=True)
    result = generate_slug("Hello World", container_client)
    assert result == "hello-world-3", f"Expected 'hello-world-3', got {result!r}"


def test_empty_title_raises(container_client):
    """generate_slug with an all-special-char title raises ValueError."""
    with pytest.raises(ValueError):
        generate_slug("!!!###", container_client)
