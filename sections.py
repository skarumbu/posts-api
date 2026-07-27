"""
sections.py — code-level registry of content sections served by posts-api.

Each section maps a URL segment (e.g. "writing", "diary") to the storage
backend it uses, its content shape, and whether it's publicly readable. This
is deliberately a plain dict of dataclasses, not an admin-editable/DB-backed
registry — adding a section later is a one-entry code change, not a runtime
config feature.

`schema` holds the actual schema module for the section (schema_writing or
schema_diary). function_app.py looks up a request's section here to get its
storage backend, content type, visibility, and schema in one place instead of
hardcoding per-section logic.
"""
from dataclasses import dataclass
from typing import Any, Literal

import schema_writing
import schema_diary
from storage.github_storage import GitHubStorage
from storage.blob_storage import BlobStorage

ContentType = Literal["markdown", "blocks"]


@dataclass(frozen=True)
class SectionConfig:
    name: str
    content_type: ContentType
    storage: Any
    public: bool
    schema: Any


SECTIONS: dict[str, SectionConfig] = {
    "writing": SectionConfig(
        name="writing",
        content_type="markdown",
        storage=GitHubStorage(dir_name="posts"),
        public=True,
        schema=schema_writing,
    ),
    "diary": SectionConfig(
        name="diary",
        content_type="blocks",
        storage=BlobStorage(container_env="DIARY_CONTAINER_NAME"),
        public=False,
        schema=schema_diary,
    ),
}
