"""Shared exception vocabulary across all storage backends (GitHubStorage,
BlobStorage, HistoryApiStorage), so function_app.py's route handlers can
catch one exception type instead of branching per backend."""


class StorageConflictError(Exception):
    """Raised when a create/update/delete loses an optimistic-concurrency
    check against the backend's current state (GitHub's SHA, Blob's ETag,
    or history-api's expected_version_id)."""
