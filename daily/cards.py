"""
cards.py — spaced-repetition index cards backed by DailyCards.

An SM-2-style ease/interval model (open question in the design sketch,
assumed here since it's the standard algorithm this shape of data maps to;
`schedule_next` is a pure function so swapping it for a simpler fixed-interval
schedule later needs no migration). Table Storage's OData comparison
operators do the "due" filter server-side instead of a Python-side scan.
"""
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Literal

from azure.core.exceptions import ResourceNotFoundError

from daily.table_client import get_table_client

TABLE_NAME = "DailyCards"
Grade = Literal["again", "hard", "good", "easy"]

_MIN_EASE_FACTOR = 1.3
_EASE_DELTA = {"again": -0.2, "hard": -0.15, "good": 0.0, "easy": 0.15}


@dataclass
class IndexCard:
    id: str
    text: str
    ease_factor: float
    interval_days: int
    next_review_date: str
    last_reviewed_date: str | None = None


def schedule_next(current: IndexCard, grade: Grade, today: str) -> IndexCard:
    """SM-2-style update. 'again' always drops the interval back to 1 day
    regardless of the prior interval; the other grades scale it by the
    (new) ease factor, floored at 1.3 per SM-2's standard floor."""
    new_ease = max(_MIN_EASE_FACTOR, current.ease_factor + _EASE_DELTA[grade])
    if grade == "again":
        new_interval = 1
    elif grade == "hard":
        new_interval = max(1, round(current.interval_days * 1.2))
    elif grade == "easy":
        new_interval = max(1, round(current.interval_days * new_ease * 1.3))
    else:
        new_interval = max(1, round(current.interval_days * new_ease))

    next_review = (date.fromisoformat(today) + timedelta(days=new_interval)).isoformat()
    return IndexCard(
        id=current.id,
        text=current.text,
        ease_factor=new_ease,
        interval_days=new_interval,
        next_review_date=next_review,
        last_reviewed_date=today,
    )


def _from_entity(entity) -> IndexCard:
    return IndexCard(
        id=entity["RowKey"],
        text=entity["text"],
        ease_factor=entity["ease_factor"],
        interval_days=entity["interval_days"],
        next_review_date=entity["next_review_date"],
        last_reviewed_date=entity.get("last_reviewed_date"),
    )


def _to_entity(user_email: str, card: IndexCard) -> dict:
    return {
        "PartitionKey": user_email,
        "RowKey": card.id,
        "text": card.text,
        "ease_factor": card.ease_factor,
        "interval_days": card.interval_days,
        "next_review_date": card.next_review_date,
        "last_reviewed_date": card.last_reviewed_date,
    }


def list_due(user_email: str, today: str, limit: int = 20) -> list[IndexCard]:
    """Server-side filter via OData comparison operators, sorted client-side
    (Table Storage has no ORDER BY) and capped at `limit`."""
    table = get_table_client(TABLE_NAME)
    entities = table.query_entities(
        "PartitionKey eq @user_email and next_review_date le @today",
        parameters={"user_email": user_email, "today": today},
    )
    cards = [_from_entity(e) for e in entities]
    cards.sort(key=lambda c: c.next_review_date)
    return cards[:limit]


def create_card(user_email: str, text: str) -> IndexCard:
    today = datetime.now(timezone.utc).date().isoformat()
    card = IndexCard(
        id=str(uuid.uuid4()), text=text, ease_factor=2.5, interval_days=0,
        next_review_date=today, last_reviewed_date=None,
    )
    get_table_client(TABLE_NAME).upsert_entity(_to_entity(user_email, card))
    return card


def review_card(user_email: str, card_id: str, grade: Grade) -> IndexCard:
    """No ETag concurrency check, same single-device-at-a-time reasoning as
    checklist.toggle_done."""
    table = get_table_client(TABLE_NAME)
    try:
        entity = table.get_entity(partition_key=user_email, row_key=card_id)
    except ResourceNotFoundError:
        raise ValueError(f"No card {card_id} for {user_email}")

    current = _from_entity(entity)
    today = datetime.now(timezone.utc).date().isoformat()
    updated = schedule_next(current, grade, today)
    table.upsert_entity(_to_entity(user_email, updated))
    return updated
