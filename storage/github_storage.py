"""
github_storage.py — GitHub Contents API storage backend for posts-api.

Stores items as files in a directory of a GitHub repo. Generalized from the
original slugs.py module: `dir_name` is now a constructor argument instead of
a hardcoded "posts" constant, and the version token is named generically
(`version_token`, GitHub's SHA) so this backend and BlobStorage (ETag-based)
can share a common calling convention from function_app.py.
"""
import os
import base64
import requests
from slugify import slugify


class GitHubStorage:
    """Stores items as .md files under `{dir_name}/{slug}.md` in a GitHub repo."""

    def __init__(self, dir_name: str):
        self.dir_name = dir_name

    def _headers(self) -> dict:
        token = os.environ["GITHUB_TOKEN"]
        return {
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "posts-api/1.0",
        }

    def _file_url(self, slug: str) -> str:
        repo = os.environ["GITHUB_REPO"]
        return f"https://api.github.com/repos/{repo}/contents/{self.dir_name}/{slug}.md"

    def _dir_url(self) -> str:
        repo = os.environ["GITHUB_REPO"]
        return f"https://api.github.com/repos/{repo}/contents/{self.dir_name}"

    def get(self, slug: str) -> tuple[str | None, str | None]:
        """Fetch (version_token, raw_content) for a file. Returns (None, None) on 404."""
        resp = requests.get(self._file_url(slug), headers=self._headers())
        if resp.status_code == 404:
            return None, None
        if resp.status_code >= 500:
            raise RuntimeError(f"GitHub error: {resp.status_code}")
        resp.raise_for_status()
        data = resp.json()
        content_b64 = data["content"].replace("\n", "")
        raw = base64.b64decode(content_b64).decode("utf-8")
        return data["sha"], raw

    def list_all(self) -> list[str]:
        """Return the raw contents (str) of every file in the directory. [] if directory is missing."""
        resp = requests.get(self._dir_url(), headers=self._headers())
        if resp.status_code == 404:
            return []
        resp.raise_for_status()
        file_entries = resp.json()
        contents = []
        for entry in file_entries:
            if not entry["name"].endswith(".md"):
                continue
            file_resp = requests.get(entry["url"], headers=self._headers())
            if file_resp.status_code != 200:
                continue
            content_b64 = file_resp.json()["content"].replace("\n", "")
            contents.append(base64.b64decode(content_b64).decode("utf-8"))
        return contents

    def create(self, slug: str, content: str, message: str) -> None:
        content_b64 = base64.b64encode(content.encode("utf-8")).decode("ascii")
        body = {"message": message, "content": content_b64}
        resp = requests.put(self._file_url(slug), headers=self._headers(), json=body)
        if resp.status_code not in (200, 201):
            resp.raise_for_status()

    def update(self, slug: str, content: str, version_token: str, message: str) -> None:
        content_b64 = base64.b64encode(content.encode("utf-8")).decode("ascii")
        body = {"message": message, "content": content_b64, "sha": version_token}
        resp = requests.put(self._file_url(slug), headers=self._headers(), json=body)
        resp.raise_for_status()

    def delete(self, slug: str, version_token: str, message: str) -> None:
        body = {"message": message, "sha": version_token}
        resp = requests.delete(self._file_url(slug), headers=self._headers(), json=body)
        resp.raise_for_status()

    def slug_exists(self, slug: str) -> bool:
        resp = requests.get(self._file_url(slug), headers=self._headers())
        if resp.status_code == 404:
            return False
        if resp.status_code == 200:
            return True
        resp.raise_for_status()
        return False

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
