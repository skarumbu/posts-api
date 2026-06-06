import base64


def encode_content(content_str: str) -> str:
    """Encode a string as base64 the way GitHub API returns it (no embedded newlines)."""
    return base64.b64encode(content_str.encode("utf-8")).decode("ascii")
