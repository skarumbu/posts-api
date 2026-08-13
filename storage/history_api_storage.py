"""HistoryApiStorage — history-api-backed storage for posts-api's writing
and diary sections. Same protocol as GitHubStorage/BlobStorage: get,
list_all, create, update, delete, slug_exists, generate_slug. content_type
sent to history-api is fixed per-section (markdown for writing, json for
diary) rather than derived per-call — see the design doc's content_type
mapping.
"""
import os
import requests
from slugify import slugify

from storage.errors import StorageConflictError

_CONTENT_TYPE_BY_SECTION = {"writing": "markdown", "diary": "json"}


class HistoryApiStorage:
    def __init__(self, section: str):
        self.section = section
        self.content_type = _CONTENT_TYPE_BY_SECTION[section]

    def _base_url(self) -> str:
        return os.environ["HISTORY_API_URL"]

    def _headers(self) -> dict:
        return {"X-History-Key": os.environ["HISTORY_API_KEY"], "Content-Type": "application/json"}

    def _document_id(self, slug: str) -> str:
        return f"{self.section}::{slug}"

    def get(self, slug: str) -> tuple[str | None, str | None]:
        resp = requests.get(f"{self._base_url()}/sections/{self.section}/documents/{slug}", headers=self._headers())
        if resp.status_code == 404:
            return None, None
        resp.raise_for_status()
        data = resp.json()
        return data.get("version_id"), data.get("content")

    def list_all(self) -> list[str]:
        resp = requests.get(f"{self._base_url()}/sections/{self.section}/documents", headers=self._headers())
        resp.raise_for_status()
        documents = resp.json()["documents"]
        contents = []
        for doc in documents:
            version_id, content = self.get(doc["slug"])
            if content is not None:
                contents.append(content)
        return contents

    def create(self, slug: str, content: str, message: str) -> None:
        resp = requests.post(
            f"{self._base_url()}/documents/{self._document_id(slug)}/versions",
            headers=self._headers(),
            json={"content": content, "content_type": self.content_type, "message": message},
        )
        if resp.status_code == 409:
            raise StorageConflictError(f"Conflict creating {slug}")
        resp.raise_for_status()

    def update(self, slug: str, content: str, version_token: str, message: str) -> None:
        resp = requests.post(
            f"{self._base_url()}/documents/{self._document_id(slug)}/versions",
            headers=self._headers(),
            json={"content": content, "content_type": self.content_type, "message": message, "expected_version_id": version_token},
        )
        if resp.status_code == 409:
            raise StorageConflictError(f"Conflict updating {slug}")
        resp.raise_for_status()

    def delete(self, slug: str, version_token: str, message: str) -> None:
        resp = requests.delete(
            f"{self._base_url()}/sections/{self.section}/documents/{slug}",
            headers=self._headers(),
            json={"expected_version_id": version_token},
        )
        if resp.status_code == 409:
            raise StorageConflictError(f"Conflict deleting {slug}")
        if resp.status_code not in (204, 404):
            resp.raise_for_status()

    def slug_exists(self, slug: str) -> bool:
        resp = requests.get(f"{self._base_url()}/sections/{self.section}/documents/{slug}", headers=self._headers())
        if resp.status_code == 404:
            return False
        resp.raise_for_status()
        return True

    def generate_slug(self, title: str) -> str:
        base_slug = slugify(title)
        if not base_slug:
            raise ValueError(f"Title '{title}' produces an empty slug")
        candidate = base_slug
        counter = 2
        while self.slug_exists(candidate):
            candidate = f"{base_slug}-{counter}"
            counter += 1
        return candidate
