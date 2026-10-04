# Write/Diary usage telemetry

posts-api has an Application Insights resource (`posts-api-prod-insights`,
backed by the `posts-api-prod-logs` Log Analytics workspace — both defined in
`azure-infrastructure/modules/postsapi.bicep`). Azure Functions' built-in HTTP
request telemetry flows there automatically — no code changes needed in
`function_app.py`.

This is backend-only: it tells you what hit the API, not what happened in the
UI before that (e.g. someone opening the new-post editor but never saving
is invisible here, since no request fires until a save).

## How to query

Easiest: Azure Portal → `posts-api-prod-insights` → **Logs**, paste a query
below, run.

From the CLI:

```bash
WORKSPACE_ID=$(az monitor log-analytics workspace show \
  --resource-group my-website-prod-rg \
  --workspace-name posts-api-prod-logs \
  --query customerId -o tsv)

az monitor log-analytics query --workspace "$WORKSPACE_ID" \
  --analytics-query "<query>" -o table
```

Request telemetry lands in the `AppRequests` table. The function **name**
(`list_items`, `create_item`, `update_item`, `delete_item`, `get_item`,
`list_versions`, `get_version`, `diff_versions`, `health`) doesn't
distinguish writing vs. diary — that's in the URL path — so queries that need
the section extract it from `Url`.

## Queries

**Call volume by section and action, last 30 days** — what's actually used:

```kql
AppRequests
| where TimeGenerated > ago(30d)
| where Name != "health"
| extend section = extract(@"sections/(\w+)/items", 1, Url)
| where isnotempty(section)
| summarize count() by section, action = Name
| order by count_ desc
```

**Writing vs. diary, relative volume over time** — which section is actually
getting used, week over week:

```kql
AppRequests
| where TimeGenerated > ago(90d)
| extend section = extract(@"sections/(\w+)/items", 1, Url)
| where isnotempty(section)
| summarize count() by section, week = bin(TimeGenerated, 7d)
| order by week asc
```

**Create vs. delete ratio per section** — are posts/entries piling up
unfinished, or getting cleaned up:

```kql
AppRequests
| where TimeGenerated > ago(90d)
| extend section = extract(@"sections/(\w+)/items", 1, Url)
| where isnotempty(section) and Name in ("create_item", "delete_item")
| summarize count() by section, action = Name
```

**Errors and latency by endpoint** — anything failing or slow:

```kql
AppRequests
| where TimeGenerated > ago(30d)
| summarize count(), avgDuration = avg(DurationMs), errorRate = countif(Success == "False") * 100.0 / count() by Name
| order by errorRate desc
```

**Time-of-day / day-of-week usage pattern** — when writing actually happens:

```kql
AppRequests
| where TimeGenerated > ago(90d)
| where Name != "health"
| summarize count() by hourOfDay = datetime_part("hour", TimeGenerated)
| order by hourOfDay asc
```

## Notes

- Health check pings (`Name == "health"`, used by uptime monitoring) are
  excluded from the usage queries above — they're not real feature usage.
- `AppRequests` retention is 30 days by default on the Log Analytics
  workspace's `PerGB2018` SKU unless retention is changed in
  `postsapi.bicep` — the 90-day queries above only return what's actually
  retained.
