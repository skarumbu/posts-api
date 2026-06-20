import os
import logging

from google.oauth2 import id_token
from google.auth.transport import requests as google_requests
import azure.functions as func

logger = logging.getLogger("posts-api")

_google_request = google_requests.Request()


def require_auth(req: func.HttpRequest) -> tuple[str, str]:
    """Authenticate request via Google ID token.

    Returns (user_id, email). Raises ValueError if unauthenticated.
    Expects: Authorization: Bearer <google_id_token>
    """
    auth_header = req.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise ValueError("Missing or invalid Authorization header")
    token = auth_header[len("Bearer "):]
    try:
        allowed = {c.strip() for c in os.environ["GOOGLE_CLIENT_ID"].split(",") if c.strip()}
        idinfo = id_token.verify_oauth2_token(token, _google_request, None)
        if idinfo.get("aud") not in allowed:
            raise ValueError(f"Unrecognised client: {idinfo.get('aud')}")
        return idinfo["sub"], idinfo["email"]
    except Exception as e:
        logger.warning("Google token verification failed: %s", e)
        raise ValueError("Invalid or expired token") from e
