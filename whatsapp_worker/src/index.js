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

function tokensForSearch(value) {
  return cleanText(value)
    .toLowerCase()
    .split(/[^a-z0-9/-]+/)
    .filter((token) => token.length > 2 && !["the", "and", "for", "with", "that", "this"].includes(token))
    .slice(0, 18);
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

function base64FromBytes(bytes) {
  let binary = "";
  for (const byte of new Uint8Array(bytes)) binary += String.fromCharCode(byte);
  return btoa(binary);
}

async function hmacSha1Base64(secret, value) {
  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(secret),
    { name: "HMAC", hash: "SHA-1" },
    false,
    ["sign"]
  );
  return base64FromBytes(await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(value)));
}

async function verifyTwilioSignature(request, env, params) {
  if (!env.TWILIO_AUTH_TOKEN || boolEnv(env.TWILIO_VALIDATE_SIGNATURE) === false) return true;
  const provided = request.headers.get("x-twilio-signature") || "";
  if (!provided) return false;
  const webhookUrl = env.TWILIO_WEBHOOK_URL || request.url;
  const sorted = [...params.entries()].sort(([left], [right]) => left.localeCompare(right));
  const basis = sorted.reduce((current, [key, value]) => current + key + value, webhookUrl);
  const expected = await hmacSha1Base64(env.TWILIO_AUTH_TOKEN, basis);
  return provided.length === expected.length && provided === expected;
}

function twilioMediaHeaders(env, mediaUrl) {
  if (!String(mediaUrl || "").includes("api.twilio.com") || !env.TWILIO_ACCOUNT_SID || !env.TWILIO_AUTH_TOKEN) return {};
  return {
    authorization: `Basic ${btoa(`${env.TWILIO_ACCOUNT_SID}:${env.TWILIO_AUTH_TOKEN}`)}`,
  };
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
  const isUrl = String(mediaId).startsWith("http://") || String(mediaId).startsWith("https://");
  const meta = isUrl ? { url: mediaId, mime_type: "audio/ogg" } : await mediaMetadata(env, mediaId);
  if (!meta.url) return "";
  const headers = isUrl
    ? twilioMediaHeaders(env, meta.url)
    : !env.WHATSAPP_ACCESS_TOKEN
      ? {}
      : { authorization: `Bearer ${env.WHATSAPP_ACCESS_TOKEN}` };
  const mediaResponse = await fetch(meta.url, { headers });
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
  const location = type === "location" ? message.location || {} : message.location || {};
  const locationText = [location.name, location.address].filter(Boolean).join(" - ");
  const bodyText = type === "text" ? message.text?.body : type === "location" ? locationText : media.caption || "";
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
    latitude: location.latitude === undefined || location.latitude === null || location.latitude === "" ? null : Number(location.latitude),
    longitude: location.longitude === undefined || location.longitude === null || location.longitude === "" ? null : Number(location.longitude),
    locationName: cleanText(location.name || ""),
    locationAddress: cleanText(location.address || ""),
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

async function recentConversation(sql, from, limit = 8) {
  if (!from) return [];
  return await sql`
    SELECT body_text, transcription_text, intent, response_text, created_at
    FROM whatsapp_messages
    WHERE from_number = ${from}
    ORDER BY created_at DESC
    LIMIT ${limit}
  `;
}

function scoreAsset(row, tokens) {
  const haystack = cleanText(
    [
      row.asset_code,
      row.title,
      row.asset_type,
      row.status,
      row.locality,
      row.area_name,
      row.district,
      row.address,
      row.owner_name,
      row.broker_name,
      row.people_summary,
      row.bottleneck_notes,
    ].join(" ")
  ).toLowerCase();
  let score = 0;
  for (const token of tokens) {
    if (haystack.includes(token)) score += token.length > 5 ? 3 : 1;
  }
  if (row.asset_code && tokens.includes(String(row.asset_code).toLowerCase())) score += 20;
  return score;
}

async function candidateAssets(sql, messageText, history = [], limit = 12) {
  const reference = assetReference(messageText);
  const rows = await sql`
    SELECT
      a.id,
      a.asset_code,
      a.title,
      a.asset_type,
      a.status,
      a.locality,
      a.area_name,
      a.district,
      a.address,
      a.land_area,
      a.asking_price,
      a.expected_price,
      a.workability_rating,
      a.bottleneck_rating,
      left(coalesce(a.bottleneck_notes, ''), 700) AS bottleneck_notes,
      o.name AS owner_name,
      b.name AS broker_name,
      COALESCE(string_agg(DISTINCT c.name || ' (' || ac.relationship_type || ')', ' | '), '') AS people_summary,
      a.updated_at
    FROM assets a
    LEFT JOIN owners o ON o.id = a.owner_id
    LEFT JOIN brokers b ON b.id = a.broker_id
    LEFT JOIN asset_contacts ac ON ac.asset_id = a.id
    LEFT JOIN contacts c ON c.id = ac.contact_id
    GROUP BY a.id, o.name, b.name
    ORDER BY a.updated_at DESC
    LIMIT 500
  `;
  const historyText = history.map((item) => `${item.body_text || ""} ${item.transcription_text || ""}`).join(" ");
  const tokens = tokensForSearch(`${messageText} ${historyText}`);
  const scored = rows
    .map((row) => {
      let score = scoreAsset(row, tokens);
      if (reference.code && row.asset_code === reference.code) score += 100;
      if (reference.id && row.id === reference.id) score += 100;
      return { ...row, match_score: score };
    })
    .filter((row) => row.match_score > 0 || reference.code || reference.id)
    .sort((left, right) => right.match_score - left.match_score || new Date(right.updated_at) - new Date(left.updated_at));
  return scored.slice(0, limit);
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

function mapsLink(textValue) {
  return cleanText(textValue).match(/https?:\/\/(?:maps\.app\.goo\.gl|goo\.gl\/maps|www\.google\.[^\s]+\/maps|google\.[^\s]+\/maps)[^\s]*/i)?.[0] || "";
}

function hasPropertyDetailWords(textValue) {
  return /\b(plot|land|jv|joint venture|brokerage|owner|broker|asking|price|rate|bigha|acre|sqyd|sq yd|yard|gaj|approval|g\+|commercial|resale|rental)\b/i.test(
    textValue
  );
}

function isStandaloneCollateral(message, textValue) {
  const text = cleanText(textValue);
  const onlyMap = Boolean(mapsLink(text)) && !hasPropertyDetailWords(text);
  return Boolean((message.mediaId && !hasPropertyDetailWords(text)) || (message.latitude !== null && !hasPropertyDetailWords(text)) || onlyMap);
}

function requestedAction(textValue) {
  const lower = cleanText(textValue).toLowerCase();
  if (!lower) return "";
  if (/^(new|create|start|start draft|new property|new deal|add property|add deal)\b/.test(lower)) return "create";
  if (/^(info|search|find|show|which|tell|what|where|who|list)\b/.test(lower)) return "info";
  if (/^(edit|update|add update|note|spoke|conversation)\b/.test(lower)) return "update";
  if (/^(attach|add document|add docs|add photo|add map|link document)\b/.test(lower)) return "attach";
  return "";
}

function confirmationCommand(textValue) {
  const lower = cleanText(textValue).toLowerCase();
  if (!lower) return "";
  if (/^(confirm|confirmed|approve draft|final|finalise|finalize|done|yes confirm|ok confirm)$/i.test(lower)) return "confirm";
  if (/^(cancel|discard|delete draft|stop|ignore)$/i.test(lower)) return "cancel";
  return "";
}

function wantsDraftSummary(textValue) {
  const lower = cleanText(textValue).toLowerCase();
  return (
    lower.includes("draft") ||
    lower.includes("summary") ||
    lower.includes("missing") ||
    lower.includes("what else") ||
    lower.includes("what is left") ||
    lower.includes("kya missing")
  );
}

function hasValue(value) {
  return value !== null && value !== undefined && cleanText(value) !== "";
}

function actionHelpText() {
  return [
    "Tell me the mode first when the message is ambiguous:",
    "NEW or CREATE - start/continue a private property draft",
    "CONFIRM - move the draft to Approval Inbox",
    "CANCEL - discard the draft",
    "INFO <question> - search the confirmed database",
    "UPDATE <property detail> - add an update to an existing asset",
    "ATTACH LJV-00029 - attach the current document/map to a known asset",
  ].join("\n");
}

async function openAiJson(env, system, user) {
  if (!env.OPENAI_API_KEY) return null;
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
        { role: "system", content: system },
        { role: "user", content: user },
      ],
    }),
  });
  if (!response.ok) return null;
  const data = await response.json();
  try {
    return JSON.parse(data.choices?.[0]?.message?.content || "{}");
  } catch (_) {
    return null;
  }
}

async function answerQuestion(env, textValue, candidates, history) {
  if (!candidates.length) return "I could not find a matching property yet. Try a locality, owner, broker, project name, or send more details.";
  const ai = await openAiJson(
    env,
    "You answer questions for an internal Indian real-estate tracker. Answer only from candidate asset rows and conversation history. Be concise, include asset codes/ids, and say when uncertain. Return JSON with keys answer and cited_asset_ids.",
    JSON.stringify({
      question: textValue,
      recent_whatsapp_context: history,
      candidate_assets: candidates,
    })
  );
  if (ai?.answer) return cleanText(ai.answer).slice(0, 3900);
  return candidates
    .slice(0, 5)
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
    google_maps_link: cleanText(extracted.google_maps_link || mapsLink(textValue) || ""),
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

function collateralDraftPayload(message, from, textValue) {
  const link = mapsLink(textValue);
  const payload = {
    title: message.locationName || (link || message.latitude !== null ? "WhatsApp map/location draft" : "WhatsApp document draft"),
    asset_type: "other",
    status: "lead",
    source: "whatsapp",
    locality: message.locationName || "",
    address: message.locationAddress || "",
    latitude: message.latitude,
    longitude: message.longitude,
    google_maps_link: link || (message.latitude !== null && message.longitude !== null ? `https://www.google.com/maps?q=${message.latitude},${message.longitude}` : ""),
    bottleneck_notes: cleanText(textValue || message.mediaCaption || "Collateral received before property details."),
    raw_source: {
      whatsapp: {
        from,
        original_message: textValue,
        extracted_by: "collateral_seed",
        captured_at: new Date().toISOString(),
      },
    },
  };
  return Object.fromEntries(Object.entries(payload).filter(([, value]) => value !== "" && value !== null && value !== undefined));
}

function blankDraftPayload(from, textValue = "") {
  return {
    title: cleanText(textValue.replace(/^(new|create|start|start draft|new property|new deal|add property|add deal)\b/i, "")) || "WhatsApp property draft",
    asset_type: "other",
    status: "lead",
    source: "whatsapp",
    bottleneck_notes: "",
    raw_source: {
      whatsapp: {
        from,
        original_message: textValue,
        extracted_by: "blank_draft",
        captured_at: new Date().toISOString(),
      },
    },
  };
}

async function openAiExtractLead(env, textValue, from) {
  const parsed =
    (await openAiJson(
      env,
      "Extract one Indian real-estate property/deal lead from WhatsApp text. Return JSON only. Valid asset_type: land, jv, brokerage_listing, commercial, resale_unit, rental, other. Do not invent missing facts.",
      `Text: ${textValue}\n\nFields: title, asset_type, status, locality, area_name, tehsil, district, state, address, latitude, longitude, google_maps_link, land_area, built_up_area, asking_price, expected_price, owner_name, broker_name, workability_rating, bottleneck_rating, bottleneck_notes, legal_status, zoning_status, key_people.`
    )) || {};
  parsed.__source = "openai";
  return fallbackLeadPayload(textValue, from, parsed);
}

async function routeWithOpenAi(env, messageText, extracted, candidates, history) {
  const fallbackIntent = isQuestion(messageText)
    ? "query"
    : candidates.length && extracted.mediaId
      ? "attach_document"
      : candidates.length
        ? "update_asset"
        : isNewLead(messageText, Boolean(extracted.mediaId))
          ? "new_lead"
          : "needs_clarification";
  const ai = await openAiJson(
    env,
    [
      "You are a WhatsApp copilot for an internal land/JV/brokerage database.",
      "Classify the message and choose safe database actions.",
      "The user may be vague and may not know asset IDs. Use candidate assets and recent conversation context.",
      "Return JSON only with keys: intent, selected_asset_id, confidence, answer, lead_fields, update_text, contact, document_notes, clarification_question.",
      "Valid intent: query, new_lead, update_asset, attach_document, help, needs_clarification.",
      "Never invent facts. If selected_asset_id is uncertain, set confidence below 0.72 and ask a clarification.",
      "New leads must be approval-first; do not directly insert into assets.",
    ].join(" "),
    JSON.stringify({
      message: messageText,
      message_type: extracted.type,
      media: {
        media_id: extracted.mediaId,
        filename: extracted.mediaFilename,
        caption: extracted.mediaCaption,
        mime_type: extracted.mediaMimeType,
      },
      recent_whatsapp_context: history,
      candidate_assets: candidates,
    })
  );
  if (!ai) return { intent: fallbackIntent, selected_asset_id: candidates[0]?.id || null, confidence: candidates[0] ? 0.6 : 0 };
  return {
    intent: ai.intent || fallbackIntent,
    selected_asset_id: Number(ai.selected_asset_id || 0) || null,
    confidence: Number(ai.confidence || 0),
    answer: cleanText(ai.answer || ""),
    lead_fields: ai.lead_fields || {},
    update_text: cleanText(ai.update_text || messageText),
    contact: ai.contact || null,
    document_notes: cleanText(ai.document_notes || ""),
    clarification_question: cleanText(ai.clarification_question || ""),
  };
}

function selectedAssetFromDecision(decision, candidates) {
  if (!decision.selected_asset_id) return null;
  return candidates.find((row) => Number(row.id) === Number(decision.selected_asset_id)) || null;
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

function documentFromMessage(message) {
  if (!message.mediaId) return null;
  const isUrl = String(message.mediaId).startsWith("http://") || String(message.mediaId).startsWith("https://");
  return {
    document_name: message.mediaFilename || message.mediaCaption || `WhatsApp ${message.type}`,
    document_type: message.type || "whatsapp_media",
    mime_type: message.mediaMimeType,
    url: isUrl ? message.mediaId : undefined,
    storage_path: isUrl ? `twilio:${message.mediaId}` : `whatsapp:${message.mediaId}`,
    media_id: message.mediaId,
    media_sha256: message.mediaSha256,
    caption: message.mediaCaption,
    notes: `Captured from WhatsApp. ${isUrl ? "Twilio media URL may require Twilio authorization if opened later." : "Media id should be copied to durable storage later."}`,
    captured_at: new Date().toISOString(),
  };
}

function applyMessageContext(payload, message, from, messageText) {
  const next = { ...payload };
  const link = mapsLink(messageText || message.bodyText || message.mediaCaption || "");
  if (link) next.google_maps_link = link;
  if (message.latitude !== null && Number.isFinite(message.latitude)) next.latitude = message.latitude;
  if (message.longitude !== null && Number.isFinite(message.longitude)) next.longitude = message.longitude;
  if (next.latitude && next.longitude && !next.google_maps_link) {
    next.google_maps_link = `https://www.google.com/maps?q=${next.latitude},${next.longitude}`;
  }
  if (!next.address && message.locationAddress) next.address = message.locationAddress;
  if (!next.locality && message.locationName) next.locality = message.locationName;
  const doc = documentFromMessage(message);
  if (doc) next.documents = [...(Array.isArray(next.documents) ? next.documents : []), doc];
  next.raw_source = next.raw_source || {};
  next.raw_source.whatsapp = {
    ...(next.raw_source.whatsapp || {}),
    from,
    source_uid: `whatsapp:${message.wamid}`,
    message_type: message.type,
    media_id: message.mediaId,
    media_filename: message.mediaFilename,
    media_caption: message.mediaCaption,
    location: message.latitude && message.longitude ? { latitude: message.latitude, longitude: message.longitude } : undefined,
    original_messages: [
      ...((next.raw_source.whatsapp && Array.isArray(next.raw_source.whatsapp.original_messages)) ? next.raw_source.whatsapp.original_messages : []),
      {
        wamid: message.wamid,
        text: messageText || message.bodyText || message.mediaCaption || "",
        type: message.type,
        captured_at: new Date().toISOString(),
      },
    ].slice(-30),
  };
  return next;
}

function mergeDraftPayload(existingPayload, incomingPayload, message, from, messageText) {
  const existing = existingPayload && typeof existingPayload === "object" ? existingPayload : {};
  const incoming = incomingPayload && typeof incomingPayload === "object" ? incomingPayload : {};
  const next = { ...existing };
  for (const [key, value] of Object.entries(incoming)) {
    if (key === "raw_source" || key === "documents") continue;
    if (hasValue(value)) next[key] = value;
  }
  if (hasValue(existing.bottleneck_notes) && hasValue(incoming.bottleneck_notes) && existing.bottleneck_notes !== incoming.bottleneck_notes) {
    next.bottleneck_notes = `${existing.bottleneck_notes}\n${incoming.bottleneck_notes}`;
  }
  const mergedDocs = [
    ...(Array.isArray(existing.documents) ? existing.documents : []),
    ...(Array.isArray(incoming.documents) ? incoming.documents : []),
  ];
  if (mergedDocs.length) next.documents = mergedDocs;
  next.raw_source = {
    ...(existing.raw_source || {}),
    ...(incoming.raw_source || {}),
    whatsapp: {
      ...((existing.raw_source || {}).whatsapp || {}),
      ...((incoming.raw_source || {}).whatsapp || {}),
      original_messages: [
        ...(((existing.raw_source || {}).whatsapp || {}).original_messages || []),
        ...(((incoming.raw_source || {}).whatsapp || {}).original_messages || []),
      ].slice(-30),
    },
  };
  return applyMessageContext(next, message, from, messageText);
}

async function activeDraft(sql, from) {
  const rows = await sql`
    SELECT id, title, payload
    FROM approval_queue
    WHERE source = 'whatsapp'
      AND source_uid = ${`whatsapp:draft:${from}`}
      AND status = 'draft'
    ORDER BY updated_at DESC
    LIMIT 1
  `;
  return rows[0] || null;
}

async function upsertDraft(sql, message, from, incomingPayload, messageText) {
  const draft = await activeDraft(sql, from);
  const payload = mergeDraftPayload(draft?.payload, incomingPayload, message, from, messageText);
  if (!payload.title) payload.title = message.locationName || messageText?.slice(0, 120) || "WhatsApp property draft";
  payload.source = "whatsapp";
  payload.status = payload.status || "lead";
  const fingerprint = await sha256Prefix(
    [payload.title, payload.locality, payload.area_name, payload.district, payload.address, payload.land_area].filter(Boolean).join("|") ||
      JSON.stringify(payload)
  );
  payload.dedupe_fingerprint = payload.dedupe_fingerprint || fingerprint;
  payload.raw_source = payload.raw_source || {};
  payload.raw_source.whatsapp = {
    ...(payload.raw_source.whatsapp || {}),
    draft_source_uid: `whatsapp:draft:${from}`,
    draft_updated_at: new Date().toISOString(),
  };
  const rows = await sql`
    INSERT INTO approval_queue (source, source_uid, title, payload, status, created_by_source)
    VALUES ('whatsapp', ${`whatsapp:draft:${from}`}, ${payload.title || "WhatsApp property draft"}, ${JSON.stringify(payload)}::jsonb, 'draft', ${`WhatsApp ${from}`})
    ON CONFLICT (source, source_uid) DO UPDATE
      SET payload = excluded.payload,
          title = excluded.title,
          status = 'draft',
          updated_at = now()
    RETURNING id, payload
  `;
  return rows[0];
}

async function confirmDraft(sql, from) {
  const draft = await activeDraft(sql, from);
  if (!draft) return null;
  const payload = {
    ...draft.payload,
    raw_source: {
      ...(draft.payload?.raw_source || {}),
      whatsapp: {
        ...((draft.payload?.raw_source || {}).whatsapp || {}),
        confirmed_at: new Date().toISOString(),
      },
    },
  };
  const rows = await sql`
    UPDATE approval_queue
    SET status = 'pending',
        source_uid = ${`whatsapp:${from}:${Date.now()}`},
        payload = ${JSON.stringify(payload)}::jsonb,
        title = ${payload.title || draft.title || "WhatsApp property lead"},
        updated_at = now()
    WHERE id = ${draft.id}
    RETURNING id, title, payload
  `;
  return rows[0] || null;
}

async function cancelDraft(sql, from) {
  const draft = await activeDraft(sql, from);
  if (!draft) return null;
  const rows = await sql`
    UPDATE approval_queue
    SET status = 'cancelled',
        approval_decision = 'cancelled_by_whatsapp_user',
        reviewed_at = now(),
        updated_at = now()
    WHERE id = ${draft.id}
    RETURNING id, title
  `;
  return rows[0] || null;
}

function missingDraftFields(payload) {
  const required = [
    ["title", "title/name"],
    ["asset_type", "type"],
    ["locality", "locality"],
    ["district", "district"],
    ["land_area", "area"],
    ["asking_price", "asking price"],
    ["owner_name", "owner"],
    ["broker_name", "broker"],
    ["latitude", "map location"],
  ];
  return required.filter(([key]) => !hasValue(payload?.[key])).map(([, label]) => label);
}

function draftSummary(payload, draftId) {
  const docs = Array.isArray(payload?.documents) ? payload.documents.length : 0;
  const missing = missingDraftFields(payload).slice(0, 8);
  const lines = [
    `Draft #${draftId}: ${payload?.title || "Untitled property"}`,
    `Type: ${payload?.asset_type || "unknown"} | Locality: ${payload?.locality || "-"} | District: ${payload?.district || "-"}`,
    `Area: ${payload?.land_area || "-"} | Ask: ${payload?.asking_price ? Number(payload.asking_price).toLocaleString("en-IN") : "-"}`,
    `Owner: ${payload?.owner_name || "-"} | Broker: ${payload?.broker_name || "-"} | Docs: ${docs}`,
  ];
  if (payload?.google_maps_link) lines.push(`Map: ${payload.google_maps_link}`);
  if (missing.length) lines.push(`Missing: ${missing.join(", ")}`);
  lines.push("Reply CONFIRM to move this to Approval Inbox, CANCEL to discard, or send more details/docs/location.");
  return lines.join("\n").slice(0, 3900);
}

async function addAssetUpdate(sql, asset, messageText, from, updateType = "whatsapp_note") {
  await sql`
    INSERT INTO asset_updates (asset_id, update_type, update_text, created_by)
    VALUES (${asset.id}, ${updateType}, ${messageText}, ${`whatsapp:${from}`})
  `;
}

async function addAssetDocument(sql, asset, message, from) {
  const link = mapsLink(message.bodyText || message.mediaCaption || "");
  const doc = documentFromMessage(message);
  await sql`
    INSERT INTO asset_documents (asset_id, document_name, document_type, url, storage_path, notes)
    VALUES (
      ${asset.id},
      ${doc?.document_name || (link ? "WhatsApp Google Maps link" : `WhatsApp reference ${message.wamid}`)},
      ${doc?.document_type || (link ? "map_link" : message.type || "whatsapp_reference")},
      ${doc?.url || link || null},
      ${doc?.storage_path || (message.mediaId ? `whatsapp:${message.mediaId}` : null)},
      ${doc?.notes || `Captured from WhatsApp sender ${from}.${link ? ` Map: ${link}` : ""}${message.mediaId ? ` Media ID: ${message.mediaId}` : ""}${message.mediaCaption ? `. Caption: ${message.mediaCaption}` : ""}`}
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

async function existingMessageReply(sql, wamid) {
  if (!wamid) return null;
  const rows = await sql`
    SELECT response_text
    FROM whatsapp_messages
    WHERE wamid = ${wamid}
    LIMIT 1
  `;
  return rows[0]?.response_text || null;
}

async function handleIncomingMessage(sql, env, phoneNumberId, message, options = {}) {
  const extracted = extractMessage(message);
  console.log("incoming_whatsapp_message", JSON.stringify({ provider: options.replyProvider || "meta", from: extracted.from, type: extracted.type, wamid: extracted.wamid }));
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
    return "Sender is not allowlisted.";
  }
  const duplicateReply = await existingMessageReply(sql, extracted.wamid);
  if (duplicateReply) {
    console.log("duplicate_whatsapp_message", JSON.stringify({ provider: options.replyProvider || "meta", from: extracted.from, wamid: extracted.wamid }));
    return duplicateReply;
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
    const history = await recentConversation(sql, extracted.from);
    const command = confirmationCommand(messageText);
    const action = requestedAction(messageText);
    const draft = await activeDraft(sql, extracted.from);
    const effectiveText =
      messageText ||
      extracted.mediaCaption ||
      extracted.locationName ||
      extracted.locationAddress ||
      (extracted.mediaId ? "WhatsApp media lead" : "");
    if (messageText.toLowerCase() === "help" || messageText.toLowerCase().startsWith("/help")) {
      intent = "help";
      status = "answered";
      responseText = [
        "Send property details in one message or many messages. I keep a private draft and only move it to Approval Inbox when you reply CONFIRM.",
        actionHelpText(),
      ].join("\n\n");
    } else if (draft && command === "confirm") {
      intent = "confirm_draft";
      status = "queued";
      const confirmed = await confirmDraft(sql, extracted.from);
      approvalId = confirmed?.id || null;
      responseText = confirmed
        ? `Confirmed. Moved to Approval Inbox #${confirmed.id}: ${confirmed.title || confirmed.payload?.title || "WhatsApp property lead"}.`
        : "I could not find an active draft to confirm.";
    } else if (draft && command === "cancel") {
      intent = "cancel_draft";
      status = "cancelled";
      const cancelled = await cancelDraft(sql, extracted.from);
      approvalId = cancelled?.id || null;
      responseText = cancelled ? `Cancelled draft #${cancelled.id}: ${cancelled.title || "WhatsApp property draft"}.` : "I could not find an active draft to cancel.";
    } else if (!draft && command) {
      intent = "draft_command";
      status = "answered";
      responseText = "There is no active WhatsApp draft right now. Send a property note, document, photo, voice note, or location to start one.";
    } else if (draft && isQuestion(messageText) && wantsDraftSummary(messageText)) {
      intent = "draft_summary";
      status = "answered";
      approvalId = draft.id;
      responseText = draftSummary(draft.payload, draft.id);
    } else if (draft && action === "create") {
      intent = "draft_already_active";
      status = "answered";
      approvalId = draft.id;
      responseText = [
        "You already have an active private draft. I will not mix two properties in one draft.",
        draftSummary(draft.payload, draft.id),
        "Reply CONFIRM to move it to Approval Inbox, CANCEL to discard it, or continue sending details for this same property.",
      ].join("\n\n");
    } else if (!draft && (action === "create" || isStandaloneCollateral(extracted, effectiveText))) {
      intent = action === "create" ? "start_draft" : "start_collateral_draft";
      status = "draft";
      const payload = hasPropertyDetailWords(effectiveText)
        ? await openAiExtractLead(env, effectiveText, extracted.from)
        : isStandaloneCollateral(extracted, effectiveText)
          ? collateralDraftPayload(extracted, extracted.from, effectiveText)
          : blankDraftPayload(extracted.from, effectiveText);
      const updated = await upsertDraft(sql, extracted, extracted.from, payload, effectiveText);
      approvalId = updated?.id || null;
      responseText = [
        action === "create"
          ? "Started a private property draft. It will not enter Approval Inbox until you reply CONFIRM."
          : "I saved this map/document as a private draft instead of attaching it to a guessed property.",
        draftSummary(updated?.payload || payload, approvalId),
      ].join("\n\n");
    } else {
      const candidates = await candidateAssets(sql, effectiveText, history);
      const decision = await routeWithOpenAi(env, effectiveText, extracted, candidates, history);
      const reference = assetReference(effectiveText);
      const explicitAssetReference = Boolean(reference.code || reference.id);
      const directAsset = explicitAssetReference ? await findAsset(sql, effectiveText) : null;
      const safeAsset = directAsset || selectedAssetFromDecision(decision, candidates) || (explicitAssetReference ? candidates[0] : decision.confidence >= 0.9 ? candidates[0] : null);
      if (draft && !action && !isQuestion(messageText) && (effectiveText || extracted.mediaId || extracted.latitude !== null)) {
        intent = "update_draft";
        status = "draft_updated";
        const payload =
          decision.lead_fields && Object.keys(decision.lead_fields).length
            ? fallbackLeadPayload(effectiveText || "WhatsApp draft update", extracted.from, { ...decision.lead_fields, __source: "openai_router" })
            : await openAiExtractLead(env, effectiveText || "WhatsApp draft update", extracted.from);
        const updated = await upsertDraft(sql, extracted, extracted.from, payload, effectiveText);
        approvalId = updated?.id || draft.id;
        responseText = draftSummary(updated?.payload || payload, approvalId);
      } else if (action === "info" || decision.intent === "query" || isQuestion(messageText)) {
        intent = "query";
        status = "answered";
        responseText = decision.answer || (await answerQuestion(env, messageText, candidates, history));
      } else if (action === "attach" && safeAsset && (extracted.mediaId || mapsLink(effectiveText)) && (explicitAssetReference || decision.confidence >= 0.9)) {
        intent = "attach_document";
        status = "attached_document";
        assetId = safeAsset.id;
        await addAssetDocument(sql, safeAsset, extracted, extracted.from);
        responseText = `Attached WhatsApp reference to ${safeAsset.asset_code || safeAsset.id}: ${safeAsset.title}`;
      } else if (action === "attach" && (extracted.mediaId || mapsLink(effectiveText))) {
        intent = "attach_needs_asset";
        status = "answered";
        const options = candidates.slice(0, 3).map((row) => `${row.asset_code || row.id}: ${row.title}`).join("\n");
        responseText = `Which confirmed asset should I attach this to? Reply with ATTACH LJV-xxxxx or ATTACH asset <id>.${options ? `\nPossible matches:\n${options}` : ""}`;
      } else if (action === "update" && safeAsset && messageText && (explicitAssetReference || decision.confidence >= 0.9)) {
        intent = "update_asset";
        status = "updated_asset";
        assetId = safeAsset.id;
        await addAssetUpdate(sql, safeAsset, decision.update_text || messageText, extracted.from, "whatsapp_conversation");
        responseText = `Updated ${safeAsset.asset_code || safeAsset.id}: ${safeAsset.title}`;
      } else if (action === "update") {
        intent = "update_needs_asset";
        status = "answered";
        const options = candidates.slice(0, 3).map((row) => `${row.asset_code || row.id}: ${row.title}`).join("\n");
        responseText = `Which confirmed asset should I update? Reply with UPDATE LJV-xxxxx: <note> or UPDATE asset <id>: <note>.${options ? `\nPossible matches:\n${options}` : ""}`;
      } else if (decision.intent === "new_lead" || isNewLead(effectiveText, Boolean(extracted.mediaId || extracted.latitude !== null))) {
        intent = "draft_new_lead";
        status = "draft";
        const payload =
          decision.lead_fields && Object.keys(decision.lead_fields).length
            ? fallbackLeadPayload(effectiveText || "WhatsApp property lead", extracted.from, { ...decision.lead_fields, __source: "openai_router" })
            : await openAiExtractLead(env, effectiveText || "WhatsApp property lead", extracted.from);
        const updated = await upsertDraft(sql, extracted, extracted.from, payload, effectiveText);
        approvalId = updated?.id || null;
        responseText = draftSummary(updated?.payload || payload, approvalId);
      } else {
        intent = "needs_clarification";
        status = "answered";
        const options = candidates.slice(0, 3).map((row) => `${row.asset_code || row.id}: ${row.title}`).join("\n");
        responseText =
          decision.clarification_question ||
          (options
            ? `I found possible matches but I am not confident. Which one should I use?\n${options}`
            : `I am not sure whether you want to create, edit, attach, or search.\n\n${actionHelpText()}`);
      }
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

  if (responseText && options.replyProvider !== "twilio") await sendWhatsAppText(env, extracted.from, responseText);
  console.log("whatsapp_message_processed", JSON.stringify({ provider: options.replyProvider || "meta", from: extracted.from, intent, status, assetId, approvalId, replyLength: responseText.length }));
  return responseText;
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

function twilioMessageFromParams(params) {
  const from = cleanText(params.get("From")).replace(/^whatsapp:/, "").replace(/^\+/, "");
  const body = cleanText(params.get("Body"));
  const mediaUrl = cleanText(params.get("MediaUrl0"));
  const mediaType = cleanText(params.get("MediaContentType0"));
  const latitudeRaw = cleanText(params.get("Latitude") || params.get("latitude"));
  const longitudeRaw = cleanText(params.get("Longitude") || params.get("longitude"));
  const latitude = latitudeRaw ? Number(latitudeRaw) : null;
  const longitude = longitudeRaw ? Number(longitudeRaw) : null;
  const hasLocation = latitude !== null && longitude !== null && Number.isFinite(latitude) && Number.isFinite(longitude);
  const messageSid = cleanText(params.get("MessageSid") || params.get("SmsMessageSid") || crypto.randomUUID());
  return {
    id: `twilio:${messageSid}`,
    from,
    type: hasLocation ? "location" : mediaUrl ? (mediaType.startsWith("audio/") ? "audio" : mediaType.includes("pdf") ? "document" : "image") : "text",
    text: { body },
    image: mediaUrl ? { id: mediaUrl, mime_type: mediaType, caption: body } : undefined,
    audio: mediaUrl ? { id: mediaUrl, mime_type: mediaType, caption: body } : undefined,
    document: mediaUrl ? { id: mediaUrl, mime_type: mediaType, filename: cleanText(params.get("MediaUrl0")).split("/").pop(), caption: body } : undefined,
    location: hasLocation
      ? {
          latitude,
          longitude,
          name: body || cleanText(params.get("Label") || params.get("LocationName")),
          address: cleanText(params.get("Address") || params.get("LocationAddress")),
        }
      : undefined,
    twilio: Object.fromEntries(params.entries()),
  };
}

function twimlResponse(body) {
  const escaped = String(body || "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
  return new Response(`<?xml version="1.0" encoding="UTF-8"?><Response><Message>${escaped}</Message></Response>`, {
    status: 200,
    headers: { "content-type": "text/xml; charset=utf-8" },
  });
}

async function handleTwilioPost(request, env) {
  const rawBody = await request.text();
  const params = new URLSearchParams(rawBody);
  const signatureOk = await verifyTwilioSignature(request, env, params);
  console.log("twilio_webhook_received", JSON.stringify({ signatureOk, from: params.get("From"), bodyLength: cleanText(params.get("Body")).length, mediaCount: params.get("NumMedia") || "0" }));
  if (!signatureOk) return twimlResponse("Webhook signature did not validate. Check TWILIO_WEBHOOK_URL exactly matches the Twilio webhook URL.");
  const sql = sqlClient(env);
  await ensureSchema(sql);
  const message = twilioMessageFromParams(params);
  const reply = await handleIncomingMessage(sql, env, cleanText(params.get("To")).replace(/^whatsapp:/, ""), message, { replyProvider: "twilio" });
  console.log("twilio_reply", JSON.stringify({ to: params.get("From"), reply: String(reply || "Received.").slice(0, 240) }));
  return twimlResponse(reply || "Received.");
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname === "/health") return json({ status: "ok", service: "land-jv-whatsapp-bot" });
    if (url.pathname === "/twilio/ping") return twimlResponse("Land JV WhatsApp bot is online.");
    if (url.pathname === "/twilio/webhook") {
      if (request.method === "POST") return handleTwilioPost(request, env);
      return text("Method not allowed", 405);
    }
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
