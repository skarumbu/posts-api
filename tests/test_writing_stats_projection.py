"""Golden-fixture tests for writing_stats.projection."""
import glob
import json
import os

import pytest

from writing_stats.projection import (
    PROJECTOR_VERSION, VersionInput, derive_document, derive_event, extract_text, tokenize,
)

FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "fixtures", "writing_stats")
FIXTURES = sorted(glob.glob(os.path.join(FIXTURE_DIR, "*.json")))


def _load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


@pytest.mark.parametrize("path", FIXTURES, ids=[os.path.basename(p) for p in FIXTURES])
def test_golden_fixture(path):
    data = _load(path)
    versions = [VersionInput(**v) for v in data["versions"]]
    actual = [e.to_dict() for e in derive_document(versions)]
    assert actual == data["expected"]


@pytest.mark.parametrize("path", FIXTURES, ids=[os.path.basename(p) for p in FIXTURES])
def test_fixture_projector_version_matches(path):
    """Expected events pin the projector_version that produced them. Changing logic
    means bumping PROJECTOR_VERSION, which fails this until fixtures are regenerated
    (and fixtures edited without a bump fail test_golden_fixture)."""
    for event in _load(path)["expected"]:
        assert event["projector_version"] == PROJECTOR_VERSION


def test_fixture_coverage():
    assert len(FIXTURES) >= 8


def test_first_version_all_added():
    curr = VersionInput("writing", "a", "v1", "2026-01-01T00:00:00Z", "---\ntitle: T\n---\none two three")
    e = derive_event(None, curr)
    assert (e.words_added, e.words_removed, e.total_words, e.is_first_version) == (3, 0, 3, True)


def test_event_to_dict_keys():
    curr = VersionInput("diary", "a", "v1", "t", "{}")
    assert set(derive_event(None, curr).to_dict()) == {
        "section", "slug", "version_id", "at", "words_added", "words_removed",
        "total_words", "is_first_version", "projector_version",
    }


def test_malformed_never_raises():
    for section, content in [("writing", "---\ntitle: [unclosed\n---\nbody"), ("diary", "{nope"),
                             ("diary", "[1,2]"), ("bogus", "x"), ("diary", '{"blocks": 5}')]:
        assert extract_text(section, content) == ""


def test_tokenize_unicode_apostrophe_hyphen():
    assert tokenize("Café don’t well-known -- naïve") == ["Café", "don’t", "well-known", "naïve"]
