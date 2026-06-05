# Online Deployment Runbook

The recommended MVP deployment is now Streamlit-only:

- Streamlit app: `frontend/streamlit_app.py`
- Database: hosted Postgres such as Neon
- Scheduled ingestion: GitHub Actions at 7 AM and 7 PM IST

You do not need to host FastAPI for the online MVP. The FastAPI code remains in the repo for future API/mobile/React use, but Streamlit Cloud can run the app in direct database mode.

## Streamlit Community Cloud

Deploy:

- Repository: this repo
- Branch: `main`
- Main file path: `frontend/streamlit_app.py`
- Python version: choose Python `3.12` if Streamlit offers it. The app also avoids psycopg wheels online by using `pg8000`.

Streamlit Cloud installs `frontend/requirements.txt`, which includes the all-in-one Streamlit runtime and a pure-Python Postgres driver.

Set Streamlit secrets as root-level values:

```toml
APP_MODE = "direct"
DATABASE_DRIVER = "pg8000"
DATABASE_URL = "postgresql://USER:PASSWORD@HOST/DATABASE?sslmode=require"
API_SECRET_KEY = "a-long-random-value"
APP_USERNAME = "your-login"
APP_PASSWORD_HASH = "bcrypt-hash-from-scripts-hash-password"
AUTO_PUBLISH_INGESTED_ASSETS = "false"

OPENAI_API_KEY = "optional"
NOTION_API_KEY = "optional"
GOOGLE_SERVICE_ACCOUNT_JSON = "optional"
```

Do not set `API_BASE_URL` for Streamlit-only mode. If `API_BASE_URL` is present, the UI assumes you intentionally want a separate FastAPI backend.

Use the full connection string copied from Neon whenever possible. If you manually paste a password into the URL, encode symbols first. For example, `@` becomes `%40`, `#` becomes `%23`, `/` becomes `%2F`, `?` becomes `%3F`, `%` becomes `%25`, and `&` becomes `%26`. A malformed password in the URL can look exactly like a wrong-password error.

After changing Streamlit secrets, reboot the app from Streamlit Cloud so the running container reloads them.

If Neon still reports password authentication failure, temporarily add:

```toml
DB_DIAGNOSTICS = "true"
```

The login page will show a redacted database target plus password length and a short SHA256 prefix. Remove this setting after troubleshooting.

For Google Sheets sync, also set:

```toml
GOOGLE_SHEET_ID = "1LC7bnveXagIs8Kc4xIaxEMVcAkViJzX7KZQYyOJdmds"
GOOGLE_SHEET_TABS = "Master-2026,Master"
```

For Notion sync, set the relevant page ids only if overriding the defaults:

```toml
NOTION_PEARL_PROJECTS_PAGE_ID = "29a5c898ef91805c8f62caccbd26b0af"
NOTION_ANALYZE_LRM_PAGE_ID = "2995c898ef918040a360c467e4837e4c"
NOTION_BROKERAGE_NEW_DEALS_PAGE_ID = "29a5c898ef91801598afdcf276fe057b"
```

## What Direct Mode Does

In direct mode, Streamlit:

- protects the app behind the same login screen
- reads and writes directly to Postgres
- runs the approval workflow, edits, deletes, exports, imports, source syncs, and copilot actions without FastAPI
- creates missing database tables on startup if needed

This removes the `localhost:8000` login error because the online Streamlit app no longer calls a backend URL.

## GitHub Actions Scheduled Ingestion

The workflow is:

```text
.github/workflows/scheduled-ingestion.yml
```

It runs:

- `01:30 UTC` = `07:00 IST`
- `13:30 UTC` = `19:00 IST`
- manual `workflow_dispatch`

GitHub Actions does not read Streamlit Cloud secrets. Add the same source/database secrets under:

```text
Settings -> Secrets and variables -> Actions -> Repository secrets
```

Required for scheduled ingestion:

```text
DATABASE_URL
API_SECRET_KEY
APP_USERNAME
APP_PASSWORD_HASH
```

Add at least one source:

```text
NOTION_API_KEY
GOOGLE_SERVICE_ACCOUNT_JSON
```

Optional:

```text
OPENAI_API_KEY
OPENAI_MODEL
NOTION_PEARL_PROJECTS_PAGE_ID
NOTION_ANALYZE_LRM_PAGE_ID
NOTION_BROKERAGE_NEW_DEALS_PAGE_ID
GOOGLE_SHEET_ID
GOOGLE_SHEET_TABS
```

The workflow keeps:

```env
AUTO_PUBLISH_INGESTED_ASSETS=false
```

So synced properties wait in the approval queue first.

## Security Checklist

- Use `APP_PASSWORD_HASH`, not a plain password, online.
- Keep `API_SECRET_KEY`, `DATABASE_URL`, `NOTION_API_KEY`, `OPENAI_API_KEY`, and `GOOGLE_SERVICE_ACCOUNT_JSON` only in Streamlit/GitHub secrets.
- Do not set `API_BASE_URL` unless you intentionally deploy FastAPI later.
- Share the Google Sheet only with the Google service account email.
- Share only the required Notion pages and related task/note pages with the Notion integration.
- Keep `AUTO_PUBLISH_INGESTED_ASSETS=false` so synced items require approval.

## Future FastAPI Option

FastAPI is still useful later if you want a faster custom frontend, mobile access, external integrations, or an API for other internal tools. For now, Streamlit-only is cheaper and simpler.
