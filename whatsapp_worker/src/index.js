import { neon } from "@neondatabase/serverless";

const GRAPH_VERSION = "v20.0";

function json(data, status = 200) {
  return new Response(JSON.stringify(data, null, 2), {
    status,
    headers: { "content-type": "application/json; charset=utf-8" },
  });
}

function text(data, status = 200) {
  return new Response(data, { status, headers: { "content-type": "text/plain; charset=utf-8" } });
}

function boolEnv(value) {
  return String(value || "").trim().toLowerCase() === "true" || String(value || "").trim() === "1";
}

function cleanText(value) {
  return String(value || "").replace(/\s+/g, " ").trim();
}

function normalizeAssetType(value, fallback = "land") {
  const text = cleanText(value).toLowerCase();
  if (!text) return fallback;
  if (text.includes("jv") || text.includes("joint venture")) return "jv";
  if (text.includes("brokerage") || text.includes("mandate") || text.includes("commission")) return "brokerage_listing";
  if (text.includes("commercial")) return "commercial";
  if (text.includes("resale")) return "resale_unit";
  if (text.includes("rental") || text.includes("lease")) return "rental";
  if (text.includes("land") || text.includes("plot") || text.includes("farm")) return "land";
  return fallback;
}

async function sha256Prefix(value, length = 24) {
  const bytes = new TextEncoder().encode(value);
  const hash = await crypto.subtle.digest("SHA-256", bytes);
  return [...new Uint8Array(hash)].map((byte) => byte.toString(16).padStart(2, "0")).join("").slice(0, length);
}

async function verifyMetaSignature(request, env, rawBody) {
  if (!env.WHATSAPP_APP_SECRET) return true;
  const signature = request.headers.get("x-hub-signature-256") || "";
  const expectedPrefix = "sha256=";
  if (!signature.startsWith(expectedPrefix)) return false;
  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(env.WHATSAPP_APP_SECRET),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"]
  );
  const signed = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(rawBody));
  const expected = expectedPrefix + [...new Uint8Array(signed)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
  return signature.length === expected.length && signature === expected;
}

function sqlClient(env) {
  if (!env.DATABASE_URL) throw new Error("DATABASE_URL is required");
  return neon(env.DATABASE_URL);
}

async function ensureSchema(sql) {
  await sql`
    CREATE TABLE IF NOT EXISTS whatsapp_messages (
      id SERIAL PRIMARY KEY,
      wamid VARCHAR(255) NOT NULL UNIQUE,
      from_number VARCHAR(80),
      phone_number_id VARCHAR(120),
      message_type VARCHAR(80),
      body_text TEXT,
      transcription_text TEXT,
      media_id VARCHAR(255),
      media_mime_type VARCHAR(255),
      media_sha256 VARCHAR(255),
      media_filename VARCHAR(500),
      media_caption TEXT,
      raw_payload JSONB,
      intent VARCHAR(80),
      processing_status VARCHAR(80) DEFAULT 'received',
      approval_queue_id INTEGER REFERENCES approval_queue(id),
      asset_id INTEGER REFERENCES assets(id),
      response_text TEXT,
      error_message TEXT,
      created_at TIMESTAMPTZ DEFAULT now(),
      updated_at TIMESTAMPTZ DEFAULT now()
    )
  `;
}

async function sendWhatsAppText(env, to, body) {
  if (!env.WHATSAPP_ACCESS_TOKEN || !env.WHATSAPP_PHONE_NUMBER_ID || !to || !body) return;
  const response = await fetch(`https://graph.facebook.com/${GRAPH_VERSION}/${env.WHATSAPP_PHONE_NUMBER_ID}/messages`, {
    method: "POST",
    headers: {
      authorization: `Bearer ${env.WHATSAPP_ACCESS_TOKEN}`,
      "content-type": "application/json",
    },
    body: JSON.stringify({
      messaging_product: "whatsapp",
      to,
      type: "text",
      text: { preview_url: false, body: body.slice(0, 3900) },
    }),
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(`WhatsApp send failed: ${detail}`);
  }
}

async function mediaMetadata(env, mediaId) {
  if (!mediaId || !env.WHATSAPP_ACCESS_TOKEN) return {};
  const response = await fetch(`https://graph.facebook.com/${GRAPH_VERSION}/${mediaId}`, {
    headers: { authorization: `Bearer ${env.WHATSAPP_ACCESS_TOKEN}` },
  });
  if (!response.ok) return { media_id: mediaId, error: await response.text() };
  const meta = await response.json();
  return {
    media_id: mediaId,
    url: meta.url,
    mime_type: meta.mime_type,
    sha256: meta.sha256,
    file_size: meta.file_size,
  };
}

async function transcribeAudio(env, mediaId) {
  if (!env.OPENAI_API_KEY || !mediaId) return "";
  const meta = await mediaMetadata(env, mediaId);
  if (!meta.url) return "";
  const mediaResponse = await fetch(meta.url, {
    headers: { authorization: `Bearer ${env.WHATSAPP_ACCESS_TOKEN}` },
  });
  if (!mediaResponse.ok) return "";
  const blob = await mediaResponse.blob();
  const form = new FormData();
  form.append("model", env.OPENAI_TRANSCRIPTION_MODEL || "whisper-1");
  form.append("file", new File([blob], "whatsapp-audio.ogg", { type: meta.mime_type || "audio/ogg" }));
  const response = await fetch("https://api.openai.com/v1/audio/transcriptions", {
    method: "POST",
    headers: { authorization: `Bearer ${env.OPENAI_API_KEY}` },
    body: form,
  });
  if (!response.ok) return "";
  const data = await response.json();
  return cleanText(data.text);
}

function extractMessage(message) {
  const type = message.type || "unknown";
  const media = message[type] || {};
  const bodyText = type === "text" ? message.text?.body : media.caption || "";
  return {
    wamid: message.id,
    from: message.from,
    type,
    bodyText: cleanText(bodyText),
    mediaId: media.id || null,
    mediaMimeType: media.mime_type || null,
    mediaSha256: media.sha256 || null,
    mediaFilename: media.filename || null,
    mediaCaption: media.caption || null,
    raw: message,
  };
}

function assetReference(textValue) {
  const textLower = textValue.toLowerCase();
  const code = textValue.match(/\bLJV-\d+\b/i)?.[0]?.toUpperCase();
  const id = textLower.match(/\b(?:asset|id)\s*#?(\d+)\b/)?.[1];
  return { code, id: id ? Number(id) : null };
}

async function findAsset(sql, messageText) {
  const reference = assetReference(messageText);
  if (reference.code) {
    const rows = await sql`SELECT id, asset_code, title, locality, district, asset_type FROM assets WHERE asset_code = ${reference.code} LIMIT 1`;
    if (rows.length) return rows[0];
  }
  if (reference.id) {
    const rows = await sql`SELECT id, asset_code, title, locality, district, asset_type FROM assets WHERE id = ${reference.id} LIMIT 1`;
    if (rows.length) return rows[0];
  }
  const tokens = cleanText(messageText)
    .toLowerCase()
    .split(/[^a-z0-9/-]+/)
    .filter((token) => token.length > 3)
    .slice(0, 8);
  if (!tokens.length) return null;
  const search = `%${tokens.join("%")}%`;
  const rows = await sql`
    SELECT id, asset_code, title, locality, district, asset_type
    FROM assets
    WHERE lower(coalesce(title, '') || ' ' || coalesce(locality, '') || ' ' || coalesce(area_name, '') || ' ' || coalesce(district, '') || ' ' || coalesce(address, '')) LIKE ${search}
    ORDER BY updated_at DESC
    LIMIT 1
  `;
  return rows[0] || null;
}

function isQuestion(textValue) {
  const lower = textValue.toLowerCase();
  return lower.includes("?") || /^(what|which|show|find|search|list|tell|who|where|kitne|kaun|kya)\b/.test(lower);
}

function isNewLead(textValue, hasMediaWithoutAsset) {
  const lower = textValue.toLowerCase();
  return (
    hasMediaWithoutAsset ||
    /\b(new property|new deal|add property|add deal|ingest|land|plot|jv|brokerage|owner|broker|asking|price|bigha|acre)\b/.test(lower)
  );
}

async function answerQuestion(sql, textValue) {
  const tokens = cleanText(textValue)
    .toLowerCase()
    .split(/[^a-z0-9/-]+/)
    .filter((token) => token.length > 2)
    .slice(0, 10);
  const search = `%${tokens.join("%")}%`;
  const rows = await sql`
    SELECT id, asset_code, title, asset_type, status, locality, district, asking_price, workability_rating, bottleneck_rating
    FROM assets
    WHERE ${tokens.length === 0} OR lower(coalesce(asset_code, '') || ' ' || coalesce(title, '') || ' ' || coalesce(locality, '') || ' ' || coalesce(district, '') || ' ' || coalesce(status, '')) LIKE ${search}
    ORDER BY updated_at DESC
    LIMIT 5
  `;
  if (!rows.length) return "I could not find a matching property yet. Try an asset code, locality, owner, broker, or project name.";
  return rows
    .map((row) => {
      const price = row.asking_price ? ` | ask ${Number(row.asking_price).toLocaleString("en-IN")}` : "";
      return `${row.asset_code || row.id}: ${row.title} | ${row.asset_type} | ${row.locality || "-"}, ${row.district || "-"}${price}`;
    })
    .join("\n");
}

function fallbackLeadPayload(textValue, from, extracted = {}) {
  const titleMatch = textValue.match(/(?:property|plot|land|deal)\s+(?:at|in|near)?\s*([^.,\n]+)/i);
  const localityMatch = textValue.match(/(?:at|in|near)\s+([A-Za-z0-9 /-]{3,60})(?:,|\.|\n|$)/i);
  const areaMatch = textValue.match(/(\d+(?:\.\d+)?)\s*(bigha|acre|acres|sq\.?\s*ft|sqft|sqm|gaj|yard|yards)/i);
  const priceMatch = textValue.match(/(?:ask|asking|price|rate)\D{0,20}(\d+(?:\.\d+)?)\s*(cr|crore|lac|lakh)?/i);
  let price = null;
  if (priceMatch) {
    price = Number(priceMatch[1]);
    const unit = (priceMatch[2] || "").toLowerCase();
    if (unit === "cr" || unit === "crore") price *= 10000000;
    if (unit === "lac" || unit === "lakh") price *= 100000;
  }
  const payload = {
    title: cleanText(extracted.title || titleMatch?.[1] || textValue.slice(0, 120)),
    asset_type: normalizeAssetType(extracted.asset_type || textValue),
    status: extracted.status || "lead",
    source: "whatsapp",
    locality: cleanText(extracted.locality || localityMatch?.[1] || ""),
    district: cleanText(extracted.district || ""),
    land_area: cleanText(extracted.land_area || areaMatch?.[0] || ""),
    asking_price: extracted.asking_price || price,
    owner_name: cleanText(extracted.owner_name || ""),
    broker_name: cleanText(extracted.broker_name || ""),
    bottleneck_notes: cleanText(extracted.bottleneck_notes || textValue),
    raw_source: {
      whatsapp: {
        from,
        original_message: textValue,
        extracted_by: extracted.__source || "fallback",
        captured_at: new Date().toISOString(),
      },
    },
  };
  return Object.fromEntries(Object.entries(payload).filter(([, value]) => value !== "" && value !== null && value !== undefined));
}

async function openAiExtractLead(env, textValue, from) {
  if (!env.OPENAI_API_KEY) return fallbackLeadPayload(textValue, from);
  const response = await fetch("https://api.openai.com/v1/chat/completions", {
    method: "POST",
    headers: {
      authorization: `Bearer ${env.OPENAI_API_KEY}`,
      "content-type": "application/json",
    },
    body: JSON.stringify({
      model: env.OPENAI_MODEL || "gpt-4.1-mini",
      response_format: { type: "json_object" },
      temperature: 0,
      messages: [
        {
          role: "system",
          content:
            "Extract one Indian real-estate property/deal lead from WhatsApp text. Return JSON only. Valid asset_type: land, jv, brokerage_listing, commercial, resale_unit, rental, other. Do not invent missing facts.",
        },
        {
          role: "user",
          content:
            `Text: ${textValue}\n\nFields: title, asset_type, status, locality, area_name, tehsil, district, state, address, latitude, longitude, google_maps_link, land_area, built_up_area, asking_price, expected_price, owner_name, broker_name, workability_rating, bottleneck_rating, bottleneck_notes, legal_status, zoning_status, key_people.`,
        },
      ],
    }),
  });
  if (!response.ok) return fallbackLeadPayload(textValue, from);
  const data = await response.json();
  let parsed = {};
  try {
    parsed = JSON.parse(data.choices?.[0]?.message?.content || "{}");
  } catch (_) {
    parsed = {};
  }
  parsed.__source = "openai";
  return fallbackLeadPayload(textValue, from, parsed);
}

async function queueApproval(sql, message, from, payload) {
  const fingerprint = await sha256Prefix(
    [payload.title, payload.locality, payload.area_name, payload.district, payload.address, payload.land_area].filter(Boolean).join("|") ||
      JSON.stringify(payload)
  );
  payload.dedupe_fingerprint = payload.dedupe_fingerprint || fingerprint;
  payload.raw_source = payload.raw_source || {};
  payload.raw_source.whatsapp = {
    ...(payload.raw_source.whatsapp || {}),
    source_uid: `whatsapp:${message.wamid}`,
    media_id: message.mediaId,
    media_filename: message.mediaFilename,
    media_caption: message.mediaCaption,
    message_type: message.type,
  };
  const rows = await sql`
    INSERT INTO approval_queue (source, source_uid, title, payload, status, created_by_source)
    VALUES ('whatsapp', ${`whatsapp:${message.wamid}`}, ${payload.title || "WhatsApp property lead"}, ${JSON.stringify(payload)}::jsonb, 'pending', ${`WhatsApp ${from}`})
    ON CONFLICT (source, source_uid) DO UPDATE
      SET payload = excluded.payload,
          title = excluded.title,
          updated_at = now()
    RETURNING id
  `;
  return rows[0]?.id;
}

async function addAssetUpdate(sql, asset, messageText, from) {
  await sql`
    INSERT INTO asset_updates (asset_id, update_type, update_text, created_by)
    VALUES (${asset.id}, 'whatsapp_note', ${messageText}, ${`whatsapp:${from}`})
  `;
}

async function addAssetDocument(sql, asset, message, from) {
  await sql`
    INSERT INTO asset_documents (asset_id, document_name, document_type, storage_path, notes)
    VALUES (
      ${asset.id},
      ${message.mediaFilename || message.mediaCaption || `WhatsApp media ${message.mediaId}`},
      ${message.type || "whatsapp_media"},
      ${`whatsapp:${message.mediaId}`},
      ${`Captured from WhatsApp sender ${from}. Media ID: ${message.mediaId || "-"}${message.mediaCaption ? `. Caption: ${message.mediaCaption}` : ""}`}
    )
  `;
}

async function logMessage(sql, record) {
  await sql`
    INSERT INTO whatsapp_messages (
      wamid, from_number, phone_number_id, message_type, body_text, transcription_text, media_id, media_mime_type,
      media_sha256, media_filename, media_caption, raw_payload, intent, processing_status, approval_queue_id,
      asset_id, response_text, error_message
    )
    VALUES (
      ${record.wamid}, ${record.from_number}, ${record.phone_number_id}, ${record.message_type}, ${record.body_text},
      ${record.transcription_text}, ${record.media_id}, ${record.media_mime_type}, ${record.media_sha256},
      ${record.media_filename}, ${record.media_caption}, ${JSON.stringify(record.raw_payload || {})}::jsonb,
      ${record.intent}, ${record.processing_status}, ${record.approval_queue_id}, ${record.asset_id},
      ${record.response_text}, ${record.error_message}
    )
    ON CONFLICT (wamid) DO NOTHING
  `;
}

async function handleIncomingMessage(sql, env, phoneNumberId, message) {
  const extracted = extractMessage(message);
  const allowed = cleanText(env.WHATSAPP_ALLOWED_SENDERS || "")
    .split(",")
    .map((value) => cleanText(value))
    .filter(Boolean);
  if (allowed.length && !allowed.includes(extracted.from)) {
    await logMessage(sql, {
      wamid: extracted.wamid,
      from_number: extracted.from,
      phone_number_id: phoneNumberId,
      message_type: extracted.type,
      body_text: extracted.bodyText,
      raw_payload: extracted.raw,
      intent: "unauthorized",
      processing_status: "ignored",
      response_text: "Sender is not allowlisted.",
    });
    return;
  }

  let transcript = "";
  if (extracted.type === "audio" || extracted.type === "voice") {
    transcript = await transcribeAudio(env, extracted.mediaId);
  }
  const messageText = cleanText([extracted.bodyText, transcript].filter(Boolean).join("\n"));
  let intent = "unknown";
  let status = "received";
  let responseText = "";
  let approvalId = null;
  let assetId = null;
  let errorMessage = null;

  try {
    const asset = await findAsset(sql, messageText);
    if (messageText.toLowerCase() === "help" || messageText.toLowerCase().startsWith("/help")) {
      intent = "help";
      status = "answered";
      responseText =
        "Send a property note to queue a lead. Mention LJV-xxxxx or asset 123 to update an existing property. Send questions like 'show active Vaishali Nagar brokerage'. Voice notes are transcribed when OPENAI_API_KEY is set.";
    } else if (isQuestion(messageText)) {
      intent = "query";
      status = "answered";
      responseText = await answerQuestion(sql, messageText);
    } else if (asset && extracted.mediaId) {
      intent = "attach_document";
      status = "attached_document";
      assetId = asset.id;
      await addAssetDocument(sql, asset, extracted, extracted.from);
      responseText = `Attached WhatsApp media to ${asset.asset_code || asset.id}: ${asset.title}`;
    } else if (asset && messageText) {
      intent = "update_asset";
      status = "updated_asset";
      assetId = asset.id;
      await addAssetUpdate(sql, asset, messageText, extracted.from);
      responseText = `Updated ${asset.asset_code || asset.id}: ${asset.title}`;
    } else if (isNewLead(messageText, Boolean(extracted.mediaId))) {
      intent = "queue_new_lead";
      status = "queued";
      const payload = await openAiExtractLead(env, messageText || extracted.mediaCaption || "WhatsApp media lead", extracted.from);
      approvalId = await queueApproval(sql, extracted, extracted.from, payload);
      responseText = `Queued for approval #${approvalId}: ${payload.title || "WhatsApp property lead"}. Open Approval Inbox to review before it enters assets.`;
    } else {
      intent = "needs_clarification";
      status = "answered";
      responseText = "I could not tell if this is a question, property update, or new lead. Send 'help' for examples, or include an asset code like LJV-00001.";
    }
  } catch (error) {
    status = "failed";
    errorMessage = error instanceof Error ? error.message : String(error);
    responseText = `WhatsApp bot hit an error: ${errorMessage.slice(0, 500)}`;
  }

  await logMessage(sql, {
    wamid: extracted.wamid,
    from_number: extracted.from,
    phone_number_id: phoneNumberId,
    message_type: extracted.type,
    body_text: extracted.bodyText || messageText,
    transcription_text: transcript,
    media_id: extracted.mediaId,
    media_mime_type: extracted.mediaMimeType,
    media_sha256: extracted.mediaSha256,
    media_filename: extracted.mediaFilename,
    media_caption: extracted.mediaCaption,
    raw_payload: extracted.raw,
    intent,
    processing_status: status,
    approval_queue_id: approvalId,
    asset_id: assetId,
    response_text: responseText,
    error_message: errorMessage,
  });

  if (responseText) await sendWhatsAppText(env, extracted.from, responseText);
}

async function handlePost(request, env) {
  const rawBody = await request.text();
  if (!(await verifyMetaSignature(request, env, rawBody))) return text("Invalid signature", 403);
  const payload = JSON.parse(rawBody || "{}");
  const sql = sqlClient(env);
  await ensureSchema(sql);
  const tasks = [];
  for (const entry of payload.entry || []) {
    for (const change of entry.changes || []) {
      const value = change.value || {};
      const phoneNumberId = value.metadata?.phone_number_id || env.WHATSAPP_PHONE_NUMBER_ID || "";
      for (const message of value.messages || []) {
        tasks.push(handleIncomingMessage(sql, env, phoneNumberId, message));
      }
    }
  }
  await Promise.all(tasks);
  return text("EVENT_RECEIVED");
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname === "/health") return json({ status: "ok", service: "land-jv-whatsapp-bot" });
    if (url.pathname !== "/webhook") return text("Not found", 404);

    if (request.method === "GET") {
      const mode = url.searchParams.get("hub.mode");
      const token = url.searchParams.get("hub.verify_token");
      const challenge = url.searchParams.get("hub.challenge");
      if (mode === "subscribe" && token && token === env.WHATSAPP_VERIFY_TOKEN) return text(challenge || "");
      return text("Forbidden", 403);
    }
    if (request.method === "POST") return handlePost(request, env);
    return text("Method not allowed", 405);
  },
};
