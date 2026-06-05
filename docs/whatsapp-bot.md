# WhatsApp Bot Setup

The WhatsApp bridge lets you interact with Land and JV Tracker from WhatsApp without opening Streamlit.

Recommended MVP shape:

```text
WhatsApp Cloud API -> Cloudflare Worker -> Neon Postgres -> Streamlit Approval Inbox
```

Streamlit remains the dashboard. The Worker is only the public webhook doorway required by WhatsApp.

## What It Can Do

- Receive WhatsApp text messages.
- Receive voice/audio messages and transcribe them when `OPENAI_API_KEY` is configured.
- Queue new property/deal leads in `approval_queue` with `source='whatsapp'`.
- Reply with the approval queue id.
- Answer simple search questions from the confirmed `assets` table.
- Add timeline updates to an existing asset when the message mentions an asset code like `LJV-00012` or `asset 12`.
- Attach WhatsApp media references to an existing asset when a media message mentions a matched asset.
- Log every received WhatsApp message in `whatsapp_messages`.

## What It Does Not Fully Solve Yet

- It does not read a personal WhatsApp account.
- It uses the official WhatsApp Business Cloud API, which needs a WhatsApp Business number.
- It does not permanently store media binaries yet. It records WhatsApp media ids and metadata. Add Cloudflare R2, S3, or Google Drive later for durable file storage.
- It does not auto-approve new properties. New leads wait in Approval Inbox.

## Deploy The Worker

From the repo root:

```bash
cd whatsapp_worker
cp wrangler.toml.example wrangler.toml
npm install
npx wrangler login
npx wrangler deploy
```

Set Worker secrets:

```bash
npx wrangler secret put DATABASE_URL
npx wrangler secret put WHATSAPP_VERIFY_TOKEN
npx wrangler secret put WHATSAPP_ACCESS_TOKEN
npx wrangler secret put WHATSAPP_PHONE_NUMBER_ID
npx wrangler secret put WHATSAPP_APP_SECRET
npx wrangler secret put OPENAI_API_KEY
```

Optional sender allowlist:

```bash
npx wrangler secret put WHATSAPP_ALLOWED_SENDERS
```

Use phone numbers without `+`, comma-separated:

```text
919999999999,918888888888
```

## Worker Secrets

Required:

```text
DATABASE_URL
WHATSAPP_VERIFY_TOKEN
WHATSAPP_ACCESS_TOKEN
WHATSAPP_PHONE_NUMBER_ID
```

Recommended:

```text
WHATSAPP_APP_SECRET
OPENAI_API_KEY
```

Optional:

```text
OPENAI_MODEL
OPENAI_TRANSCRIPTION_MODEL
WHATSAPP_ALLOWED_SENDERS
AUTO_APPLY_EXISTING_ASSET_UPDATES
REQUIRE_APPROVAL_FOR_NEW_ASSETS
```

For `DATABASE_URL`, use the same Neon connection string that works for Streamlit.

## Meta WhatsApp Cloud API

In Meta Developers:

1. Create or open a Meta app with WhatsApp enabled.
2. Add a WhatsApp Business phone number.
3. Set the webhook callback URL:

```text
https://YOUR-WORKER.YOUR-SUBDOMAIN.workers.dev/webhook
```

4. Set verify token to the same value as `WHATSAPP_VERIFY_TOKEN`.
5. Subscribe to the `messages` webhook field.
6. Send a test WhatsApp message to the business number.

Useful first messages:

```text
help
```

```text
New land lead near Ajmer Road, 12 bigha, owner Sharma, broker Rakesh, asking 18 cr. Title unclear.
```

```text
Update LJV-00012: spoke to broker Rakesh today. Seller revised asking to 16 cr and wants quick token.
```

```text
Show active brokerage properties in Jaipur
```

## How Data Flows

New property messages:

```text
whatsapp_messages -> approval_queue -> Approval Inbox -> assets
```

Existing asset updates:

```text
whatsapp_messages -> asset_updates
```

Media for existing assets:

```text
whatsapp_messages -> asset_documents
```

## Database Setup

The Streamlit app and `scripts/init_db.py` create the `whatsapp_messages` table through SQLAlchemy. There is also a SQL migration:

```bash
psql "$DATABASE_URL" -f migrations/002_whatsapp_intake.sql
```

The Worker also creates `whatsapp_messages` defensively on startup if it is missing.

## Security Notes

- Set `WHATSAPP_ALLOWED_SENDERS` once you know which team phone numbers should control the bot.
- Set `WHATSAPP_APP_SECRET` so the Worker verifies Meta webhook signatures.
- Keep `WHATSAPP_ACCESS_TOKEN`, `DATABASE_URL`, and `OPENAI_API_KEY` only in Worker secrets.
- New properties are approval-first by design.
