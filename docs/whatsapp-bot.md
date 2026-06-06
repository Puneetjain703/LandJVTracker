# WhatsApp Bot Setup

The WhatsApp bridge lets you interact with Land and JV Tracker from WhatsApp without opening Streamlit.

Recommended MVP shape:

```text
WhatsApp Cloud API -> Cloudflare Worker -> Neon Postgres -> Streamlit Approval Inbox
```

Streamlit remains the dashboard. The Worker is only the public webhook doorway required by WhatsApp.

If you want a third-party provider that gives/hosts the WhatsApp number and relays messages to your webhook, the common names are Twilio, Gupshup, 360dialog, WATI, Interakt, Vonage, and Bird/MessageBird. Twilio is the one most people remember as "they give me a number and POST messages to my webhook"; this Worker supports a Twilio-compatible endpoint.

## What It Can Do

- Receive WhatsApp text messages.
- Receive voice/audio messages and transcribe them when `OPENAI_API_KEY` is configured.
- Use OpenAI to classify unstructured messages, search candidate assets, answer from database rows, and extract new lead fields.
- Maintain a lightweight conversational flow by looking at recent WhatsApp messages from the same sender.
- Keep one active WhatsApp property draft per sender so you can send details, files, voice notes, and location in multiple messages.
- Move a WhatsApp draft into the Approval Inbox only after you reply `CONFIRM`.
- Reply with the draft id or approval queue id.
- Answer simple search questions from the confirmed `assets` table.
- Add timeline updates to an existing asset when the message mentions an asset code like `LJV-00012` or `asset 12`.
- Fuzzy-match an existing asset even when you do not mention an asset id, then ask a clarification if confidence is low.
- Attach WhatsApp media references to an existing asset when a media message mentions a matched asset.
- Log every received WhatsApp message in `whatsapp_messages`.

## What It Does Not Fully Solve Yet

- It does not read a personal WhatsApp account.
- It uses the official WhatsApp Business Cloud API, which needs a WhatsApp Business number.
- It does not permanently store media binaries yet. It records WhatsApp media ids and metadata. Add Cloudflare R2, S3, or Google Drive later for durable file storage.
- It does not auto-approve new properties. New leads wait in Approval Inbox.
- It does not push partial WhatsApp drafts into the visible approval list until you confirm the draft.

## Conversational Intake

You do not need to send everything in one message. The intended flow is:

```text
New land near Achrol, 5 bigha, suitable for farmhouse plotting, asking around 26 cr
```

Then send more messages as they come:

```text
Broker is Rakesh, owner is Sharma family, title papers need checking
```

Forward photos, PDFs, voice notes, or a WhatsApp location pin. The bot keeps merging them into the same active draft for your phone number.

When the bot summary looks right:

```text
CONFIRM
```

That moves the property to the Streamlit Approval Inbox. From there you can approve it into the main asset database.

To discard the active WhatsApp draft:

```text
CANCEL
```

To inspect the current draft:

```text
what is missing in this draft?
```

The bot will show the current extracted fields, documents count, map link when available, and missing fields such as owner, broker, price, area, or coordinates.

Existing asset updates still work separately. If you mention a recognizable existing property, the bot can add a timeline update or attach media to that asset. When the match is uncertain, it asks for clarification.

For ambiguous forwards, use explicit mode words:

```text
NEW
```

Starts a private draft. Documents, photos, map links, location pins, and voice notes after this are added to that draft.

```text
INFO show Queens Road JV opportunities
```

Searches the confirmed database without creating anything.

```text
UPDATE LJV-00029: spoke to broker, seller reduced price
```

Adds a timeline update to a confirmed asset.

```text
ATTACH LJV-00029
```

Attaches the current media/map reference to a confirmed asset. Without an explicit asset code/id, the bot asks you to clarify instead of guessing.

Map-only or document-only forwards are saved as private drafts. They do not enter the Approval Inbox and they do not attach to a guessed asset unless you explicitly say `ATTACH <asset code>`.

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
npx wrangler secret put APP_BASE_URL
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
APP_BASE_URL
```

For `DATABASE_URL`, use the same Neon connection string that works for Streamlit.

Set `APP_BASE_URL` to your public Streamlit app URL if you want WhatsApp replies to include contextual links back into the UI:

```text
https://YOUR-STREAMLIT-APP.streamlit.app
```

The bot only adds links when they are useful for the current action. Drafts link to WhatsApp Intake, confirmed leads link to Approval Inbox, and existing asset updates/documents link to the Asset Desk.

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

## Twilio Setup

If you use Twilio WhatsApp, set the inbound message webhook URL to:

```text
https://YOUR-WORKER.YOUR-SUBDOMAIN.workers.dev/twilio/webhook
```

Use HTTP `POST`.

Twilio sends text in `Body`, sender in `From`, and media links in fields such as `MediaUrl0`. The Worker replies with TwiML, so you do not need the Meta `WHATSAPP_ACCESS_TOKEN` just to respond through Twilio.

For Twilio-only mode, required Worker secrets are:

```text
DATABASE_URL
OPENAI_API_KEY
TWILIO_ACCOUNT_SID
TWILIO_AUTH_TOKEN
TWILIO_WEBHOOK_URL
APP_BASE_URL
```

Recommended:

```text
WHATSAPP_ALLOWED_SENDERS
TWILIO_VALIDATE_SIGNATURE=true
```

`TWILIO_WEBHOOK_URL` must exactly match the URL you paste into Twilio, for example:

```text
https://YOUR-WORKER.YOUR-SUBDOMAIN.workers.dev/twilio/webhook
```

The Worker validates Twilio's `X-Twilio-Signature` when `TWILIO_AUTH_TOKEN` is present. `TWILIO_ACCOUNT_SID` and `TWILIO_AUTH_TOKEN` are also used to fetch Twilio media URLs for voice transcription.

The Meta-specific secrets are only needed for direct Meta Cloud API mode:

```text
WHATSAPP_VERIFY_TOKEN
WHATSAPP_ACCESS_TOKEN
WHATSAPP_PHONE_NUMBER_ID
WHATSAPP_APP_SECRET
```

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

Natural-language examples that should work without asset IDs:

```text
Which Vaishali Nagar brokerage opportunities have high workability but missing owner details?
```

```text
I spoke to Rakesh about the Ajmer Road 12 bigha land. Seller has reduced ask to 16 cr but title papers are still pending.
```

```text
The broker sent this map for the commercial plot near Mansarovar. Attach it to the right property if you can identify it.
```

## How Data Flows

New property messages:

```text
whatsapp_messages -> approval_queue(status=draft) -> CONFIRM -> approval_queue(status=pending) -> Approval Inbox -> assets
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
