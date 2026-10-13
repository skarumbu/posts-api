import json
from datetime import date
from unittest.mock import patch, MagicMock

import pytest
from azure.core.exceptions import ResourceNotFoundError

from daily.checklist import (
    ChecklistTask, is_done_on, is_active_on, list_tasks, create_task, toggle_done,
)


def _entity(task_id="t1", text="Stretch", done_dates=None, expires_after=None):
    return {
        "PartitionKey": "user@example.com",
        "RowKey": task_id,
        "text": text,
        "done_dates": json.dumps(done_dates or []),
        "expires_after": expires_after,
    }


# --- pure functions -----------------------------------------------------

def test_is_done_on_true_when_date_present():
    task = ChecklistTask(id="t1", text="Stretch", done_dates=["2026-10-10", "2026-10-12"])
    assert is_done_on(task, date(2026, 10, 12)) is True


def test_is_done_on_false_when_date_absent():
    task = ChecklistTask(id="t1", text="Stretch", done_dates=["2026-10-10"])
    assert is_done_on(task, date(2026, 10, 12)) is False


def test_is_active_on_recurs_forever_when_no_expiry():
    task = ChecklistTask(id="t1", text="Stretch", done_dates=[], expires_after=None)
    assert is_active_on(task, date(2099, 1, 1)) is True


def test_is_active_on_false_after_expiry():
    task = ChecklistTask(id="t1", text="One-off", done_dates=[], expires_after="2026-10-01")
    assert is_active_on(task, date(2026, 10, 12)) is False


def test_is_active_on_true_on_expiry_day():
    task = ChecklistTask(id="t1", text="One-off", done_dates=[], expires_after="2026-10-12")
    assert is_active_on(task, date(2026, 10, 12)) is True


# --- I/O functions (mocked table client) ---------------------------------

def test_list_tasks_queries_partition_and_decodes_done_dates():
    mock_table = MagicMock()
    mock_table.query_entities.return_value = [_entity(done_dates=["2026-10-10"])]

    with patch("daily.checklist.get_table_client", return_value=mock_table):
        tasks = list_tasks("user@example.com")

    assert tasks == [ChecklistTask(id="t1", text="Stretch", done_dates=["2026-10-10"], expires_after=None)]
    args, kwargs = mock_table.query_entities.call_args
    assert kwargs["parameters"] == {"user_email": "user@example.com"}


def test_create_task_upserts_new_row_with_empty_done_dates():
    mock_table = MagicMock()

    with patch("daily.checklist.get_table_client", return_value=mock_table):
        task = create_task("user@example.com", text="Stretch")

    assert task.text == "Stretch"
    assert task.done_dates == []
    mock_table.upsert_entity.assert_called_once()
    entity = mock_table.upsert_entity.call_args[0][0]
    assert entity["PartitionKey"] == "user@example.com"
    assert entity["RowKey"] == task.id


def test_toggle_done_adds_date_when_absent():
    mock_table = MagicMock()
    mock_table.get_entity.return_value = _entity(done_dates=["2026-10-10"])

    with patch("daily.checklist.get_table_client", return_value=mock_table):
        task = toggle_done("user@example.com", "t1", date(2026, 10, 12))

    assert task.done_dates == ["2026-10-10", "2026-10-12"]
    mock_table.upsert_entity.assert_called_once()


def test_toggle_done_removes_date_when_present():
    mock_table = MagicMock()
    mock_table.get_entity.return_value = _entity(done_dates=["2026-10-10", "2026-10-12"])

    with patch("daily.checklist.get_table_client", return_value=mock_table):
        task = toggle_done("user@example.com", "t1", date(2026, 10, 12))

    assert task.done_dates == ["2026-10-10"]


def test_toggle_done_raises_on_missing_task():
    mock_table = MagicMock()
    mock_table.get_entity.side_effect = ResourceNotFoundError("missing")

    with patch("daily.checklist.get_table_client", return_value=mock_table):
        with pytest.raises(ValueError):
            toggle_done("user@example.com", "missing", date(2026, 10, 12))
