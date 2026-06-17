---
wave: 1
phase: cli
goal: >
  Provide a standalone Python CLI (post.py) that can create, list, update,
  and delete posts on the quixotry.me posts-api using the existing Azure AD
  MSAL device-code auth flow — no backend changes required.
files_modified:
  - posts-api/post.py
  - posts-api/requirements-cli.txt
autonomous: true
---

# Plan: posts-api CLI

## Phase Goal

A self-contained `post.py` script that:

1. On first run — prompts the user with a device-code URL + code; the user
   signs in on any browser; the token is written to `~/.posts-cli-cache.json`.
2. On subsequent runs — silently refreshes the token from the cache.
3. Exposes four sub-commands: `create`, `list`, `update`, `delete`.
4. Can be called from ideas-bot as a subprocess (headless once cache is warm).

**Auth mechanism:** MSAL Python + device code flow → Bearer token →
Azure Easy Auth on the Function App validates it and injects
`X-MS-CLIENT-PRINCIPAL` before the handler sees the request.
No changes to `posts-api/function_app.py` or `auth.py` are needed.

---

## Research Notes

### How the existing auth works end-to-end

```
[CLI / browser]
    │  POST with Authorization: Bearer <access_token>
    ▼
[Azure Function App — Easy Auth middleware]
    │  Validates token against Azure AD tenant
    │  Injects X-MS-CLIENT-PRINCIPAL (base64 claims JSON)
    ▼
[function_app.py → require_auth(req)]
    │  Decodes X-MS-CLIENT-PRINCIPAL → (oid, email, name)
    ▼
  handler runs
```

The CLI only needs a valid access token for scope
`api://825b77cb-1492-406f-9072-923aa536b328/.default`.
Azure Easy Auth does the rest.

### MSAL device code flow (Python)

```python
from msal import PublicClientApplication

CLIENT_ID = "825b77cb-1492-406f-9072-923aa536b328"
TENANT_ID = "<from env var POSTS_AZURE_TENANT_ID>"
SCOPE     = ["api://825b77cb-1492-406f-9072-923aa536b328/.default"]

app = PublicClientApplication(
    client_id=CLIENT_ID,
    authority=f"https://login.microsoftonline.com/{TENANT_ID}",
    token_cache=SerializableTokenCache(),   # backed by ~/.posts-cli-cache.json
)

# 1. Try silent first
result = app.acquire_token_silent(SCOPE, account=app.get_accounts()[0] if app.get_accounts() else None)

# 2. Fall back to device code
if not result:
    flow = app.initiate_device_flow(SCOPE)
    print(flow["message"])          # "Go to https://microsoft.com/devicelogin and enter XXXXX"
    result = app.acquire_token_by_device_flow(flow)

access_token = result["access_token"]
```

### posts-api base URL

Stored in env var `POSTS_API_BASE_URL`. When unset, script errors with a
clear message ("Set POSTS_API_BASE_URL to the Function App URL").

---

## Wave 1 — Core CLI

### Task 1: Create `post.py` and `requirements-cli.txt`

<read_first>
- posts-api/function_app.py — understand POST /api/posts request shape
- posts-api/auth.py — confirm X-MS-CLIENT-PRINCIPAL is the only auth requirement
</read_first>

<action>
Create `posts-api/post.py` with:

**Env vars read:**
- `POSTS_API_BASE_URL` — e.g. `https://posts-api-xxx.azurewebsites.net` (required)
- `POSTS_AZURE_TENANT_ID` — Azure AD tenant GUID (required)
- `POSTS_TOKEN_CACHE` — path to token cache file (default `~/.posts-cli-cache.json`)

**Client ID (hard-coded):** `825b77cb-1492-406f-9072-923aa536b328`

**Scope:** `["api://825b77cb-1492-406f-9072-923aa536b328/.default"]`

**Sub-commands:**

```
post.py list
    GET /api/posts  →  prints slug + title + published flag per post

post.py create --title "..." --description "..." [--body "..."] [--published]
    POST /api/posts  →  prints created slug

post.py update <slug> --title "..." --description "..." [--body "..."] [--published]
    PUT /api/posts/<slug>  →  prints "updated"

post.py delete <slug>
    DELETE /api/posts/<slug>  →  prints "deleted"
```

**Token cache:**
- Load from `POSTS_TOKEN_CACHE` path on startup (if file exists).
- After any network token acquisition, serialize cache back to the file.
- File permissions: `600` (owner-read-only) — enforce with `os.chmod`.

**Error handling:**
- HTTP 401 → "Auth failed. Run: python post.py login   (to force re-auth)"
- HTTP 4xx → print JSON error body
- HTTP 5xx → print status code + "posts-api returned a server error"
- Missing env vars → print which var is missing and exit 1

**Extra command `login`:**
- Forces a new device-code flow even if the cache exists (useful when token
  is truly expired and silent refresh is failing).
- `python post.py login` → runs device-code flow, saves cache, prints "Logged in as <email>".

Create `posts-api/requirements-cli.txt`:
```
msal>=1.28
requests>=2.31
```
</action>

<acceptance_criteria>
- `python post.py list` with a valid cache prints at least a header line ("No posts yet" or a slug list); exits 0.
- `python post.py create --title "Test CLI" --description "CLI smoke test"` returns a slug and exits 0 (given a valid token and a reachable API).
- `python post.py list` when `POSTS_API_BASE_URL` is unset exits 1 and prints a message containing "POSTS_API_BASE_URL".
- `~/.posts-cli-cache.json` (or `POSTS_TOKEN_CACHE` path) is created with mode 600 after `login`.
- `python post.py --help` prints sub-command list without errors.
- Running with a stale/missing cache and no network falls back gracefully (error message, not a Python traceback).
</acceptance_criteria>

---

### Task 2: Document usage in CLAUDE.md (ideas-bot)

<read_first>
- ideas-bot/CLAUDE.md (or bot.py top-level docstring) — understand what context the bot reads
</read_first>

<action>
Add a "Posting to the Writing API" section to `ideas-bot/CLAUDE.md` (create the
file if it doesn't exist) documenting:

1. Env vars needed: `POSTS_API_BASE_URL`, `POSTS_AZURE_TENANT_ID`, `POSTS_TOKEN_CACHE`.
2. How to call the CLI as a subprocess from the bot:
   ```python
   import subprocess, os
   result = subprocess.run(
       ["python", "path/to/post.py", "create",
        "--title", title, "--description", desc, "--body", body],
       capture_output=True, text=True, env=os.environ
   )
   ```
3. Note that the token cache must be pre-warmed by a human running
   `python post.py login` before the bot can use it headlessly.
</action>

<acceptance_criteria>
- ideas-bot/CLAUDE.md contains a section titled "Posting to the Writing API".
- Section lists all three env vars.
- Section includes the subprocess call pattern.
- Section mentions the `post.py login` pre-warm step.
</acceptance_criteria>

---

## Verification

### must_haves

**truths:**
- `post.py` exists in `posts-api/`
- `post.py` contains `PublicClientApplication` from `msal`
- `post.py` contains `initiate_device_flow`
- `post.py` contains `acquire_token_silent`
- `post.py` reads `POSTS_API_BASE_URL` from env
- `requirements-cli.txt` contains `msal`
- `~/.posts-cli-cache.json` path (or `POSTS_TOKEN_CACHE`) has mode 600 after login
- ideas-bot CLAUDE.md contains "Posting to the Writing API"

**behaviors:**
- `python post.py list` exits 0 with warm cache and reachable API
- `python post.py create --title T --description D` exits 0 and prints a slug
- `python post.py login` runs device-code flow (prompts with URL + code)
- Missing `POSTS_API_BASE_URL` → exit 1 with actionable message

---

## Setup steps for first use

```bash
# 1. Install CLI deps (separate from the Azure Function's requirements.txt)
pip install -r posts-api/requirements-cli.txt

# 2. Set env vars (add to ~/.profile or .env)
export POSTS_API_BASE_URL="https://<your-function-app>.azurewebsites.net"
export POSTS_AZURE_TENANT_ID="<your-tenant-guid>"

# 3. Warm the token cache (one-time browser sign-in)
python posts-api/post.py login

# 4. Use it
python posts-api/post.py list
python posts-api/post.py create --title "My Design Doc" --description "Options for X" --body "..."
```

---

## Notes

- The Function App must have Easy Auth configured for the Azure AD app
  `825b77cb-1492-406f-9072-923aa536b328`. If it isn't, `require_auth` will
  receive a request with no `X-MS-CLIENT-PRINCIPAL` and return 401.
- The `POSTS_AZURE_TENANT_ID` is the same tenant as `REACT_APP_AZURE_TENANT_ID`
  in the website's `.env`.
- `msal` handles token refresh automatically. The cached refresh token is
  valid for 90 days; `acquire_token_silent` extends it on each use.
- Ideas-bot usage: ensure `POSTS_TOKEN_CACHE` points to a persistent path
  (not a temp dir) so the cache survives between bot invocations.
