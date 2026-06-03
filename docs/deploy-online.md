# Online Deployment Runbook

This app has two runtime pieces:

- Streamlit frontend: `frontend/streamlit_app.py`
- FastAPI backend: `backend.app.main:app`

Streamlit Community Cloud is a good fit for the frontend. The FastAPI backend still needs its own HTTPS host, such as Render, Fly.io, Railway, Google Cloud Run, AWS App Runner, or an EC2/VPS. The scheduled ingestion should run independently through GitHub Actions so it does not depend on Codex or your laptop.

## Recommended MVP Hosting Shape

1. Managed Postgres: Neon, Supabase, RDS, Cloud SQL, or similar.
2. FastAPI backend: deploy with Docker using `MODE=backend`.
3. Streamlit frontend: deploy `frontend/streamlit_app.py` on Streamlit Community Cloud.
4. Scheduled ingestion: GitHub Actions runs `scripts/sync_all_sources.py` at 07:00 and 19:00 IST.

## Backend Deployment

Use `render.yaml` as the ready blueprint if deploying on Render.

Required backend environment variables:

```env
ENVIRONMENT=production
MODE=backend
DATABASE_URL=postgresql+psycopg://USER:PASSWORD@HOST:5432/DATABASE?sslmode=require
API_SECRET_KEY=<long random value>
APP_USERNAME=<private login username>
APP_PASSWORD_HASH=<bcrypt hash from scripts/hash_password.py>
AUTO_PUBLISH_INGESTED_ASSETS=false
ENABLE_BACKGROUND_SYNC=false
OPENAI_API_KEY=<optional>
NOTION_API_KEY=<required for Notion sync>
GOOGLE_SERVICE_ACCOUNT_JSON=<required for Google Sheets sync>
```

Use either `postgresql+psycopg://...` or a plain hosted URL like `postgresql://...`. The app normalizes plain Postgres URLs to the `psycopg` SQLAlchemy driver automatically.

Do not use `APP_PASSWORD` online. Use `APP_PASSWORD_HASH`.

After the backend deploys, open:

```text
https://YOUR-BACKEND-HOST/health
```

Expected:

```json
{"status":"ok","app":"Land and JV Tracker"}
```

In production, FastAPI docs and OpenAPI are disabled.

## Streamlit Community Cloud

Deploy:

- Repository: this repo
- Branch: your production branch
- Main file path: `frontend/streamlit_app.py`
- Advanced settings Python version: choose Python `3.12` for the most stable dependency support.

Set Streamlit secrets as root-level values:

```toml
API_BASE_URL = "https://YOUR-BACKEND-HOST"
```

Root-level Streamlit secrets are available as environment variables, so the frontend can read `API_BASE_URL` through `os.getenv`.

The Streamlit frontend has its own login screen, but all sensitive data still comes from the backend. If someone bypasses the UI, the backend requires a bearer token for every data endpoint.

## GitHub Actions Scheduled Ingestion

The workflow is:

```text
.github/workflows/scheduled-ingestion.yml
```

It runs:

- `01:30 UTC` = `07:00 IST`
- `13:30 UTC` = `19:00 IST`
- manual `workflow_dispatch`

Set these GitHub repository secrets under:

```text
Settings -> Secrets and variables -> Actions -> Repository secrets
```

Required:

```text
DATABASE_URL
API_SECRET_KEY
APP_USERNAME
APP_PASSWORD_HASH
NOTION_API_KEY
GOOGLE_SERVICE_ACCOUNT_JSON
```

Optional:

```text
OPENAI_API_KEY
OPENAI_MODEL
OPENAI_TRANSCRIPTION_MODEL
NOTION_DATABASE_ID
NOTION_SOURCE_NAME
NOTION_PEARL_PROJECTS_PAGE_ID
NOTION_ANALYZE_LRM_PAGE_ID
NOTION_ANALYZE_LRM_SOURCE_NAME
NOTION_BROKERAGE_NEW_DEALS_PAGE_ID
NOTION_BROKERAGE_SOURCE_NAME
GOOGLE_SHEET_ID
GOOGLE_SHEET_TABS
```

The workflow sets:

```env
AUTO_PUBLISH_INGESTED_ASSETS=false
```

So new source items go to the approval queue first.

## Security Checklist

- `ENVIRONMENT=production` on the backend.
- Strong `API_SECRET_KEY`; do not reuse the local dev value.
- Use `APP_PASSWORD_HASH`, not plain `APP_PASSWORD`.
- Keep `.env` out of git.
- Keep `GOOGLE_SERVICE_ACCOUNT_JSON`, `NOTION_API_KEY`, `OPENAI_API_KEY`, and database credentials only in host/GitHub/Streamlit secrets.
- Share the Google Sheet only with the Google service account email.
- Share only the required Notion pages/databases with the Notion integration.
- Keep `AUTO_PUBLISH_INGESTED_ASSETS=false` so synced items require approval.

Current backend hardening:

- Data endpoints require bearer-token auth.
- `/health` and `/login` are the only public API endpoints.
- Failed login attempts are throttled.
- Production disables `/docs`, `/redoc`, and `/openapi.json`.
- Basic no-cache and security headers are applied.

## Going Faster Later

For a very responsive production app, move the backend and Postgres to the same region and keep Streamlit close to that backend. The current architecture is fine for MVP operations, but a React frontend later will be faster than Streamlit for heavy tables, maps, and chat workflows.
