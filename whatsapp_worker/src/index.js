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
  const headers = isUrl || !env.WHATSAPP_ACCESS_TOKEN ? {} : { authorization: `Bearer ${env.WHATSAPP_ACCESS_TOKEN}` };
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

async function addAssetUpdate(sql, asset, messageText, from, updateType = "whatsapp_note") {
  await sql`
    INSERT INTO asset_updates (asset_id, update_type, update_text, created_by)
    VALUES (${asset.id}, ${updateType}, ${messageText}, ${`whatsapp:${from}`})
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

async function handleIncomingMessage(sql, env, phoneNumberId, message, options = {}) {
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
    return "Sender is not allowlisted.";
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
    const candidates = await candidateAssets(sql, messageText || extracted.mediaCaption || "", history);
    const decision = await routeWithOpenAi(env, messageText || extracted.mediaCaption || "", extracted, candidates, history);
    const asset = selectedAssetFromDecision(decision, candidates) || (decision.confidence >= 0.72 ? candidates[0] : null);
    if (messageText.toLowerCase() === "help" || messageText.toLowerCase().startsWith("/help")) {
      intent = "help";
      status = "answered";
      responseText =
        "Send a property note to queue a lead. Ask natural questions like 'which Jaipur brokerage deals are high workability?' For updates, mention any recognizable property detail; I will search and ask if unsure. Voice notes are transcribed when OPENAI_API_KEY is set.";
    } else if (decision.intent === "query" || isQuestion(messageText)) {
      intent = "query";
      status = "answered";
      responseText = decision.answer || (await answerQuestion(env, messageText, candidates, history));
    } else if (decision.intent === "attach_document" && asset && extracted.mediaId && decision.confidence >= 0.72) {
      intent = "attach_document";
      status = "attached_document";
      assetId = asset.id;
      await addAssetDocument(sql, asset, extracted, extracted.from);
      responseText = `Attached WhatsApp media to ${asset.asset_code || asset.id}: ${asset.title}`;
    } else if (decision.intent === "update_asset" && asset && messageText && decision.confidence >= 0.72) {
      intent = "update_asset";
      status = "updated_asset";
      assetId = asset.id;
      await addAssetUpdate(sql, asset, decision.update_text || messageText, extracted.from, "whatsapp_conversation");
      responseText = `Updated ${asset.asset_code || asset.id}: ${asset.title}`;
    } else if (decision.intent === "new_lead" || isNewLead(messageText, Boolean(extracted.mediaId))) {
      intent = "queue_new_lead";
      status = "queued";
      const payload = decision.lead_fields && Object.keys(decision.lead_fields).length
        ? fallbackLeadPayload(messageText || extracted.mediaCaption || "WhatsApp media lead", extracted.from, { ...decision.lead_fields, __source: "openai_router" })
        : await openAiExtractLead(env, messageText || extracted.mediaCaption || "WhatsApp media lead", extracted.from);
      approvalId = await queueApproval(sql, extracted, extracted.from, payload);
      responseText = `Queued for approval #${approvalId}: ${payload.title || "WhatsApp property lead"}. Open Approval Inbox to review before it enters assets.`;
    } else {
      intent = "needs_clarification";
      status = "answered";
      const options = candidates.slice(0, 3).map((row) => `${row.asset_code || row.id}: ${row.title}`).join("\n");
      responseText =
        decision.clarification_question ||
        (options
          ? `I found possible matches but I am not confident. Which one should I use?\n${options}`
          : "I could not tell if this is a question, property update, or new lead. Send 'help' for examples, or add location/name/type.");
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

function twilioMessageFromForm(form) {
  const from = cleanText(form.get("From")).replace(/^whatsapp:/, "").replace(/^\+/, "");
  const body = cleanText(form.get("Body"));
  const mediaUrl = cleanText(form.get("MediaUrl0"));
  const mediaType = cleanText(form.get("MediaContentType0"));
  const messageSid = cleanText(form.get("MessageSid") || form.get("SmsMessageSid") || crypto.randomUUID());
  return {
    id: `twilio:${messageSid}`,
    from,
    type: mediaUrl ? (mediaType.startsWith("audio/") ? "audio" : mediaType.includes("pdf") ? "document" : "image") : "text",
    text: { body },
    image: mediaUrl ? { id: mediaUrl, mime_type: mediaType, caption: body } : undefined,
    audio: mediaUrl ? { id: mediaUrl, mime_type: mediaType, caption: body } : undefined,
    document: mediaUrl ? { id: mediaUrl, mime_type: mediaType, filename: cleanText(form.get("MediaUrl0")).split("/").pop(), caption: body } : undefined,
    twilio: Object.fromEntries(form.entries()),
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
  const form = await request.formData();
  const sql = sqlClient(env);
  await ensureSchema(sql);
  const message = twilioMessageFromForm(form);
  const reply = await handleIncomingMessage(sql, env, cleanText(form.get("To")).replace(/^whatsapp:/, ""), message, { replyProvider: "twilio" });
  return twimlResponse(reply || "Received.");
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname === "/health") return json({ status: "ok", service: "land-jv-whatsapp-bot" });
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
