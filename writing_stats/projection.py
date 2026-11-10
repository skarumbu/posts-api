"""
writing_stats/projection.py — pure projection of document versions into WritingEvents.

Pure: no clock, environment, network or randomness. Everything derives from the
arguments, so replaying the same versions always yields the same events.

Bump PROJECTOR_VERSION on ANY change to extraction, tokenizing or diffing.

Malformed content: if a version's stored content cannot be parsed (bad
frontmatter YAML, invalid diary JSON, wrong JSON shape, unknown section), its
text is treated as "" and an event is still emitted. extract_text never raises.
"""
import difflib
import re
from dataclasses import dataclass
from typing import Optional

import schema_diary
import schema_writing

PROJECTOR_VERSION = 1

_TOKEN_RE = re.compile(r"[\w][\w'’-]*")

_FENCED_CODE_RE = re.compile(r"^[ \t]*(`{3,}|~{3,}).*?(?:^[ \t]*\1[`~]*[ \t]*$|\Z)", re.S | re.M)
_INLINE_CODE_RE = re.compile(r"(`+)(?!`).+?(?<!`)\1(?!`)", re.S)
_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_REF_LINK_RE = re.compile(r"!?\[([^\]]*)\]\[[^\]]*\]")
_REF_DEF_RE = re.compile(r"^[ \t]{0,3}\[[^\]]+\]:[ \t]*\S+.*$", re.M)
_HTML_TAG_RE = re.compile(r"<[^>\n]+>")
_LINE_MARKER_RE = re.compile(r"^[ \t]{0,3}(?:#{1,6}|>+|[-*+]|\d+[.)])[ \t]+", re.M)
_EMPHASIS_RE = re.compile(r"[*~]+|(?<!\w)_+|_+(?!\w)")


@dataclass(frozen=True)
class VersionInput:
    section: str
    slug: str
    version_id: str
    created_at: str  # history-api's timestamp, never now()
    content: str     # raw stored content (frontmatter md or diary JSON)


@dataclass(frozen=True)
class WritingEvent:
    section: str
    slug: str
    version_id: str
    at: str
    words_added: int
    words_removed: int
    total_words: int
    is_first_version: bool
    projector_version: int

    def to_dict(self) -> dict:
        return {
            "section": self.section,
            "slug": self.slug,
            "version_id": self.version_id,
            "at": self.at,
            "words_added": self.words_added,
            "words_removed": self.words_removed,
            "total_words": self.total_words,
            "is_first_version": self.is_first_version,
            "projector_version": self.projector_version,
        }


def _strip_markdown(body: str) -> str:
    text = _FENCED_CODE_RE.sub("\n", body)
    text = _INLINE_CODE_RE.sub(" ", text)
    text = _IMAGE_RE.sub(r" \1 ", text)
    text = _LINK_RE.sub(r" \1 ", text)
    text = _REF_LINK_RE.sub(r" \1 ", text)
    text = _REF_DEF_RE.sub("", text)
    text = _HTML_TAG_RE.sub(" ", text)
    text = _LINE_MARKER_RE.sub("", text)
    text = _EMPHASIS_RE.sub(" ", text)
    return text


def _writing_text(content: str) -> str:
    post = schema_writing.parse_post(content)
    return _strip_markdown(post.content or "")


def _diary_text(content: str) -> str:
    entry = schema_diary.parse_entry(content)
    blocks = entry.metadata.get("blocks") if isinstance(entry.metadata, dict) else None
    if not isinstance(blocks, list):
        return ""
    parts = []
    for block in blocks:
        if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("content"), str):
            parts.append(block["content"])
    return "\n".join(parts)


def extract_text(section: str, content: str) -> str:
    """Countable text for a version. Never raises; malformed content -> ""."""
    try:
        if section == "writing":
            return _writing_text(content)
        if section == "diary":
            return _diary_text(content)
    except Exception:
        return ""
    return ""


def tokenize(text: str) -> list:
    return _TOKEN_RE.findall(text)


def _tokens(version: VersionInput) -> list:
    return tokenize(extract_text(version.section, version.content))


def derive_event(prev: Optional[VersionInput], curr: VersionInput) -> WritingEvent:
    curr_tokens = _tokens(curr)
    if prev is None:
        added, removed = len(curr_tokens), 0
    else:
        prev_tokens = _tokens(prev)
        added = removed = 0
        matcher = difflib.SequenceMatcher(None, prev_tokens, curr_tokens, autojunk=False)
        for op, i1, i2, j1, j2 in matcher.get_opcodes():
            if op in ("insert", "replace"):
                added += j2 - j1
            if op in ("delete", "replace"):
                removed += i2 - i1
    return WritingEvent(
        section=curr.section,
        slug=curr.slug,
        version_id=curr.version_id,
        at=curr.created_at,
        words_added=added,
        words_removed=removed,
        total_words=len(curr_tokens),
        is_first_version=prev is None,
        projector_version=PROJECTOR_VERSION,
    )


def derive_document(versions: list) -> list:
    """Sort by (created_at, version_id), then derive_event over consecutive pairs."""
    ordered = sorted(versions, key=lambda v: (v.created_at, v.version_id))
    events = []
    prev = None
    for v in ordered:
        events.append(derive_event(prev, v))
        prev = v
    return events
