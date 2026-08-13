import os
from unittest.mock import patch, MagicMock

from scripts.backfill_history import run_backfill, main


def test_backfill_skips_existing_slugs_unless_forced():
    fake_storage = MagicMock()
    fake_storage.list_all.return_value = ['---\ntitle: Hello\nslug: hello\n---\nbody']

    with patch("scripts.backfill_history.schema_writing.parse_post") as mock_parse:
        mock_parse.return_value = MagicMock(metadata={"slug": "hello"})
        with patch("scripts.backfill_history.requests.get") as mock_get, \
             patch("scripts.backfill_history.requests.post") as mock_post:
            mock_get.return_value = MagicMock(status_code=200)  # slug already exists
            result = run_backfill("writing", fake_storage, "https://history-api-prod.azurewebsites.net/api", "test-key")

    mock_post.assert_not_called()
    assert result["skipped"] == 1
    assert result["imported"] == 0


def test_backfill_imports_new_slugs():
    fake_storage = MagicMock()
    fake_storage.list_all.return_value = ['---\ntitle: Hello\nslug: hello\n---\nbody']

    with patch("scripts.backfill_history.schema_writing.parse_post") as mock_parse:
        mock_parse.return_value = MagicMock(metadata={"slug": "hello"})
        with patch("scripts.backfill_history.requests.get") as mock_get, \
             patch("scripts.backfill_history.requests.post") as mock_post:
            mock_get.return_value = MagicMock(status_code=404)  # slug doesn't exist yet
            mock_post.return_value = MagicMock(status_code=201)
            result = run_backfill("writing", fake_storage, "https://history-api-prod.azurewebsites.net/api", "test-key")

    mock_post.assert_called_once()
    _, kwargs = mock_post.call_args
    assert kwargs["json"]["content_type"] == "markdown"
    assert kwargs["json"]["message"] == "backfill: initial import"
    assert result["imported"] == 1
    assert result["skipped"] == 0


def test_backfill_force_reimports_existing_slugs():
    fake_storage = MagicMock()
    fake_storage.list_all.return_value = ['---\ntitle: Hello\nslug: hello\n---\nbody']

    with patch("scripts.backfill_history.schema_writing.parse_post") as mock_parse:
        mock_parse.return_value = MagicMock(metadata={"slug": "hello"})
        with patch("scripts.backfill_history.requests.get") as mock_get, \
             patch("scripts.backfill_history.requests.post") as mock_post:
            mock_get.return_value = MagicMock(status_code=200)
            mock_post.return_value = MagicMock(status_code=201)
            result = run_backfill("writing", fake_storage, "https://history-api-prod.azurewebsites.net/api", "test-key", force=True)

    mock_post.assert_called_once()
    assert result["imported"] == 1


def test_backfill_post_failure_does_not_abort_run():
    fake_storage = MagicMock()
    fake_storage.list_all.return_value = [
        '---\ntitle: Hello\nslug: hello\n---\nbody',
        '---\ntitle: World\nslug: world\n---\nbody',
    ]

    with patch("scripts.backfill_history.schema_writing.parse_post") as mock_parse:
        mock_parse.side_effect = [
            MagicMock(metadata={"slug": "hello"}),
            MagicMock(metadata={"slug": "world"}),
        ]
        with patch("scripts.backfill_history.requests.get") as mock_get, \
             patch("scripts.backfill_history.requests.post") as mock_post:
            mock_get.return_value = MagicMock(status_code=404)  # neither slug exists yet
            failing_resp = MagicMock(status_code=500)
            failing_resp.raise_for_status.side_effect = Exception("boom")
            ok_resp = MagicMock(status_code=201)
            mock_post.side_effect = [failing_resp, ok_resp]
            result = run_backfill("writing", fake_storage, "https://history-api-prod.azurewebsites.net/api", "test-key")

    assert mock_post.call_count == 2
    assert result["failed"] == 1
    assert result["imported"] == 1
    assert result["skipped"] == 0


def test_backfill_parse_failure_does_not_abort_run():
    fake_storage = MagicMock()
    fake_storage.list_all.return_value = [
        'malformed',
        '---\ntitle: World\nslug: world\n---\nbody',
    ]

    with patch("scripts.backfill_history.schema_writing.parse_post") as mock_parse:
        mock_parse.side_effect = [Exception("bad frontmatter"), MagicMock(metadata={"slug": "world"})]
        with patch("scripts.backfill_history.requests.get") as mock_get, \
             patch("scripts.backfill_history.requests.post") as mock_post:
            mock_get.return_value = MagicMock(status_code=404)
            mock_post.return_value = MagicMock(status_code=201)
            result = run_backfill("writing", fake_storage, "https://history-api-prod.azurewebsites.net/api", "test-key")

    mock_post.assert_called_once()
    assert result["failed"] == 1
    assert result["imported"] == 1
    assert result["skipped"] == 0


def test_main_returns_1_when_items_failed():
    """main() must exit non-zero when any item failed, so an operator running
    this during production cutover doesn't get a success code on a partial failure."""
    with patch("scripts.backfill_history.sys.argv", ["backfill_history.py", "--section", "writing"]), \
         patch.dict(os.environ, {"HISTORY_API_URL": "https://history-api", "HISTORY_API_KEY": "key"}), \
         patch("scripts.backfill_history.GitHubStorage"), \
         patch("scripts.backfill_history.run_backfill", return_value={"imported": 0, "skipped": 0, "failed": 1}):
        assert main() == 1


def test_main_returns_0_when_no_failures():
    with patch("scripts.backfill_history.sys.argv", ["backfill_history.py", "--section", "writing"]), \
         patch.dict(os.environ, {"HISTORY_API_URL": "https://history-api", "HISTORY_API_KEY": "key"}), \
         patch("scripts.backfill_history.GitHubStorage"), \
         patch("scripts.backfill_history.run_backfill", return_value={"imported": 1, "skipped": 0, "failed": 0}):
        assert main() == 0
