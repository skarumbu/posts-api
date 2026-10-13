"""
Tests for daily/table_client.py. Mocks TableServiceClient.from_connection_string
so the azure-data-tables SDK never touches a real (or Azurite-emulated)
storage account — pure unit test, mirrors test_blob_storage.py's approach.
"""
import os
from unittest.mock import patch, MagicMock

from daily.table_client import get_table_client

_ENV = {"DAILY_STORAGE_CONNECTION_STRING": "UseDevelopmentStorage=true"}


def test_get_table_client_reads_connection_string_and_returns_table():
    mock_table = MagicMock()
    mock_service = MagicMock()
    mock_service.get_table_client.return_value = mock_table

    with patch.dict(os.environ, _ENV):
        with patch("daily.table_client.TableServiceClient") as mock_cls:
            mock_cls.from_connection_string.return_value = mock_service
            result = get_table_client("DailyChecklist")

    mock_cls.from_connection_string.assert_called_once_with("UseDevelopmentStorage=true")
    mock_service.get_table_client.assert_called_once_with("DailyChecklist")
    assert result is mock_table
