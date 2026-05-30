"""
slugs.py — Slug generation with deduplication for posts-api.

Exports: generate_slug, get_container_client
"""
import os
from slugify import slugify
from azure.storage.blob import ContainerClient


def get_container_client() -> ContainerClient:
    """
    Build a ContainerClient from environment variables.

    Reads POSTS_STORAGE_CONNECTION_STRING (required) and
    POSTS_CONTAINER_NAME (optional, default 'posts').

    Raises RuntimeError if POSTS_STORAGE_CONNECTION_STRING is empty or unset.
    """
    conn_str = os.environ.get("POSTS_STORAGE_CONNECTION_STRING", "")
    if not conn_str:
        raise RuntimeError(
            "POSTS_STORAGE_CONNECTION_STRING environment variable is not set or empty"
        )
    container = os.environ.get("POSTS_CONTAINER_NAME", "posts")
    return ContainerClient.from_connection_string(conn_str, container_name=container)


def generate_slug(title: str, container_client: ContainerClient) -> str:
    """
    Derive a URL-safe slug from title; append -2/-3 suffix to avoid collisions.

    Uses python-slugify for unicode transliteration and URL-safe output.
    Checks existing blob names with a prefix filter before returning.

    Raises ValueError if title produces an empty slug (e.g., all special chars).
    Returns the slug string without the .md suffix.
    """
    base_slug = slugify(title)
    if not base_slug:
        raise ValueError(f"Title '{title}' produces an empty slug")

    candidate = base_slug
    counter = 2
    existing = {b.name for b in container_client.list_blobs(name_starts_with=base_slug)}

    while f"{candidate}.md" in existing:
        candidate = f"{base_slug}-{counter}"
        counter += 1

    return candidate
