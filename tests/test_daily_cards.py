from datetime import date
from unittest.mock import patch, MagicMock

import pytest
from azure.core.exceptions import ResourceNotFoundError

from daily.cards import IndexCard, schedule_next, list_due, create_card, review_card


def _card(ease=2.5, interval=6, next_review="2026-10-12"):
    return IndexCard(id="c1", text="capital of France", ease_factor=ease, interval_days=interval,
                      next_review_date=next_review, last_reviewed_date="2026-10-06")


def _entity(card: IndexCard):
    return {
        "PartitionKey": "user@example.com",
        "RowKey": card.id,
        "text": card.text,
        "ease_factor": card.ease_factor,
        "interval_days": card.interval_days,
        "next_review_date": card.next_review_date,
        "last_reviewed_date": card.last_reviewed_date,
    }


# --- schedule_next (pure) -------------------------------------------------

def test_schedule_next_again_resets_interval_and_drops_ease():
    current = _card(ease=2.5, interval=10)
    updated = schedule_next(current, "again", "2026-10-12")
    assert updated.interval_days == 1
    assert updated.ease_factor == pytest.approx(2.3)
    assert updated.next_review_date == "2026-10-13"
    assert updated.last_reviewed_date == "2026-10-12"


def test_schedule_next_good_scales_interval_by_ease():
    current = _card(ease=2.5, interval=6)
    updated = schedule_next(current, "good", "2026-10-12")
    assert updated.ease_factor == pytest.approx(2.5)
    assert updated.interval_days == round(6 * 2.5)


def test_schedule_next_easy_grows_faster_than_good():
    current = _card(ease=2.5, interval=6)
    good = schedule_next(current, "good", "2026-10-12")
    easy = schedule_next(current, "easy", "2026-10-12")
    assert easy.interval_days > good.interval_days
    assert easy.ease_factor > good.ease_factor


def test_schedule_next_ease_floors_at_1_3():
    current = _card(ease=1.3, interval=3)
    updated = schedule_next(current, "again", "2026-10-12")
    assert updated.ease_factor == pytest.approx(1.3)


# --- I/O functions (mocked table client) ---------------------------------

def test_list_due_filters_and_sorts_by_next_review_date():
    mock_table = MagicMock()
    mock_table.query_entities.return_value = [
        _entity(_card(next_review="2026-10-11")),
        {**_entity(_card(next_review="2026-10-09")), "RowKey": "c2"},
    ]

    with patch("daily.cards.get_table_client", return_value=mock_table):
        due = list_due("user@example.com", "2026-10-12")

    assert [c.next_review_date for c in due] == ["2026-10-09", "2026-10-11"]
    _, kwargs = mock_table.query_entities.call_args
    assert kwargs["parameters"] == {"user_email": "user@example.com", "today": "2026-10-12"}


def test_list_due_respects_limit():
    mock_table = MagicMock()
    mock_table.query_entities.return_value = [
        {**_entity(_card()), "RowKey": f"c{i}"} for i in range(5)
    ]

    with patch("daily.cards.get_table_client", return_value=mock_table):
        due = list_due("user@example.com", "2026-10-12", limit=2)

    assert len(due) == 2


def test_create_card_starts_with_default_ease_and_due_today():
    mock_table = MagicMock()

    with patch("daily.cards.get_table_client", return_value=mock_table):
        card = create_card("user@example.com", "capital of France")

    assert card.ease_factor == 2.5
    assert card.interval_days == 0
    mock_table.upsert_entity.assert_called_once()


def test_review_card_raises_on_missing_card():
    mock_table = MagicMock()
    mock_table.get_entity.side_effect = ResourceNotFoundError("missing")

    with patch("daily.cards.get_table_client", return_value=mock_table):
        with pytest.raises(ValueError):
            review_card("user@example.com", "missing", "good")


def test_review_card_applies_schedule_next_and_upserts():
    mock_table = MagicMock()
    mock_table.get_entity.return_value = _entity(_card(ease=2.5, interval=6))

    with patch("daily.cards.get_table_client", return_value=mock_table):
        updated = review_card("user@example.com", "c1", "good")

    assert updated.interval_days == round(6 * 2.5)
    mock_table.upsert_entity.assert_called_once()
