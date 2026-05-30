# Land and JV Tracker

Production-minded MVP for an internal brokerage, land, JV, resale, RE asset, and brokerage-led opportunity tracker.

## What is included

- FastAPI backend with authenticated endpoints
- Streamlit MVP frontend
- PostgreSQL schema for assets, deals, contacts, organizations, brokers, owners, documents, locations, updates, tags, approvals, sync logs, ingestion logs, CRM profiles, match suggestions, and AI query logs
- Excel import with direct asset publishing by default, or optional approval queue review
- Notion sync from the configured project pages with direct asset publishing by default
- Approval/rejection workflow remains available when `AUTO_PUBLISH_INGESTED_ASSETS=false`
- Map coordinates, Google Maps links, and no-key best-effort geocoding through OpenStreetMap Nominatim
- Read-only AI assistant over retrieved asset rows
- Placeholder source path for future WhatsApp ingestion
- CRM matching foundation tables
- People/institution relationship layer for brokers, landowners, partners, financiers, banks, referrers, and other roles on each asset

Personal WhatsApp account reading is not implemented in this MVP. The schema and approval queue are ready for future `whatsapp` lead extraction.

## Quick start

```bash
cd land-jv-tracker
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
docker compose up -d postgres
python scripts/init_db.py
uvicorn backend.app.main:app --reload --port 8000
```

In a second terminal:

```bash
cd land-jv-tracker
source .venv/bin/activate
streamlit run frontend/streamlit_app.py --server.port 8501
```

Open:

- API health: http://localhost:8000/health
- Streamlit app: http://localhost:8501

Default MVP login comes from `.env`:

- `APP_USERNAME=admin`
- `APP_PASSWORD=change-me`

For a safer local password, run:

```bash
python scripts/hash_password.py
```

Then paste the result into `APP_PASSWORD_HASH` and clear `APP_PASSWORD`.

## Environment variables

All secrets live in `.env`; do not commit it.

Required for normal local use:

- `DATABASE_URL`
- `API_SECRET_KEY`
- `APP_USERNAME`
- `APP_PASSWORD` or `APP_PASSWORD_HASH`

Optional:

- `OPENAI_API_KEY`
- `OPENAI_MODEL`
- `NOTION_API_KEY`
- `NOTION_DATABASE_ID`
- `NOTION_PEARL_PROJECTS_PAGE_ID` for the Pearl Spytech project page relation sync
- `NOTION_ANALYZE_LRM_PAGE_ID` for the Analyze LRM project page relation sync
- `NOTION_BROKERAGE_NEW_DEALS_PAGE_ID` for the Brokerage New Deals project page
- `GOOGLE_SHEET_ID`
- `GOOGLE_SHEET_TABS`
- `GOOGLE_SERVICE_ACCOUNT_FILE` or `GOOGLE_SERVICE_ACCOUNT_JSON`
- `NOTION_SOURCE_NAME`

If `NOTION_DATABASE_ID` is blank, the sync script attempts to find a Notion database named `Pearl Spytech New Projects` through the Notion search API.

## API endpoints

- `GET /health`
- `POST /login`
- `POST /logout`
- `GET /stats`
- `GET /assets`
- `GET /assets/{asset_id}`
- `POST /assets`
- `PUT /assets/{asset_id}`
- `POST /assets/{asset_id}/contacts`
- `POST /assets/{asset_id}/documents`
- `POST /assets/{asset_id}/updates`
- `GET /approvals`
- `POST /approvals/{id}/approve`
- `POST /approvals/{id}/reject`
- `POST /import/excel`
- `GET /export/excel`
- `POST /sync/notion`
- `POST /ask`
- `GET /owners`, `POST /owners`
- `GET /brokers`, `POST /brokers`

## Excel import behavior

The importer accepts `.xlsx` and `.xls`, maps likely column names flexibly, stores the original row JSON in `raw_source`, classifies each row as `land`, `jv`, or `brokerage_listing`, and publishes directly to `assets` when `AUTO_PUBLISH_INGESTED_ASSETS=true`. Set that flag to `false` if you want rows to wait in the approval inbox.

Likely aliases are supported for fields such as title, property, asset type, location, district, tehsil, land area, asking price, legal status, zoning, and bottleneck notes.

## Notion sync

Run manually from the UI or from a cron/GitHub Actions-friendly command:

```bash
python scripts/sync_notion.py
```

The sync is rerunnable. It uses each source row/page id plus a property fingerprint to skip repeated assets already in the confirmed database or approval queue.

## AI assistant

The `/ask` endpoint does not execute model-generated SQL. It performs a read-only retrieval of relevant asset rows, then asks OpenAI to summarize those rows if `OPENAI_API_KEY` is configured. Without an OpenAI key, it returns a deterministic fallback summary.

Questions and answers are logged in `ai_query_logs`.

## Editing and exporting

Confirmed records live in the main relational tables, especially `assets`, `asset_updates`, `asset_documents`, `contacts`, `owners`, and `brokers`.

- Use `Add / Edit` to create or update an asset.
- Use an asset's detail tabs to add updates, documents, and contacts.
- Use `Approvals` to edit imported/Notion rows before approving them into `assets`.
- Use `Export` in the Streamlit app, or `GET /export/excel`, to download a multi-sheet workbook backup.

The export includes confirmed assets, related updates/documents/contacts, the approval queue, import/sync logs, CRM foundation tables, and AI query logs.

## People and roles

Each asset can have multiple linked people or institutions through `contacts` and `asset_contacts`. Use the `People & Roles` tab inside a property file to link brokers, landowners, possible partners, financiers, banks, buyers, sellers, developers, legal advisors, and referrers. The `People` workspace and asset filters can then list all properties associated with a selected person, institution, or role.

## Schema setup

There are two setup paths:

```bash
python scripts/init_db.py
```

or, for SQL-first PostgreSQL setup:

```bash
psql "$DATABASE_URL" -f migrations/001_initial_schema.sql
```

The Python path uses the SQLAlchemy models as the source of truth.

## Deployment notes

- Run PostgreSQL as a managed database or via the included Docker Compose file.
- Set `API_SECRET_KEY` to a long random value.
- Configure the FastAPI backend behind internal network access or a reverse proxy with HTTPS.
- Keep Streamlit behind internal auth or VPN. The MVP app also requires the API login token.
- Use `scripts/sync_notion.py` from cron or GitHub Actions with environment variables injected as secrets.
- Use `scripts/sync_all_sources.py` from cron or GitHub Actions to listen to Google Sheets, Pearl Spytech Notion, and Brokerage New Deals Notion together.
- Keep `OPENAI_API_KEY` and `NOTION_API_KEY` only in the runtime environment.

For any hosted PostgreSQL service, set `DATABASE_URL` to its SQLAlchemy-compatible URL, for example:

```env
DATABASE_URL=postgresql+psycopg2://USER:PASSWORD@HOST:5432/DATABASE?sslmode=require
```

Then run:

```bash
python scripts/init_db.py
```

After that, the same FastAPI and Streamlit app will read/write to the hosted database instead of the local preview database.

To move an existing local preview database into hosted Postgres, set `DATABASE_URL` first and run:

```bash
PYTHONPATH=. python scripts/migrate_sqlite_to_postgres.py local_preview.db
```

If the destination already has rows and you intentionally want to replace them, add `--replace`.

## Scheduled ingestion

The combined listener is:

```bash
PYTHONPATH=. python scripts/sync_all_sources.py
```

It reads:

- Google Sheet tabs `Master-2026` and `Master`
- Notion project page `Pearl Spytech New Projects`, including its Tasks and Notes relations
- Notion project page `Analyze the Property deals and update LRM`, including its Tasks and Notes relations
- Notion project page `Brokerage New Deals`, including its Tasks and Notes relations

By default, imported properties are auto-classified and inserted into the confirmed `assets` table as land purchase (`land`), brokerage opportunity (`brokerage_listing`), or joint venture (`jv`). Set `AUTO_PUBLISH_INGESTED_ASSETS=false` to send new items to `approval_queue` first. The sync checks existing approval items and confirmed assets with source ids and property fingerprints, so repeated properties are skipped.

For Google Sheets, create a Google Cloud service account, put the JSON path in `GOOGLE_SERVICE_ACCOUNT_FILE` or the raw JSON in `GOOGLE_SERVICE_ACCOUNT_JSON`, and share the sheet with the service account email.

For Notion project-page sync, the app reads only the configured project pages and their `Tasks` / `Notes` relation properties. The current project IDs are configured as `NOTION_PEARL_PROJECTS_PAGE_ID`, `NOTION_ANALYZE_LRM_PAGE_ID`, and `NOTION_BROKERAGE_NEW_DEALS_PAGE_ID`.

Current Notion source links:

- Pearl Spytech New Projects: https://www.notion.so/Pearl-Spytech-New-Projects-29a5c898ef91805c8f62caccbd26b0af
- Analyze the Property deals and update LRM: https://www.notion.so/Analyze-the-Property-deals-and-update-LRM-2995c898ef918040a360c467e4837e4c
- Brokerage New Deals: https://www.notion.so/Brokerage-New-Deals-29a5c898ef91801598afdcf276fe057b

If sync says relation entries are hidden, the project page itself is visible but Notion is not returning the related Task/Note pages to the integration. Open the project page, inspect the Tasks and Notes sections, and make sure those related pages are visible to the `Land/JV/Brokerage Tracker` integration, then rerun Sync.

If `OPENAI_API_KEY` is set, Notion Tasks/Notes are processed with an AI extractor that converts raw page properties and body text into structured asset fields. If it is not set, the listener uses a deterministic fallback parser and still stores the raw source data.


## AI database agent

The Streamlit page `AI DB Agent` lets an internal user give natural-language instructions such as adding an asset update, changing status, changing asking price, or changing workability/bottleneck ratings. The backend first creates a proposed action plan and the UI requires confirmation before applying edits. Only allowlisted asset fields and timeline updates can be modified; delete actions are not supported.

Set `OPENAI_API_KEY` to enable richer OpenAI planning. Without it, the agent uses a conservative fallback parser for direct asset ID/code instructions.

## Future expansion points

- WhatsApp ingestion: add extractors that write `source='whatsapp'` payloads into `approval_queue`.
- CRM matching: populate `crm_profiles`, score candidates, and write suggestions into `asset_match_suggestions`.
- Documents: attach storage-backed files to `asset_documents`.
- Timeline: expose `asset_updates` composer in the Streamlit detail page.
- Role-based auth: replace the MVP env-user login with database users and roles.
# LandJVTracker
