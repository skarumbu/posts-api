"""
sections.py — code-level registry of content sections served by posts-api.

Each section maps a URL segment (e.g. "writing") to the storage backend it
uses and whether it's publicly readable. This is deliberately a plain dict of
dataclasses, not an admin-editable/DB-backed registry — adding a section
later is a one-entry code change, not a runtime config feature.

`schema` holds the actual schema module for the section (e.g. schema_writing),
exposing build_post/validate_post/serialize_post/parse_post. function_app.py
looks up a request's section here to get its storage backend, visibility, and
schema in one place instead of hardcoding "posts"-specific logic.
"""
from dataclasses import dataclass
from typing import Any

import schema_writing
from storage.github_storage import GitHubStorage


@dataclass(frozen=True)
class SectionConfig:
    name: str
    storage: Any
    public: bool
    schema: Any


SECTIONS: dict[str, SectionConfig] = {
    "writing": SectionConfig(
        name="writing",
        storage=GitHubStorage(dir_name="posts"),
        public=True,
        schema=schema_writing,
    ),
}
