#!/usr/bin/env python3
"""
CLI for the quixotry.me posts-api.

Required env vars:
  POSTS_API_BASE_URL          e.g. https://posts-api-xxx.azurewebsites.net
  POSTS_GOOGLE_CLIENT_ID      OAuth 2.0 client ID (web client with http://localhost redirect)
  POSTS_GOOGLE_CLIENT_SECRET  OAuth 2.0 client secret

Sub-commands:
  login                         Authenticate with Google and cache token
  list                          List all published posts
  create --title T --description D [--body B] [--body-file F] [--published]
  update <slug> --title T --description D [--body B] [--body-file F] [--published]
  delete <slug>

First-time setup:
  pip install -r requirements-cli.txt
  export POSTS_API_BASE_URL=https://...
  export POSTS_GOOGLE_CLIENT_ID=<client-id>
  export POSTS_GOOGLE_CLIENT_SECRET=<client-secret>
  python post.py login
"""

import argparse
import base64
import json
import os
import sys
import time

import requests
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["openid", "email"]
_CACHE = os.path.join(os.path.expanduser("~"), ".posts-cli-google-token.json")


def _require_env(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        print(f"Error: environment variable {name} is not set.", file=sys.stderr)
        sys.exit(1)
    return val


def _get_client_config() -> dict:
    client_id = _require_env("POSTS_GOOGLE_CLIENT_ID")
    client_secret = _require_env("POSTS_GOOGLE_CLIENT_SECRET")
    return {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"],
        }
    }


def _decode_jwt_payload(token: str) -> dict:
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload))
    except Exception:
        return {}


def _is_id_token_expired(token: str) -> bool:
    payload = _decode_jwt_payload(token)
    exp = payload.get("exp", 0)
    return time.time() > exp - 30


def _load_cached_token() -> str | None:
    if not os.path.exists(_CACHE):
        return None
    try:
        with open(_CACHE) as f:
            data = json.load(f)
        token = data.get("id_token", "")
        if token and not _is_id_token_expired(token):
            return token
    except Exception:
        pass
    return None


def _save_token(id_token: str) -> None:
    with open(_CACHE, "w") as f:
        json.dump({"id_token": id_token}, f)
    os.chmod(_CACHE, 0o600)


def _do_login() -> str:
    flow = InstalledAppFlow.from_client_config(_get_client_config(), SCOPES)
    creds = flow.run_local_server(port=0, prompt="consent")
    id_token = creds.id_token
    if not id_token:
        print("Error: No ID token received from Google. Ensure 'openid' scope is enabled.", file=sys.stderr)
        sys.exit(1)
    _save_token(id_token)
    return id_token


def _get_token() -> str:
    token = _load_cached_token()
    if token:
        return token
    print("Token expired or not found — opening browser for Google sign-in…")
    return _do_login()


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _base_url() -> str:
    return _require_env("POSTS_API_BASE_URL").rstrip("/")


def _handle_response(resp: requests.Response, success_status: int = 200) -> dict | None:
    if resp.status_code == 401:
        print("Error: Unauthorized (401). Re-run: python post.py login", file=sys.stderr)
        sys.exit(1)
    if resp.status_code == 403:
        print("Error: Forbidden (403). Your account does not have write access.", file=sys.stderr)
        sys.exit(1)
    if resp.status_code == 404:
        print("Error: Not found (404).", file=sys.stderr)
        sys.exit(1)
    if 400 <= resp.status_code < 500:
        try:
            body = resp.json()
            print(f"Error ({resp.status_code}): {body.get('error', body)}", file=sys.stderr)
        except Exception:
            print(f"Error ({resp.status_code}): {resp.text}", file=sys.stderr)
        sys.exit(1)
    if resp.status_code >= 500:
        print(f"Error: posts-api returned a server error ({resp.status_code}).", file=sys.stderr)
        sys.exit(1)
    if resp.status_code == 204:
        return None
    return resp.json()


def cmd_login(_args: argparse.Namespace) -> None:
    id_token = _do_login()
    payload = _decode_jwt_payload(id_token)
    email = payload.get("email", "unknown")
    print(f"Signed in as {email}")
    print(f"Token cached at: {_CACHE}")


def cmd_list(_args: argparse.Namespace) -> None:
    token = _get_token()
    resp = requests.get(f"{_base_url()}/api/posts", headers=_headers(token))
    data = _handle_response(resp)
    posts = data.get("posts", [])
    if not posts:
        print("No published posts.")
        return
    print(f"{'SLUG':<40} {'TITLE'}")
    print("-" * 70)
    for p in posts:
        print(f"{p['slug']:<40} {p['title']}")


def _resolve_body(args: argparse.Namespace) -> str:
    if getattr(args, "body_file", None):
        with open(args.body_file, "r", encoding="utf-8") as f:
            return f.read()
    return args.body or ""


def cmd_create(args: argparse.Namespace) -> None:
    token = _get_token()
    payload = {
        "title": args.title,
        "description": args.description,
        "body": _resolve_body(args),
        "published": args.published,
    }
    resp = requests.post(
        f"{_base_url()}/api/posts",
        headers=_headers(token),
        data=json.dumps(payload),
    )
    data = _handle_response(resp, success_status=201)
    print(f"Created: {data['slug']}")


def cmd_update(args: argparse.Namespace) -> None:
    token = _get_token()
    payload = {
        "title": args.title,
        "description": args.description,
        "body": _resolve_body(args),
        "published": args.published,
    }
    resp = requests.put(
        f"{_base_url()}/api/posts/{args.slug}",
        headers=_headers(token),
        data=json.dumps(payload),
    )
    _handle_response(resp)
    print(f"Updated: {args.slug}")


def cmd_delete(args: argparse.Namespace) -> None:
    token = _get_token()
    resp = requests.delete(
        f"{_base_url()}/api/posts/{args.slug}",
        headers=_headers(token),
    )
    _handle_response(resp, success_status=204)
    print(f"Deleted: {args.slug}")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="post.py",
        description="CLI for the quixotry.me posts-api",
    )
    sub = parser.add_subparsers(dest="command", metavar="command")

    sub.add_parser("login", help="Authenticate with Google and cache token")
    sub.add_parser("list", help="List all published posts")

    p_create = sub.add_parser("create", help="Create a new post")
    p_create.add_argument("--title", required=True, help="Post title")
    p_create.add_argument("--description", required=True, help="Post description/subtitle")
    p_create.add_argument("--body", default="", help="Post body (markdown)")
    p_create.add_argument("--body-file", dest="body_file", help="Read body from file instead of --body")
    p_create.add_argument("--published", action="store_true", help="Publish immediately")

    p_update = sub.add_parser("update", help="Update an existing post")
    p_update.add_argument("slug", help="Post slug to update")
    p_update.add_argument("--title", required=True)
    p_update.add_argument("--description", required=True)
    p_update.add_argument("--body", default="")
    p_update.add_argument("--body-file", dest="body_file", help="Read body from file instead of --body")
    p_update.add_argument("--published", action="store_true")

    p_delete = sub.add_parser("delete", help="Delete a post")
    p_delete.add_argument("slug", help="Post slug to delete")

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(0)

    dispatch = {
        "login": cmd_login,
        "list": cmd_list,
        "create": cmd_create,
        "update": cmd_update,
        "delete": cmd_delete,
    }
    dispatch[args.command](args)


if __name__ == "__main__":
    main()
