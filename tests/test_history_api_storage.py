import os
import pytest
from unittest.mock import patch, MagicMock
from storage.history_api_storage import HistoryApiStorage
from storage.errors import StorageConflictError

_ENV = {
    "HISTORY_API_URL": "https://history-api-prod.azurewebsites.net/api",
    "HISTORY_API_KEY": "test-machine-key",
}


def test_get_returns_version_id_and_content():
    resp = MagicMock(status_code=200)
    resp.json.return_value = {"version_id": "v2", "content": "hello", "content_type": "markdown"}
    with patch.dict(os.environ, _ENV), patch("storage.history_api_storage.requests.get", return_value=resp):
        version_id, content = HistoryApiStorage(section="writing").get("my-post")
    assert version_id == "v2"
    assert content == "hello"


def test_get_missing_returns_none_none():
    resp = MagicMock(status_code=404)
    with patch.dict(os.environ, _ENV), patch("storage.history_api_storage.requests.get", return_value=resp):
        version_id, content = HistoryApiStorage(section="writing").get("missing")
    assert version_id is None
    assert content is None


def test_list_all_fetches_every_documents_content():
    list_resp = MagicMock(status_code=200)
    list_resp.json.return_value = {"documents": [{"slug": "a", "latest_version_id": "v1", "content_type": "markdown"}, {"slug": "b", "latest_version_id": "v1", "content_type": "markdown"}]}
    get_resp_a = MagicMock(status_code=200)
    get_resp_a.json.return_value = {"content": "content-a"}
    get_resp_b = MagicMock(status_code=200)
    get_resp_b.json.return_value = {"content": "content-b"}
    with patch.dict(os.environ, _ENV), patch("storage.history_api_storage.requests.get", side_effect=[list_resp, get_resp_a, get_resp_b]):
        contents = HistoryApiStorage(section="writing").list_all()
    assert contents == ["content-a", "content-b"]


def test_create_posts_version():
    get_resp = MagicMock(status_code=404)  # slug does not already exist
    post_resp = MagicMock(status_code=201)
    with patch.dict(os.environ, _ENV), \
         patch("storage.history_api_storage.requests.get", return_value=get_resp), \
         patch("storage.history_api_storage.requests.post", return_value=post_resp) as mock_post:
        HistoryApiStorage(section="writing").create("my-post", "content", "writing: add my-post")
    args, kwargs = mock_post.call_args
    assert args[0] == "https://history-api-prod.azurewebsites.net/api/documents/writing::my-post/versions"
    assert kwargs["json"]["content"] == "content"
    assert kwargs["json"]["content_type"] == "markdown"
    assert kwargs["headers"]["X-History-Key"] == "test-machine-key"


def test_create_raises_storage_conflict_error_when_slug_already_exists():
    get_resp = MagicMock(status_code=200)  # slug already exists
    with patch.dict(os.environ, _ENV), \
         patch("storage.history_api_storage.requests.get", return_value=get_resp), \
         patch("storage.history_api_storage.requests.post") as mock_post:
        with pytest.raises(StorageConflictError):
            HistoryApiStorage(section="writing").create("my-post", "content", "writing: add my-post")
    mock_post.assert_not_called()


def test_update_sends_expected_version_id():
    resp = MagicMock(status_code=201)
    with patch.dict(os.environ, _ENV), patch("storage.history_api_storage.requests.post", return_value=resp) as mock_post:
        HistoryApiStorage(section="writing").update("my-post", "new content", "v1", "writing: update my-post")
    _, kwargs = mock_post.call_args
    assert kwargs["json"]["expected_version_id"] == "v1"


def test_update_conflict_raises_storage_conflict_error():
    resp = MagicMock(status_code=409)
    with patch.dict(os.environ, _ENV), patch("storage.history_api_storage.requests.post", return_value=resp):
        with pytest.raises(StorageConflictError):
            HistoryApiStorage(section="writing").update("my-post", "content", "stale-version", "msg")


def test_delete_calls_delete_endpoint():
    resp = MagicMock(status_code=204)
    with patch.dict(os.environ, _ENV), patch("storage.history_api_storage.requests.delete", return_value=resp) as mock_delete:
        HistoryApiStorage(section="writing").delete("my-post", "v1", "writing: delete my-post")
    args, kwargs = mock_delete.call_args
    assert args[0] == "https://history-api-prod.azurewebsites.net/api/sections/writing/documents/my-post"
    assert kwargs["json"]["expected_version_id"] == "v1"


def test_delete_conflict_raises_storage_conflict_error():
    resp = MagicMock(status_code=409)
    with patch.dict(os.environ, _ENV), patch("storage.history_api_storage.requests.delete", return_value=resp):
        with pytest.raises(StorageConflictError):
            HistoryApiStorage(section="writing").delete("my-post", "stale-version", "msg")


def test_slug_exists_true():
    resp = MagicMock(status_code=200)
    with patch.dict(os.environ, _ENV), patch("storage.history_api_storage.requests.get", return_value=resp):
        assert HistoryApiStorage(section="writing").slug_exists("my-post") is True


def test_slug_exists_false():
    resp = MagicMock(status_code=404)
    with patch.dict(os.environ, _ENV), patch("storage.history_api_storage.requests.get", return_value=resp):
        assert HistoryApiStorage(section="writing").slug_exists("missing") is False


def test_generate_slug_dedup():
    taken = MagicMock(status_code=200)
    free = MagicMock(status_code=404)
    with patch.dict(os.environ, _ENV), patch("storage.history_api_storage.requests.get", side_effect=[taken, free]):
        result = HistoryApiStorage(section="writing").generate_slug("Hello World")
    assert result == "hello-world-2"
