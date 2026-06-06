"""
Slug generation tests for the posts-api slugs module.
GH-01: Slug generation with deduplication via GitHub API probe.
"""
import os
import pytest
from unittest.mock import patch, MagicMock
from slugs import generate_slug

# Minimal env vars required by _gh_headers() and _gh_url()
_ENV = {
    "GITHUB_TOKEN": "test-token",
    "GITHUB_REPO": "owner/repo",
}


def test_basic_slug():
    """generate_slug('Hello World') -> 'hello-world' when GitHub returns 404 (free)."""
    mock_resp = MagicMock()
    mock_resp.status_code = 404
    with patch.dict(os.environ, _ENV), patch("slugs.requests.get", return_value=mock_resp):
        result = generate_slug("Hello World")
    assert result == "hello-world", f"Expected 'hello-world', got {result!r}"


def test_unicode_slug():
    """generate_slug('Café au lait') -> valid non-empty slug when GitHub returns 404."""
    mock_resp = MagicMock()
    mock_resp.status_code = 404
    with patch.dict(os.environ, _ENV), patch("slugs.requests.get", return_value=mock_resp):
        result = generate_slug("Café au lait")
    assert result, f"Expected a non-empty slug, got {result!r}"


def test_dedup_suffix():
    """With 'hello-world' taken (200) and 'hello-world-2' free (404), returns 'hello-world-2'."""
    taken = MagicMock()
    taken.status_code = 200
    free = MagicMock()
    free.status_code = 404
    with patch.dict(os.environ, _ENV), patch("slugs.requests.get", side_effect=[taken, free]):
        result = generate_slug("Hello World")
    assert result == "hello-world-2", f"Expected 'hello-world-2', got {result!r}"


def test_dedup_triple():
    """With 'hello-world' and 'hello-world-2' both taken, returns 'hello-world-3'."""
    taken1 = MagicMock()
    taken1.status_code = 200
    taken2 = MagicMock()
    taken2.status_code = 200
    free = MagicMock()
    free.status_code = 404
    with patch.dict(os.environ, _ENV), patch("slugs.requests.get", side_effect=[taken1, taken2, free]):
        result = generate_slug("Hello World")
    assert result == "hello-world-3", f"Expected 'hello-world-3', got {result!r}"


def test_empty_title_raises():
    """generate_slug with an all-special-char title raises ValueError before any network call."""
    with patch.dict(os.environ, _ENV), patch("slugs.requests.get") as mock_get:
        with pytest.raises(ValueError):
            generate_slug("!!!###")
        mock_get.assert_not_called()
