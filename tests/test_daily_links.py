import json
from datetime import date
from unittest.mock import patch, MagicMock

from azure.core.exceptions import ResourceNotFoundError

from daily.links import LinkDef, get_config, set_config, get_opened_today, mark_opened


def _config_entity(links):
    return {
        "PartitionKey": "user@example.com",
        "RowKey": "config",
        "links": json.dumps([link.__dict__ for link in links]),
    }


def test_get_config_returns_empty_list_when_missing():
    mock_table = MagicMock()
    mock_table.get_entity.side_effect = ResourceNotFoundError("missing")

    with patch("daily.links.get_table_client", return_value=mock_table):
        assert get_config("user@example.com") == []


def test_get_config_decodes_existing_row():
    links = [LinkDef(id="wordle", label="Wordle", url="https://www.nytimes.com/games/wordle")]
    mock_table = MagicMock()
    mock_table.get_entity.return_value = _config_entity(links)

    with patch("daily.links.get_table_client", return_value=mock_table):
        assert get_config("user@example.com") == links


def test_set_config_upserts_config_row():
    mock_table = MagicMock()
    links = [LinkDef(id="krillion", label="Krillion", url="https://krillion.io")]

    with patch("daily.links.get_table_client", return_value=mock_table):
        set_config("user@example.com", links)

    entity = mock_table.upsert_entity.call_args[0][0]
    assert entity["RowKey"] == "config"
    assert json.loads(entity["links"]) == [{"id": "krillion", "label": "Krillion", "url": "https://krillion.io"}]


def test_get_opened_today_empty_when_no_row_for_day():
    mock_table = MagicMock()
    mock_table.get_entity.side_effect = ResourceNotFoundError("missing")

    with patch("daily.links.get_table_client", return_value=mock_table):
        assert get_opened_today("user@example.com", date(2026, 10, 12)) == set()


def test_mark_opened_creates_row_when_absent():
    mock_table = MagicMock()
    mock_table.get_entity.side_effect = ResourceNotFoundError("missing")

    with patch("daily.links.get_table_client", return_value=mock_table):
        mark_opened("user@example.com", date(2026, 10, 12), "wordle")

    entity = mock_table.upsert_entity.call_args[0][0]
    assert entity["RowKey"] == "2026-10-12"
    assert json.loads(entity["opened"]) == {"wordle": True}


def test_mark_opened_merges_into_existing_row():
    mock_table = MagicMock()
    mock_table.get_entity.return_value = {
        "PartitionKey": "user@example.com",
        "RowKey": "2026-10-12",
        "opened": json.dumps({"random-wiki": True}),
    }

    with patch("daily.links.get_table_client", return_value=mock_table):
        mark_opened("user@example.com", date(2026, 10, 12), "wordle")

    entity = mock_table.upsert_entity.call_args[0][0]
    assert json.loads(entity["opened"]) == {"random-wiki": True, "wordle": True}
