"""One-time backfill: reads every existing post/diary entry from its
current GitHubStorage/BlobStorage backend and POSTs it as an initial
version into history-api, before posts-api's SECTIONS gets cut over to
HistoryApiStorage. Idempotent — safe to re-run after a partial failure;
use --force to re-import slugs that already exist in history-api.

Usage:
  python scripts/backfill_history.py --section writing
  python scripts/backfill_history.py --section diary --force
"""
import argparse
import os
import sys

import requests

import schema_writing
import schema_diary
from storage.github_storage import GitHubStorage
from storage.blob_storage import BlobStorage
from storage.history_api_storage import _CONTENT_TYPE_BY_SECTION

_PARSERS = {"writing": schema_writing.parse_post, "diary": schema_diary.parse_entry}


def _slug_exists(history_api_url: str, history_api_key: str, section: str, slug: str) -> bool:
    resp = requests.get(f"{history_api_url}/sections/{section}/documents/{slug}", headers={"X-History-Key": history_api_key}, timeout=(5, 30))
    return resp.status_code == 200


def run_backfill(section: str, storage, history_api_url: str, history_api_key: str, force: bool = False) -> dict:
    parse = _PARSERS[section]
    content_type = _CONTENT_TYPE_BY_SECTION[section]
    imported = 0
    skipped = 0
    failed = 0

    for raw in storage.list_all():
        try:
            item = parse(raw)
            slug = item.metadata["slug"]
        except Exception as exc:
            print(f"failed to parse item in {section}: {exc}")
            failed += 1
            continue

        if not force and _slug_exists(history_api_url, history_api_key, section, slug):
            print(f"skip {section}/{slug}: already exists in history-api")
            skipped += 1
            continue

        document_id = f"{section}::{slug}"
        try:
            resp = requests.post(
                f"{history_api_url}/documents/{document_id}/versions",
                headers={"X-History-Key": history_api_key},
                json={"content": raw, "content_type": content_type, "message": "backfill: initial import"},
                timeout=(5, 30),
            )
            resp.raise_for_status()
        except Exception as exc:
            print(f"failed to import {section}/{slug}: {exc}")
            failed += 1
            continue

        print(f"imported {section}/{slug}")
        imported += 1

    return {"imported": imported, "skipped": skipped, "failed": failed}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--section", required=True, choices=["writing", "diary"])
    parser.add_argument("--force", action="store_true", help="re-import slugs that already exist in history-api")
    args = parser.parse_args()

    history_api_url = os.environ["HISTORY_API_URL"]
    history_api_key = os.environ["HISTORY_API_KEY"]

    if args.section == "writing":
        storage = GitHubStorage(dir_name="posts")
    else:
        storage = BlobStorage(container_env="DIARY_CONTAINER_NAME")

    result = run_backfill(args.section, storage, history_api_url, history_api_key, force=args.force)
    print(f"\nDone: {result['imported']} imported, {result['skipped']} skipped, {result['failed']} failed")
    return 1 if result["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
