#!/usr/bin/env python3
"""
CLI for the quixotry.me posts-api.

Required env vars:
  POSTS_API_BASE_URL      e.g. https://posts-api-xxx.azurewebsites.net
  POSTS_AZURE_TENANT_ID   Azure AD tenant GUID

Optional env vars:
  POSTS_TOKEN_CACHE       Path to token cache file (default: ~/.posts-cli-cache.json)

Sub-commands:
  login                         Force device-code auth and save token cache
  list                          List all published posts
  create --title T --description D [--body B] [--published]
  update <slug> --title T --description D [--body B] [--published]
  delete <slug>

First-time setup:
  pip install -r requirements-cli.txt
  export POSTS_API_BASE_URL=https://...
  export POSTS_AZURE_TENANT_ID=<tenant-guid>
  python post.py login
"""

import argparse
import json
import os
import sys

import requests
from msal import PublicClientApplication, SerializableTokenCache

CLIENT_ID = "825b77cb-1492-406f-9072-923aa536b328"
SCOPE = ["api://825b77cb-1492-406f-9072-923aa536b328/.default"]


def _require_env(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        print(f"Error: environment variable {name} is not set.", file=sys.stderr)
        sys.exit(1)
    return val


def _cache_path() -> str:
    return os.environ.get(
        "POSTS_TOKEN_CACHE",
        os.path.join(os.path.expanduser("~"), ".posts-cli-cache.json"),
    )


def _load_cache() -> SerializableTokenCache:
    cache = SerializableTokenCache()
    path = _cache_path()
    if os.path.exists(path):
        with open(path, "r") as f:
            cache.deserialize(f.read())
    return cache


def _save_cache(cache: SerializableTokenCache) -> None:
    if not cache.has_state_changed:
        return
    path = _cache_path()
    with open(path, "w") as f:
        f.write(cache.serialize())
    os.chmod(path, 0o600)


def _build_app(cache: SerializableTokenCache) -> PublicClientApplication:
    tenant_id = _require_env("POSTS_AZURE_TENANT_ID")
    return PublicClientApplication(
        client_id=CLIENT_ID,
        authority=f"https://login.microsoftonline.com/{tenant_id}",
        token_cache=cache,
    )


def _get_token(force_login: bool = False) -> str:
    cache = _load_cache()
    app = _build_app(cache)

    token = None
    if not force_login:
        accounts = app.get_accounts()
        if accounts:
            result = app.acquire_token_silent(SCOPE, account=accounts[0])
            if result and "access_token" in result:
                token = result["access_token"]

    if token is None:
        flow = app.initiate_device_flow(SCOPE)
        if "user_code" not in flow:
            print(f"Error initiating device flow: {flow.get('error_description', flow)}", file=sys.stderr)
            sys.exit(1)
        print(flow["message"])
        result = app.acquire_token_by_device_flow(flow)
        if "access_token" not in result:
            print(f"Auth failed: {result.get('error_description', result.get('error', 'unknown'))}", file=sys.stderr)
            sys.exit(1)
        token = result["access_token"]

    _save_cache(cache)
    return token


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _base_url() -> str:
    return _require_env("POSTS_API_BASE_URL").rstrip("/")


def _handle_response(resp: requests.Response, success_status: int = 200) -> dict | None:
    if resp.status_code == 401:
        print("Error: Unauthorized (401). Re-run: python post.py login", file=sys.stderr)
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
    cache = _load_cache()
    app = _build_app(cache)
    flow = app.initiate_device_flow(SCOPE)
    if "user_code" not in flow:
        print(f"Error initiating device flow: {flow.get('error_description', flow)}", file=sys.stderr)
        sys.exit(1)
    print(flow["message"])
    result = app.acquire_token_by_device_flow(flow)
    if "access_token" not in result:
        print(f"Auth failed: {result.get('error_description', result.get('error', 'unknown'))}", file=sys.stderr)
        sys.exit(1)
    _save_cache(cache)

    accounts = app.get_accounts()
    email = accounts[0].get("username", "unknown") if accounts else "unknown"
    print(f"Logged in as {email}")
    print(f"Token cache saved to: {_cache_path()}")


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


def cmd_create(args: argparse.Namespace) -> None:
    token = _get_token()
    payload = {
        "title": args.title,
        "description": args.description,
        "body": args.body or "",
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
        "body": args.body or "",
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

    sub.add_parser("login", help="Force device-code auth and save token cache")
    sub.add_parser("list", help="List all published posts")

    p_create = sub.add_parser("create", help="Create a new post")
    p_create.add_argument("--title", required=True, help="Post title")
    p_create.add_argument("--description", required=True, help="Post description/subtitle")
    p_create.add_argument("--body", default="", help="Post body (markdown)")
    p_create.add_argument("--published", action="store_true", help="Publish immediately")

    p_update = sub.add_parser("update", help="Update an existing post")
    p_update.add_argument("slug", help="Post slug to update")
    p_update.add_argument("--title", required=True)
    p_update.add_argument("--description", required=True)
    p_update.add_argument("--body", default="")
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
