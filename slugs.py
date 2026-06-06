"""
slugs.py — GitHub API helper layer for posts-api.

Exports: _gh_headers, _gh_url, _gh_dir_url, get_file_sha, list_posts_dir,
         create_file, update_file, delete_file, generate_slug
"""
import os
import base64
import requests
from slugify import slugify

POSTS_DIR = "posts"


def _gh_headers() -> dict:
    """Return GitHub API request headers using GITHUB_TOKEN from env."""
    token = os.environ["GITHUB_TOKEN"]
    return {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "posts-api/1.0",
    }


def _gh_url(slug: str) -> str:
    """Return the GitHub Contents API URL for a single post file."""
    repo = os.environ["GITHUB_REPO"]
    return f"https://api.github.com/repos/{repo}/contents/{POSTS_DIR}/{slug}.md"


def _gh_dir_url() -> str:
    """Return the GitHub Contents API URL for the posts directory."""
    repo = os.environ["GITHUB_REPO"]
    return f"https://api.github.com/repos/{repo}/contents/{POSTS_DIR}"


def get_file_sha(slug: str) -> tuple[str | None, str | None]:
    """
    Fetch the SHA and raw content for a post file from GitHub.

    Returns (sha, raw_content) on 200, (None, None) on 404.
    Raises RuntimeError on 5xx. Raises HTTPError on other non-200 statuses.
    """
    resp = requests.get(_gh_url(slug), headers=_gh_headers())
    if resp.status_code == 404:
        return None, None
    if resp.status_code >= 500:
        raise RuntimeError(f"GitHub error: {resp.status_code}")
    resp.raise_for_status()
    data = resp.json()
    content_b64 = data["content"].replace("\n", "")  # strip GitHub's embedded newlines
    raw = base64.b64decode(content_b64).decode("utf-8")
    return data["sha"], raw


def list_posts_dir() -> list:
    """
    List all .md files in the posts directory and return parsed Post objects.

    Returns [] if the directory does not exist (404).
    Raises HTTPError on other non-200 statuses.
    """
    resp = requests.get(_gh_dir_url(), headers=_gh_headers())
    if resp.status_code == 404:
        return []
    resp.raise_for_status()
    file_entries = resp.json()
    posts = []
    for entry in file_entries:
        if not entry["name"].endswith(".md"):
            continue
        file_resp = requests.get(entry["url"], headers=_gh_headers())
        if file_resp.status_code != 200:
            continue
        content_b64 = file_resp.json()["content"].replace("\n", "")
        raw = base64.b64decode(content_b64).decode("utf-8")
        from schema import parse_post
        posts.append(parse_post(raw))
    return posts


def create_file(slug: str, content_str: str, commit_message: str) -> None:
    """Create a new post file in GitHub via PUT."""
    content_b64 = base64.b64encode(content_str.encode("utf-8")).decode("ascii")
    body = {"message": commit_message, "content": content_b64}
    resp = requests.put(_gh_url(slug), headers=_gh_headers(), json=body)
    if resp.status_code not in (200, 201):
        resp.raise_for_status()


def update_file(slug: str, content_str: str, sha: str, commit_message: str) -> None:
    """Update an existing post file in GitHub via PUT, providing its current SHA."""
    content_b64 = base64.b64encode(content_str.encode("utf-8")).decode("ascii")
    body = {"message": commit_message, "content": content_b64, "sha": sha}
    resp = requests.put(_gh_url(slug), headers=_gh_headers(), json=body)
    resp.raise_for_status()


def delete_file(slug: str, sha: str, commit_message: str) -> None:
    """Delete a post file from GitHub via DELETE, providing its current SHA."""
    body = {"message": commit_message, "sha": sha}
    resp = requests.delete(_gh_url(slug), headers=_gh_headers(), json=body)
    resp.raise_for_status()


def generate_slug(title: str) -> str:
    """
    Derive a URL-safe slug from title; append -2/-3 suffix to avoid collisions.

    Probes GitHub API with GET; 404 means the slug is free. 200 means taken.
    Raises ValueError if title produces an empty slug (e.g., all special chars).
    Returns the slug string without the .md suffix.
    """
    base_slug = slugify(title)
    if not base_slug:
        raise ValueError(f"Title '{title}' produces an empty slug")
    candidate = base_slug
    counter = 2
    while True:
        resp = requests.get(_gh_url(candidate), headers=_gh_headers())
        if resp.status_code == 404:
            return candidate
        if resp.status_code == 200:
            candidate = f"{base_slug}-{counter}"
            counter += 1
        else:
            resp.raise_for_status()
