"""
blob_storage.py — Azure Blob Storage backend for posts-api.

Stores items as `{slug}.json` blobs in a private container. Counterpart to
GitHubStorage: `version_token` here is the blob's ETag, used for optimistic
concurrency on update/delete (mirroring GitHub's SHA-based convention) via
the `etag`/`match_condition` kwargs supported across azure-storage-blob's
write operations.
"""
import os

from azure.core.exceptions import ResourceNotFoundError
from azure.core import MatchConditions
from azure.storage.blob import BlobServiceClient
from slugify import slugify


class BlobStorage:
    """Stores items as `{slug}.json` blobs in a private Azure Blob container."""

    def __init__(self, container_env: str):
        self.container_env = container_env

    def _container_client(self):
        conn_str = os.environ["POSTS_STORAGE_CONNECTION_STRING"]
        container_name = os.environ[self.container_env]
        service = BlobServiceClient.from_connection_string(conn_str)
        return service.get_container_client(container_name)

    def get(self, slug: str) -> tuple[str | None, str | None]:
        """Fetch (version_token, raw_content) for a blob. Returns (None, None) if missing."""
        blob = self._container_client().get_blob_client(f"{slug}.json")
        try:
            downloaded = blob.download_blob()
        except ResourceNotFoundError:
            return None, None
        content = downloaded.readall().decode("utf-8")
        return downloaded.properties.etag, content

    def list_all(self) -> list[str]:
        """Return the raw contents (str) of every blob in the container."""
        container = self._container_client()
        contents = []
        for item in container.list_blobs():
            blob = container.get_blob_client(item.name)
            try:
                downloaded = blob.download_blob()
            except ResourceNotFoundError:
                continue
            contents.append(downloaded.readall().decode("utf-8"))
        return contents

    def create(self, slug: str, content: str, message: str) -> None:
        """Create a new blob. Raises azure.core.exceptions.ResourceExistsError on conflict."""
        blob = self._container_client().get_blob_client(f"{slug}.json")
        blob.upload_blob(content.encode("utf-8"), overwrite=False)

    def update(self, slug: str, content: str, version_token: str, message: str) -> None:
        """Update an existing blob, providing its current ETag for optimistic concurrency.
        Raises azure.core.exceptions.ResourceModifiedError if the ETag doesn't match."""
        blob = self._container_client().get_blob_client(f"{slug}.json")
        blob.upload_blob(
            content.encode("utf-8"),
            overwrite=True,
            etag=version_token,
            match_condition=MatchConditions.IfNotModified,
        )

    def delete(self, slug: str, version_token: str, message: str) -> None:
        blob = self._container_client().get_blob_client(f"{slug}.json")
        blob.delete_blob(etag=version_token, match_condition=MatchConditions.IfNotModified)

    def slug_exists(self, slug: str) -> bool:
        return self._container_client().get_blob_client(f"{slug}.json").exists()

    def generate_slug(self, title: str) -> str:
        """Derive a URL-safe slug from title; append -2/-3 suffix to avoid collisions."""
        base_slug = slugify(title)
        if not base_slug:
            raise ValueError(f"Title '{title}' produces an empty slug")
        candidate = base_slug
        counter = 2
        while self.slug_exists(candidate):
            candidate = f"{base_slug}-{counter}"
            counter += 1
        return candidate
