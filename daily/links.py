"""
links.py — curated daily links backed by DailyLinks.

One config row (RowKey "config") holding the link definitions, plus one row
per day (RowKey = that day's ISO date) holding that day's opened-link flags,
in the same table, distinguished by RowKey. Runtime-editable by design: the
task's extensibility bar ("add a link without touching code") is a data
edit, not a redeploy.
"""
import json
from dataclasses import dataclass
from datetime import date

from azure.core.exceptions import ResourceNotFoundError

from daily.table_client import get_table_client

TABLE_NAME = "DailyLinks"
CONFIG_ROW_KEY = "config"


@dataclass
class LinkDef:
    id: str
    label: str
    url: str


def get_config(user_email: str) -> list[LinkDef]:
    """A missing config row returns the empty list — 404-shaped, not an
    error — the frontend ships its own first-run default via a one-time
    seed call to set_config."""
    table = get_table_client(TABLE_NAME)
    try:
        entity = table.get_entity(partition_key=user_email, row_key=CONFIG_ROW_KEY)
    except ResourceNotFoundError:
        return []
    return [LinkDef(**d) for d in json.loads(entity["links"])]


def set_config(user_email: str, links: list[LinkDef]) -> None:
    table = get_table_client(TABLE_NAME)
    table.upsert_entity({
        "PartitionKey": user_email,
        "RowKey": CONFIG_ROW_KEY,
        "links": json.dumps([link.__dict__ for link in links]),
    })


def get_opened_today(user_email: str, day: date) -> set[str]:
    table = get_table_client(TABLE_NAME)
    try:
        entity = table.get_entity(partition_key=user_email, row_key=day.isoformat())
    except ResourceNotFoundError:
        return set()
    return set(json.loads(entity["opened"]).keys())


def mark_opened(user_email: str, day: date, link_id: str) -> None:
    """A plain Table Storage row, not a version-history item: no ETag check,
    same single-device-at-a-time reasoning as checklist.toggle_done."""
    table = get_table_client(TABLE_NAME)
    row_key = day.isoformat()
    try:
        entity = table.get_entity(partition_key=user_email, row_key=row_key)
        opened = json.loads(entity["opened"])
    except ResourceNotFoundError:
        opened = {}

    opened[link_id] = True
    table.upsert_entity({
        "PartitionKey": user_email,
        "RowKey": row_key,
        "opened": json.dumps(opened),
    })
