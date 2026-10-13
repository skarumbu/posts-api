"""
checklist.py — recurring daily tasks backed by DailyChecklist.

Each task is one row holding an append-only log of ISO dates on which it was
completed (`done_dates`). No day-snapshot, no carry-over logic: "unchecked
yesterday" needs no representation because nothing is ever snapshotted per
day — see the design sketch's synthesis decision for why this strictly
dominates a day-instance model for the same observable behavior.
"""
import json
import uuid
from dataclasses import dataclass
from datetime import date

from azure.core.exceptions import ResourceNotFoundError

from daily.table_client import get_table_client

TABLE_NAME = "DailyChecklist"


@dataclass
class ChecklistTask:
    id: str
    text: str
    done_dates: list[str]
    expires_after: str | None = None


def is_done_on(task: ChecklistTask, day: date) -> bool:
    return day.isoformat() in task.done_dates


def is_active_on(task: ChecklistTask, day: date) -> bool:
    return task.expires_after is None or date.fromisoformat(task.expires_after) >= day


def _from_entity(entity) -> ChecklistTask:
    return ChecklistTask(
        id=entity["RowKey"],
        text=entity["text"],
        done_dates=json.loads(entity["done_dates"]),
        expires_after=entity.get("expires_after"),
    )


def _to_entity(user_email: str, task: ChecklistTask) -> dict:
    return {
        "PartitionKey": user_email,
        "RowKey": task.id,
        "text": task.text,
        "done_dates": json.dumps(task.done_dates),
        "expires_after": task.expires_after,
    }


def list_tasks(user_email: str) -> list[ChecklistTask]:
    table = get_table_client(TABLE_NAME)
    entities = table.query_entities(
        "PartitionKey eq @user_email",
        parameters={"user_email": user_email},
    )
    return [_from_entity(e) for e in entities]


def create_task(user_email: str, text: str, expires_after: str | None = None) -> ChecklistTask:
    task = ChecklistTask(id=str(uuid.uuid4()), text=text, done_dates=[], expires_after=expires_after)
    get_table_client(TABLE_NAME).upsert_entity(_to_entity(user_email, task))
    return task


def toggle_done(user_email: str, task_id: str, day: date) -> ChecklistTask:
    """No ETag concurrency check: a single user toggling their own tasks from
    one device at a time has no concurrent-writer case to guard."""
    table = get_table_client(TABLE_NAME)
    try:
        entity = table.get_entity(partition_key=user_email, row_key=task_id)
    except ResourceNotFoundError:
        raise ValueError(f"No checklist task {task_id} for {user_email}")

    task = _from_entity(entity)
    iso = day.isoformat()
    if iso in task.done_dates:
        task.done_dates.remove(iso)
    else:
        task.done_dates.append(iso)

    table.upsert_entity(_to_entity(user_email, task))
    return task
