"""
Tests for BlobStorage. Mocks BlobStorage._container_client so azure-storage-blob's
BlobServiceClient/ContainerClient/BlobClient chain never touches a real (or
Azurite-emulated) storage account — pure unit tests.
"""
import os
from unittest.mock import patch, MagicMock

import pytest
from azure.core.exceptions import ResourceNotFoundError, ResourceModifiedError

from storage.blob_storage import BlobStorage
from storage.errors import StorageConflictError

_ENV = {
    "POSTS_STORAGE_CONNECTION_STRING": "UseDevelopmentStorage=true",
    "DIARY_CONTAINER_NAME": "diary-entries-test",
}


def _storage_with_mocked_container(mock_container):
    storage = BlobStorage(container_env="DIARY_CONTAINER_NAME")
    storage._container_client = MagicMock(return_value=mock_container)
    return storage


def test_get_returns_content_and_etag():
    mock_blob = MagicMock()
    downloaded = MagicMock()
    downloaded.readall.return_value = b'{"title": "T"}'
    downloaded.properties.etag = "etag-123"
    mock_blob.download_blob.return_value = downloaded
    mock_container = MagicMock()
    mock_container.get_blob_client.return_value = mock_blob

    with patch.dict(os.environ, _ENV):
        storage = _storage_with_mocked_container(mock_container)
        version_token, raw = storage.get("my-slug")

    assert version_token == "etag-123"
    assert raw == '{"title": "T"}'
    mock_container.get_blob_client.assert_called_with("my-slug.json")


def test_get_returns_none_on_missing():
    mock_blob = MagicMock()
    mock_blob.download_blob.side_effect = ResourceNotFoundError("missing")
    mock_container = MagicMock()
    mock_container.get_blob_client.return_value = mock_blob

    with patch.dict(os.environ, _ENV):
        storage = _storage_with_mocked_container(mock_container)
        version_token, raw = storage.get("missing")

    assert version_token is None
    assert raw is None


def test_list_all_returns_raw_contents():
    entry1 = MagicMock(name="entry1")
    entry1.name = "a.json"
    entry2 = MagicMock(name="entry2")
    entry2.name = "b.json"
    mock_container = MagicMock()
    mock_container.list_blobs.return_value = [entry1, entry2]

    downloaded1 = MagicMock()
    downloaded1.readall.return_value = b'{"slug": "a"}'
    downloaded2 = MagicMock()
    downloaded2.readall.return_value = b'{"slug": "b"}'
    blob1, blob2 = MagicMock(), MagicMock()
    blob1.download_blob.return_value = downloaded1
    blob2.download_blob.return_value = downloaded2
    mock_container.get_blob_client.side_effect = [blob1, blob2]

    with patch.dict(os.environ, _ENV):
        storage = _storage_with_mocked_container(mock_container)
        contents = storage.list_all()

    assert contents == ['{"slug": "a"}', '{"slug": "b"}']


def test_create_uploads_without_overwrite():
    mock_blob = MagicMock()
    mock_container = MagicMock()
    mock_container.get_blob_client.return_value = mock_blob

    with patch.dict(os.environ, _ENV):
        storage = _storage_with_mocked_container(mock_container)
        storage.create("new-slug", '{"title": "New"}', "diary: add new-slug")

    mock_blob.upload_blob.assert_called_once()
    args, kwargs = mock_blob.upload_blob.call_args
    assert kwargs.get("overwrite") is False


def test_update_passes_etag_for_optimistic_concurrency():
    mock_blob = MagicMock()
    mock_container = MagicMock()
    mock_container.get_blob_client.return_value = mock_blob

    with patch.dict(os.environ, _ENV):
        storage = _storage_with_mocked_container(mock_container)
        storage.update("my-slug", '{"title": "Updated"}', "etag-123", "diary: update my-slug")

    args, kwargs = mock_blob.upload_blob.call_args
    assert kwargs.get("overwrite") is True
    assert kwargs.get("etag") == "etag-123"


def test_delete_passes_etag():
    mock_blob = MagicMock()
    mock_container = MagicMock()
    mock_container.get_blob_client.return_value = mock_blob

    with patch.dict(os.environ, _ENV):
        storage = _storage_with_mocked_container(mock_container)
        storage.delete("my-slug", "etag-123", "diary: delete my-slug")

    mock_blob.delete_blob.assert_called_once()
    _, kwargs = mock_blob.delete_blob.call_args
    assert kwargs.get("etag") == "etag-123"


def test_update_conflict_raises_storage_conflict_error():
    mock_blob = MagicMock()
    mock_blob.upload_blob.side_effect = ResourceModifiedError("etag mismatch")
    mock_container = MagicMock()
    mock_container.get_blob_client.return_value = mock_blob

    with patch.dict(os.environ, _ENV):
        storage = _storage_with_mocked_container(mock_container)
        with pytest.raises(StorageConflictError):
            storage.update("my-slug", '{"title": "Updated"}', "etag-123", "diary: update my-slug")


def test_slug_exists_true_and_false():
    mock_blob = MagicMock()
    mock_blob.exists.return_value = True
    mock_container = MagicMock()
    mock_container.get_blob_client.return_value = mock_blob

    with patch.dict(os.environ, _ENV):
        storage = _storage_with_mocked_container(mock_container)
        assert storage.slug_exists("taken") is True

    mock_blob.exists.return_value = False
    assert storage.slug_exists("free") is False


def test_generate_slug_dedup():
    mock_blob = MagicMock()
    mock_blob.exists.side_effect = [True, False]
    mock_container = MagicMock()
    mock_container.get_blob_client.return_value = mock_blob

    with patch.dict(os.environ, _ENV):
        storage = _storage_with_mocked_container(mock_container)
        result = storage.generate_slug("Hello World")

    assert result == "hello-world-2"


def test_generate_slug_empty_title_raises():
    storage = BlobStorage(container_env="DIARY_CONTAINER_NAME")
    with pytest.raises(ValueError):
        storage.generate_slug("!!!###")
