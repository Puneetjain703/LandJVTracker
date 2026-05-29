from __future__ import annotations

import json
import os
from html import escape
from typing import Any

import folium
import pandas as pd
import requests
import streamlit as st
from streamlit_folium import st_folium


API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000").rstrip("/")
ASSET_TYPES = ["", "land", "jv", "resale_unit", "commercial", "rental", "brokerage_listing", "other"]
UPDATE_TYPES = ["note", "price_revision", "status_change", "sold", "follow_up", "site_visit", "legal", "document", "other"]


st.set_page_config(page_title="Land and JV Tracker", layout="wide", initial_sidebar_state="expanded")


THEME_CSS = """
<style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&family=Outfit:wght@300;400;500;600;700;800;900&display=swap');
    
    :root {
        --ink: #f8f6f0;
        --muted: #a6a49c;
        --line: #3d3b34;
        --paper: #131417;
        --wash: #08090b;
        --gold: #dfb75c;
        --gold-soft: #f5dfa3;
        --silver: #cfd1d4;
        --silver-deep: #696e75;
        --charcoal: #121316;
        --coal: #050608;
        --danger: #c26550;
    }
    
    body, [data-testid="stAppViewContainer"], .stApp {
        font-family: 'Inter', sans-serif;
        background:
            radial-gradient(circle at 80% 10%, rgba(223,183,92,0.11), transparent 45%),
            radial-gradient(circle at 10% 90%, rgba(194,101,80,0.06), transparent 40%),
            linear-gradient(180deg, #090a0d 0%, #0f1014 260px, #07080a 100%);
        color: var(--ink);
    }
    
    .hero-title, .hero-kicker, .section-label, .asset-card-title, h1, h2, h3 {
        font-family: 'Outfit', sans-serif;
    }
    
    [data-testid="stSidebar"] {
        background: linear-gradient(180deg, #050609 0%, #0b0c10 100%);
        border-right: 1px solid rgba(223,183,92,0.15);
    }
    [data-testid="stSidebar"] * {
        color: #fcf8ee;
    }
    [data-testid="stSidebar"] .stRadio label {
        padding: 0.18rem 0;
        font-weight: 500;
    }
    [data-testid="stAppViewContainer"] label,
    [data-testid="stWidgetLabel"] p {
        color: var(--ink) !important;
        font-weight: 600;
        font-family: 'Outfit', sans-serif;
    }
    [data-testid="stSidebar"] [data-testid="stWidgetLabel"] p,
    [data-testid="stSidebar"] label {
        color: #fcf8ee !important;
    }
    [data-testid="stTextInput"] input,
    [data-testid="stNumberInput"] input,
    [data-testid="stTextArea"] textarea,
    [data-baseweb="select"] > div {
        background: #141518 !important;
        border-color: #3b3d43 !important;
        color: #f8f6f0 !important;
        border-radius: 10px !important;
        font-family: 'Inter', sans-serif;
    }
    [data-testid="stTextInput"] input:focus,
    [data-testid="stNumberInput"] input:focus,
    [data-testid="stTextArea"] textarea:focus {
        border-color: var(--gold) !important;
        box-shadow: 0 0 0 1px rgba(223,183,92,0.4) !important;
    }
    div[data-testid="stMetric"] {
        background: rgba(22,23,26,0.5);
        border: 1px solid rgba(223,183,92,0.15);
        backdrop-filter: blur(8px);
        -webkit-backdrop-filter: blur(8px);
        border-radius: 12px;
        padding: 16px 20px;
        box-shadow: 0 10px 30px rgba(0,0,0,0.35);
    }
    div[data-testid="stMetric"] label {
        color: var(--muted);
        font-family: 'Outfit', sans-serif;
        letter-spacing: 0.5px;
    }
    div[data-testid="stMetricValue"] {
        color: var(--gold-soft);
        font-family: 'Outfit', sans-serif;
        font-weight: 850;
    }
    .hero-panel {
        border: 1px solid rgba(223,183,92,0.25);
        border-radius: 14px;
        padding: 24px 28px;
        background:
            linear-gradient(135deg, rgba(16,17,20,0.95) 0%, rgba(12,13,15,0.95) 60%, rgba(45,37,18,0.93) 100%);
        box-shadow: 0 20px 50px rgba(0,0,0,0.45), inset 0 1px 0 rgba(255,255,255,0.04);
        margin-bottom: 1.2rem;
    }
    .hero-kicker {
        font-size: 0.8rem;
        font-weight: 800;
        letter-spacing: 1px;
        color: var(--gold-soft);
        text-transform: uppercase;
        margin-bottom: 0.4rem;
    }
    .hero-title {
        font-size: 2.25rem;
        line-height: 1.1;
        font-weight: 850;
        color: #fff9eb;
        margin-bottom: 0.5rem;
    }
    .hero-copy {
        color: #d4cfc3;
        font-size: 1.04rem;
        max-width: 900px;
        line-height: 1.5;
    }
    .chip-row {
        display: flex;
        flex-wrap: wrap;
        gap: 6px;
        margin: 8px 0 2px;
    }
    .chip {
        display: inline-flex;
        align-items: center;
        min-height: 26px;
        padding: 3px 9px;
        border-radius: 999px;
        background: rgba(223,183,92,0.1);
        border: 1px solid rgba(223,183,92,0.3);
        color: #f7dfa6;
        font-size: 0.78rem;
        font-weight: 700;
        white-space: nowrap;
    }
    .chip.alt {
        background: rgba(207,209,212,0.1);
        border-color: rgba(207,209,212,0.3);
        color: #f3f4f6;
    }
    .chip.warn {
        background: rgba(194,101,80,0.12);
        border-color: rgba(194,101,80,0.4);
        color: #f4beaf;
    }
    .section-label {
        color: var(--muted);
        font-size: 0.8rem;
        font-weight: 800;
        text-transform: uppercase;
        margin: 1.2rem 0 0.4rem;
        letter-spacing: 1px;
    }
    .asset-card {
        background: rgba(20,21,24,0.45);
        border: 1px solid rgba(223,183,92,0.15);
        backdrop-filter: blur(12px);
        -webkit-backdrop-filter: blur(12px);
        border-radius: 12px;
        padding: 18px 20px;
        min-height: 180px;
        box-shadow: 0 8px 32px 0 rgba(0,0,0,0.35);
        transition: all 0.35s cubic-bezier(0.25, 0.8, 0.25, 1);
    }
    .asset-card:hover {
        transform: translateY(-5px);
        border-color: rgba(223,183,92,0.5);
        box-shadow: 0 12px 40px rgba(223,183,92,0.15);
        background: rgba(26,27,31,0.6);
    }
    .asset-card-title {
        font-size: 1.05rem;
        font-weight: 800;
        color: var(--ink);
        margin-bottom: 8px;
        overflow-wrap: anywhere;
        letter-spacing: -0.2px;
    }
    .asset-card-meta {
        color: var(--muted);
        font-size: 0.88rem;
        line-height: 1.5;
        min-height: 44px;
    }
    .asset-card-footer {
        display: flex;
        justify-content: space-between;
        gap: 10px;
        margin-top: 14px;
        border-top: 1px solid rgba(207,209,212,0.12);
        padding-top: 10px;
        color: var(--muted);
        font-size: 0.84rem;
    }
    .score-track {
        height: 6px;
        background: #232427;
        border-radius: 999px;
        overflow: hidden;
        margin-top: 6px;
    }
    .score-fill {
        height: 6px;
        background: linear-gradient(90deg, #6c7075, var(--silver), var(--gold));
    }
    .field-grid {
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 10px;
        margin-top: 8px;
    }
    .field {
        border: 1px solid rgba(207,209,212,0.15);
        border-radius: 10px;
        padding: 12px 14px;
        background: rgba(20,21,24,0.7);
    }
    .field-label {
        color: var(--muted);
        font-size: 0.74rem;
        font-weight: 750;
        text-transform: uppercase;
        letter-spacing: 0.5px;
    }
    .field-value {
        color: var(--ink);
        font-weight: 700;
        margin-top: 4px;
        overflow-wrap: anywhere;
    }
    .note-panel {
        background: rgba(22,23,26,0.65);
        border: 1px solid rgba(207,209,212,0.18);
        border-left: 5px solid var(--gold);
        border-radius: 10px;
        padding: 16px 18px;
        color: var(--ink);
        line-height: 1.5;
    }
    .empty-panel {
        border: 1px dashed rgba(207,209,212,0.3);
        border-radius: 10px;
        background: rgba(18,19,22,0.6);
        padding: 24px;
        color: var(--muted);
        text-align: center;
    }
    button[kind="primary"],
    button[kind="primaryFormSubmit"],
    button[data-testid="stBaseButton-primary"],
    button[data-testid="stBaseButton-primaryFormSubmit"],
    .stDownloadButton button {
        border-radius: 10px !important;
        font-weight: 800 !important;
        background: linear-gradient(135deg, #a67b1e, #ebd076) !important;
        color: #0c0c0d !important;
        border: 1px solid #ebce73 !important;
        box-shadow: 0 4px 15px rgba(166,123,30,0.25) !important;
        transition: all 0.25s ease !important;
    }
    button[kind="primary"]:hover,
    button[kind="primaryFormSubmit"]:hover,
    button[data-testid="stBaseButton-primary"]:hover,
    button[data-testid="stBaseButton-primaryFormSubmit"]:hover,
    .stDownloadButton button:hover {
        background: linear-gradient(135deg, #c2932c, #f7df8f) !important;
        box-shadow: 0 6px 20px rgba(166,123,30,0.4) !important;
    }
    button:not([kind="primary"]):not([kind="primaryFormSubmit"]):not([data-testid="stBaseButton-primary"]):not([data-testid="stBaseButton-primaryFormSubmit"]) {
        border-radius: 10px !important;
        border-color: rgba(207,209,212,0.25) !important;
        color: #f8f6f0 !important;
        background: #141518 !important;
        transition: all 0.25s ease !important;
    }
    button:not([kind="primary"]):not([kind="primaryFormSubmit"]):not([data-testid="stBaseButton-primary"]):not([data-testid="stBaseButton-primaryFormSubmit"]):hover {
        border-color: var(--gold) !important;
        background: #1c1d22 !important;
    }
    .stTabs [data-baseweb="tab-list"] {
        gap: 6px;
        border-bottom: 1px solid rgba(207,209,212,0.15);
    }
    .stTabs [data-baseweb="tab"] {
        border-radius: 10px 10px 0 0;
        padding: 10px 16px;
        color: #cfd1d4;
        background: #111215;
        border: 1px solid rgba(207,209,212,0.12);
        font-family: 'Outfit', sans-serif;
    }
    .stTabs [aria-selected="true"] {
        color: var(--gold-soft) !important;
        background: rgba(20,21,24,0.7) !important;
        border-color: rgba(223,183,92,0.3) rgba(223,183,92,0.3) transparent rgba(223,183,92,0.3) !important;
    }
    .stDataFrame, [data-testid="stDataFrame"] {
        border: 1px solid rgba(207,209,212,0.15);
        border-radius: 10px;
        overflow: hidden;
    }
    @media (max-width: 900px) {
        .hero-title { font-size: 1.7rem; }
        .field-grid { grid-template-columns: 1fr; }
    }
</style>
""""""


def apply_theme() -> None:
    st.markdown(THEME_CSS, unsafe_allow_html=True)


def labelize(value: Any) -> str:
    if value in (None, ""):
        return "-"
    return str(value).replace("_", " ").strip().title()


def money(value: Any) -> str:
    amount = to_float(value, default=0)
    if not amount:
        return "-"
    if amount >= 10_000_000:
        return f"{amount / 10_000_000:.2f} Cr"
    if amount >= 100_000:
        return f"{amount / 100_000:.2f} Lac"
    return f"{amount:,.0f}"


def clip_text(value: Any, length: int = 90) -> str:
    text = str(value or "").strip()
    if len(text) <= length:
        return text
    return text[: length - 1].rstrip() + "..."


def page_hero(kicker: str, title: str, copy: str, chips: list[str] | None = None) -> None:
    chip_html = ""
    for index, chip in enumerate(chips or []):
        css_class = "chip alt" if index % 3 == 1 else "chip warn" if index % 3 == 2 else "chip"
        chip_html += f'<span class="{css_class}">{escape(chip)}</span>'
    st.markdown(
        f"""
        <div class="hero-panel">
            <div class="hero-kicker">{escape(kicker)}</div>
            <div class="hero-title">{escape(title)}</div>
            <div class="hero-copy">{escape(copy)}</div>
            <div class="chip-row">{chip_html}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def score_html(label: str, value: Any) -> str:
    score = max(0, min(10, to_int(value)))
    return (
        f"<div><strong>{label}: {score}/10</strong>"
        f'<div class="score-track"><div class="score-fill" style="width:{score * 10}%"></div></div></div>'
    )


def field_grid(fields: dict[str, Any]) -> None:
    html = '<div class="field-grid">'
    for label, value in fields.items():
        shown = labelize(value) if label.lower() not in {"asking", "expected"} else money(value)
        html += (
            '<div class="field">'
            f'<div class="field-label">{escape(label)}</div>'
            f'<div class="field-value">{escape(shown)}</div>'
            "</div>"
        )
    html += "</div>"
    st.markdown(html, unsafe_allow_html=True)


def empty_panel(message: str) -> None:
    st.markdown(f'<div class="empty-panel">{escape(message)}</div>', unsafe_allow_html=True)


def _headers() -> dict[str, str]:
    token = st.session_state.get("token")
    return {"Authorization": f"Bearer {token}"} if token else {}


def api(method: str, path: str, **kwargs: Any) -> Any:
    response = requests.request(method, f"{API_BASE_URL}{path}", headers=_headers(), timeout=60, **kwargs)
    if response.status_code == 401:
        st.session_state.pop("token", None)
        st.error("Session expired. Please log in again.")
        st.stop()
    if response.status_code >= 400:
        try:
            detail = response.json().get("detail", response.text)
        except Exception:
            detail = response.text
        raise RuntimeError(detail)
    if not response.content:
        return None
    return response.json()


def api_bytes(method: str, path: str, **kwargs: Any) -> bytes:
    response = requests.request(method, f"{API_BASE_URL}{path}", headers=_headers(), timeout=120, **kwargs)
    if response.status_code == 401:
        st.session_state.pop("token", None)
        st.error("Session expired. Please log in again.")
        st.stop()
    if response.status_code >= 400:
        raise RuntimeError(response.text)
    return response.content


def login_page() -> None:
    apply_theme()
    left, right = st.columns([1.1, 0.9], gap="large")
    with left:
        page_hero(
            "Internal intelligence desk",
            "Land and JV Tracker",
            "A calmer place to approve leads, track deal movement, inspect maps, and keep the property memory outside Excel.",
            ["Brokerage", "Land parcels", "Approvals first", "Map-ready"],
        )
        st.markdown(
            """
            <div class="note-panel">
                This MVP keeps new sources in a review queue before they enter the confirmed database.
                Use it as the daily desk for leads, bottlenecks, price changes, documents, and follow-ups.
            </div>
            """,
            unsafe_allow_html=True,
        )
    with right:
        st.markdown('<div class="section-label">Secure workspace</div>', unsafe_allow_html=True)
        with st.form("login"):
            username = st.text_input("Username")
            password = st.text_input("Password", type="password")
            submitted = st.form_submit_button("Enter tracker", use_container_width=True, type="primary")
    if submitted:
        try:
            data = requests.post(
                f"{API_BASE_URL}/login",
                json={"username": username, "password": password},
                timeout=30,
            ).json()
            if "access_token" not in data:
                raise RuntimeError(data.get("detail", "Login failed"))
            st.session_state["token"] = data["access_token"]
            st.session_state["username"] = data["username"]
            st.rerun()
        except Exception as exc:
            st.error(f"Login failed: {exc}")


def stats_bar() -> None:
    try:
        stats = api("GET", "/stats")
    except Exception:
        stats = {"total_assets": 0, "active_deals": 0, "pending_approvals": 0, "new_leads_this_week": 0}
    cols = st.columns(4)
    cols[0].metric("Confirmed inventory", stats["total_assets"])
    cols[1].metric("Active deals", stats["active_deals"])
    cols[2].metric("Approval queue", stats["pending_approvals"])
    cols[3].metric("Fresh this week", stats["new_leads_this_week"])


def compact_df(rows: list[dict[str, Any]]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    columns = [
        "id",
        "asset_code",
        "title",
        "asset_type",
        "status",
        "district",
        "tehsil",
        "locality",
        "source",
        "asking_price",
        "workability_rating",
        "approval_status",
        "owner_name",
        "broker_name",
    ]
    return df[[col for col in columns if col in df.columns]]


def type_index(value: str | None) -> int:
    asset_types = ASSET_TYPES[1:]
    return asset_types.index(value) if value in asset_types else 0


def update_type_index(value: str | None) -> int:
    return UPDATE_TYPES.index(value) if value in UPDATE_TYPES else 0


def to_float(value: Any, default: float = 0.0) -> float:
    if value in (None, ""):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def to_int(value: Any, default: int = 0) -> int:
    if value in (None, ""):
        return default
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def clean_payload(payload: dict[str, Any], keep_zero_fields: set[str] | None = None) -> dict[str, Any]:
    keep_zero_fields = keep_zero_fields or set()
    cleaned: dict[str, Any] = {}
    for key, value in payload.items():
        if value in ("", None):
            continue
        if value in (0, 0.0) and key not in keep_zero_fields:
            continue
        cleaned[key] = value
    return cleaned


def document_lines(documents: list[dict[str, Any]] | None, external_links: Any = None, notion_url: str | None = None) -> str:
    lines: list[str] = []
    for document in documents or []:
        lines.append(
            " | ".join(
                str(part)
                for part in [
                    document.get("document_name") or document.get("name") or "Imported collateral",
                    document.get("url") or document.get("storage_path") or "",
                    document.get("document_type") or "",
                    document.get("notes") or "",
                ]
                if part
            )
        )
    links: list[str] = []
    if isinstance(external_links, list):
        links = [str(link) for link in external_links if link]
    elif external_links:
        links = [part.strip() for part in str(external_links).replace(",", "\n").splitlines() if part.strip()]
    for link in links:
        if link not in "\n".join(lines):
            lines.append(f"External link | {link}")
    if notion_url and notion_url not in "\n".join(lines):
        lines.append(f"Notion source | {notion_url} | notion")
    return "\n".join(lines)


def parse_document_lines(text: str) -> list[dict[str, Any]]:
    documents: list[dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = [part.strip() for part in line.split("|")]
        document_name = parts[0] if parts else "Imported collateral"
        url = parts[1] if len(parts) > 1 else ""
        document_type = parts[2] if len(parts) > 2 else ""
        notes = parts[3] if len(parts) > 3 else ""
        if document_name.startswith(("http://", "https://")) and not url:
            url = document_name
            document_name = "Imported collateral"
        documents.append(
            clean_payload(
                {
                    "document_name": document_name or url or "Imported collateral",
                    "url": url,
                    "document_type": document_type,
                    "notes": notes,
                }
            )
        )
    return documents


def asset_filters() -> dict[str, Any]:
    st.markdown('<div class="section-label">Find the right file fast</div>', unsafe_allow_html=True)
    with st.container(border=True):
        c1, c2, c3, c4, c5 = st.columns([1.2, 1, 1, 1, 1])
        filters = {
            "asset_type": c1.selectbox("Type", ASSET_TYPES, label_visibility="collapsed"),
            "district": c2.text_input("District", placeholder="District"),
            "tehsil": c3.text_input("Tehsil", placeholder="Tehsil"),
            "locality": c4.text_input("Locality", placeholder="Locality"),
            "status": c5.text_input("Status", placeholder="Status"),
        }
        c6, c7, c8 = st.columns([1, 1, 2])
        filters.update(
            {
                "source": c6.text_input("Source", placeholder="Source"),
                "workability_rating": c7.number_input("Minimum workability", min_value=0, max_value=10, value=0),
                "approval_status": c8.segmented_control(
                    "Approval",
                    ["", "approved", "pending", "rejected"],
                    default="",
                    format_func=lambda value: "Any approval" if value == "" else labelize(value),
                ),
            }
        )
    return {key: value for key, value in filters.items() if value not in ("", 0, None)}


def filter_rows_locally(rows: list[dict[str, Any]], query: str) -> list[dict[str, Any]]:
    query = query.strip().lower()
    if not query:
        return rows
    searchable = ["asset_code", "title", "district", "tehsil", "locality", "area_name", "owner_name", "broker_name", "source"]
    return [
        row
        for row in rows
        if any(query in str(row.get(field) or "").lower() for field in searchable)
    ]


def asset_card(row: dict[str, Any]) -> None:
    chips = [
        labelize(row.get("asset_type")),
        labelize(row.get("status")),
        row.get("asset_code") or f"#{row.get('id')}",
    ]
    chip_html = "".join(f'<span class="chip">{escape(str(chip))}</span>' for chip in chips if chip and chip != "-")
    location = ", ".join(part for part in [row.get("locality"), row.get("tehsil"), row.get("district")] if part) or "Location not set"
    
    loc_score = row.get("location_score")
    score_display = ""
    if loc_score is not None:
        try:
            score_val = float(loc_score)
            score_display = f"""
            <div style="margin-top: 8px; font-size: 0.8rem; display: flex; align-items: center; justify-content: space-between;">
                <span style="color: var(--gold-soft); font-weight: 700;">Location Score</span>
                <span style="font-weight: 800; color: #fff;">{score_val:.1f}/10</span>
            </div>
            <div class="score-track" style="height: 5px; margin-top: 3px; background: #232427; border-radius: 99px; overflow: hidden;">
                <div class="score-fill" style="width: {score_val * 10}%; height: 5px; background: linear-gradient(90deg, #dfb75c, #ebd076); border-radius: 99px;"></div>
            </div>
            """
        except Exception:
            pass

    html = f"""
        <div class="asset-card">
            <div class="chip-row">{chip_html}</div>
            <div class="asset-card-title">{escape(clip_text(row.get("title"), 86))}</div>
            <div class="asset-card-meta">
                {escape(location)}<br>
                Owner: {escape(str(row.get("owner_name") or "-"))}<br>
                Broker: {escape(str(row.get("broker_name") or "-"))}
            </div>
            {score_html("Workability", row.get("workability_rating"))}
            {score_display}
            <div class="asset-card-footer">
                <span>Ask {escape(money(row.get("asking_price")))}</span>
                <span>{escape(labelize(row.get("source")))}</span>
            </div>
        </div>
    """
    st.markdown(html, unsafe_allow_html=True)


def asset_list_page(title: str = "Asset Desk", preset_filters: dict[str, Any] | None = None) -> None:
    page_hero(
        "Confirmed database",
        title,
        "Search, compare, open, edit, map, and update properties without hunting through workbook tabs.",
        ["Cards for scanning", "Table for sorting", "Every edit goes to Postgres"],
    )
    filters = asset_filters()
    filters.update(preset_filters or {})
    try:
        rows = api("GET", "/assets", params=filters)
    except Exception as exc:
        st.error(str(exc))
        return
    q1, q2, q3 = st.columns([2, 1, 1])
    quick_search = q1.text_input("Quick search", placeholder="Search title, code, owner, broker, locality")
    view_mode = q2.segmented_control("View", ["Cards", "Table"], default="Cards")
    sort_mode = q3.selectbox("Sort", ["Newest first", "Workability high", "Price high", "Title A-Z"])
    rows = filter_rows_locally(rows, quick_search)
    if sort_mode == "Workability high":
        rows = sorted(rows, key=lambda row: to_int(row.get("workability_rating")), reverse=True)
    elif sort_mode == "Price high":
        rows = sorted(rows, key=lambda row: to_float(row.get("asking_price")), reverse=True)
    elif sort_mode == "Title A-Z":
        rows = sorted(rows, key=lambda row: str(row.get("title") or "").lower())

    st.caption(f"{len(rows)} matching properties")
    df = compact_df(rows)
    if rows and view_mode == "Cards":
        for start in range(0, min(len(rows), 12), 3):
            cols = st.columns(3)
            for col, row in zip(cols, rows[start : start + 3]):
                with col:
                    asset_card(row)
        if len(rows) > 12:
            st.info("Showing first 12 cards. Switch to Table view for the full result set.")
    else:
        if not df.empty:
            st.dataframe(
                df,
                use_container_width=True,
                hide_index=True,
                column_config={
                    "asset_code": st.column_config.TextColumn("Code", width="small"),
                    "title": st.column_config.TextColumn("Property", width="large"),
                    "asking_price": st.column_config.NumberColumn("Ask", format="%.0f"),
                    "workability_rating": st.column_config.ProgressColumn("Workability", min_value=0, max_value=10),
                },
            )
        else:
            empty_panel("No matching assets yet. Import or approve leads to build the confirmed database.")
    if rows:
        selected_id = st.selectbox(
            "Open property file",
            [row["id"] for row in rows],
            format_func=lambda i: next(f"{row.get('asset_code') or row['id']} - {row['title']}" for row in rows if row["id"] == i),
        )
        asset_detail(selected_id)


def brokerage_page() -> None:
    asset_list_page("Brokerage Pipeline", preset_filters={"asset_type": "brokerage_listing"})


def asset_detail(asset_id: int) -> None:
    asset = api("GET", f"/assets/{asset_id}")
    st.divider()
    location = ", ".join(part for part in [asset.get("locality"), asset.get("tehsil"), asset.get("district")] if part)
    chips = [
        asset.get("asset_code") or f"Asset {asset_id}",
        labelize(asset.get("asset_type")),
        labelize(asset.get("status")),
        location or "Location pending",
    ]
    page_hero(
        "Property file",
        asset["title"],
        "One working record for ownership, pricing, map position, documents, risks, updates, and AI notes.",
        chips,
    )
    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Asking", money(asset.get("asking_price")))
    m2.metric("Expected", money(asset.get("expected_price")))
    m3.metric("Workability", f"{to_int(asset.get('workability_rating'))}/10")
    m4.metric("Bottleneck", f"{to_int(asset.get('bottleneck_rating'))}/10")
    loc_score = asset.get("location_score")
    m5.metric("Location Rating", f"{float(loc_score):.1f}/10" if loc_score is not None else "N/A")

    tabs = st.tabs(["Snapshot", "Update Log", "Edit Property", "Map", "Contacts", "Documents", "AI Notes"])
    with tabs[0]:
        left, right = st.columns([1.25, 0.75], gap="large")
        with left:
            st.markdown('<div class="section-label">Property identity</div>', unsafe_allow_html=True)
            field_grid(
                {
                    "Type": asset.get("asset_type"),
                    "Status": asset.get("status"),
                    "Source": asset.get("source"),
                    "District": asset.get("district"),
                    "Tehsil": asset.get("tehsil"),
                    "Locality": asset.get("locality"),
                    "Land area": asset.get("land_area"),
                    "Built-up": asset.get("built_up_area"),
                    "Asking": asset.get("asking_price"),
                    "Expected": asset.get("expected_price"),
                }
            )
            st.markdown('<div class="section-label">Address and intent</div>', unsafe_allow_html=True)
            st.markdown(
                f'<div class="note-panel">{escape(asset.get("address") or "No address or location narrative saved yet.")}</div>',
                unsafe_allow_html=True,
            )
        with right:
            st.markdown('<div class="section-label">Deal health</div>', unsafe_allow_html=True)
            st.markdown(
                f"""
                <div class="asset-card">
                    {score_html("Workability", asset.get("workability_rating"))}
                    <br>
                    {score_html("Bottleneck", asset.get("bottleneck_rating"))}
                    <div class="asset-card-footer">
                        <span>{escape(labelize(asset.get("legal_status") or "Legal pending"))}</span>
                        <span>{escape(labelize(asset.get("zoning_status") or "Zoning pending"))}</span>
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )
            st.markdown('<div class="section-label">Bottlenecks</div>', unsafe_allow_html=True)
            st.markdown(
                f'<div class="note-panel">{escape(asset.get("bottleneck_notes") or "No bottleneck notes yet.")}</div>',
                unsafe_allow_html=True,
            )
            if asset.get("location_score_reason"):
                st.markdown('<div class="section-label">Location Rating Analysis</div>', unsafe_allow_html=True)
                st.markdown(
                    f'<div class="note-panel" style="border-left-color: #3b82f6;"><strong>Score: {asset.get("location_score")}/10</strong><br>{escape(asset.get("location_score_reason"))}</div>',
                    unsafe_allow_html=True,
                )
    with tabs[1]:
        left, right = st.columns([1, 1], gap="large")
        with left:
            st.markdown('<div class="section-label">Recent movement</div>', unsafe_allow_html=True)
            updates = asset.get("updates") or []
            if updates:
                for update in updates[:8]:
                    st.markdown(
                        f"""
                        <div class="asset-card">
                            <div class="chip-row"><span class="chip">{escape(labelize(update.get("update_type")))}</span></div>
                            <div class="asset-card-title">{escape(clip_text(update.get("update_text"), 120))}</div>
                            <div class="asset-card-meta">{escape(str(update.get("created_at") or ""))}</div>
                        </div>
                        """,
                        unsafe_allow_html=True,
                    )
            else:
                empty_panel("No updates yet. Add the first movement note from the form.")
        with right:
            st.markdown('<div class="section-label">Add an update</div>', unsafe_allow_html=True)
            with st.form(f"update_{asset_id}"):
                c1, c2, c3 = st.columns(3)
                update_type = c1.selectbox("Update type", UPDATE_TYPES, index=0, key=f"update_type_{asset_id}")
                new_status = c2.text_input("New status", value="", placeholder="Optional", key=f"update_status_{asset_id}")
                revised_price = c3.number_input("Revised asking price", min_value=0.0, value=0.0, key=f"update_price_{asset_id}")
                update_text = st.text_area(
                    "What happened?",
                    placeholder="Example: Seller revised price after today's call; registry papers still pending.",
                    height=150,
                    key=f"update_text_{asset_id}",
                )
                if st.form_submit_button("Save movement", use_container_width=True, type="primary") and update_text:
                    try:
                        api("POST", f"/assets/{asset_id}/updates", json={"update_type": update_type, "update_text": update_text})
                        asset_changes = clean_payload({"status": new_status, "asking_price": revised_price})
                        if asset_changes:
                            api("PUT", f"/assets/{asset_id}", json=asset_changes)
                        st.success("Update saved.")
                        st.rerun()
                    except Exception as exc:
                        st.error(str(exc))
    with tabs[2]:
        st.caption("Edit the confirmed database record. Updates are saved to the database immediately.")
        with st.form(f"asset_detail_edit_{asset_id}"):
            payload = asset_payload_form(asset, key_prefix=f"asset_detail_{asset_id}")
            if st.form_submit_button("Save property changes", use_container_width=True, type="primary"):
                try:
                    saved = api("PUT", f"/assets/{asset_id}", json=payload)
                    st.success(f"Saved {saved['asset_code']}: {saved['title']}")
                    st.rerun()
                except Exception as exc:
                    st.error(str(exc))
    with tabs[3]:
        if asset.get("latitude") and asset.get("longitude"):
            fmap = folium.Map(location=[asset["latitude"], asset["longitude"]], zoom_start=14)
            folium.Marker([asset["latitude"], asset["longitude"]], tooltip=asset["title"]).add_to(fmap)
            st_folium(fmap, height=420, use_container_width=True)
            st.link_button("Open Google Maps", asset["google_maps_link"] or f"https://www.google.com/maps?q={asset['latitude']},{asset['longitude']}")
        else:
            empty_panel("No coordinates saved yet. Add latitude and longitude in Edit Property to unlock map inspection.")
    with tabs[4]:
        c1, c2 = st.columns(2)
        c1.metric("Owner", asset.get("owner_name") or "-")
        c2.metric("Broker", asset.get("broker_name") or "-")
        if asset.get("contacts"):
            st.dataframe(pd.DataFrame(asset["contacts"]), use_container_width=True, hide_index=True)
        else:
            empty_panel("No contact rows yet.")
        with st.form(f"contact_{asset_id}"):
            c1, c2, c3 = st.columns(3)
            contact_payload = {
                "name": c1.text_input("Contact name"),
                "relationship_type": c2.text_input("Relationship", value="related"),
                "phone": c3.text_input("Phone"),
                "whatsapp": c1.text_input("WhatsApp"),
                "email": c2.text_input("Email"),
                "company": c3.text_input("Company"),
                "notes": st.text_area("Contact notes"),
            }
            if st.form_submit_button("Add contact", use_container_width=True) and contact_payload["name"]:
                api("POST", f"/assets/{asset_id}/contacts", json={k: v for k, v in contact_payload.items() if v})
                st.rerun()
    with tabs[5]:
        if asset.get("documents"):
            st.dataframe(pd.DataFrame(asset["documents"]), use_container_width=True, hide_index=True)
        else:
            empty_panel("No documents linked yet.")
        with st.form(f"document_{asset_id}"):
            c1, c2 = st.columns(2)
            document_payload = {
                "document_name": c1.text_input("Document name"),
                "document_type": c2.text_input("Document type"),
                "url": st.text_input("Document URL"),
                "notes": st.text_area("Document notes"),
            }
            if st.form_submit_button("Add document", use_container_width=True) and document_payload["document_name"]:
                api("POST", f"/assets/{asset_id}/documents", json={k: v for k, v in document_payload.items() if v})
                st.rerun()
    with tabs[6]:
        q = st.text_input("Ask about this asset", value=f"Summarize bottlenecks and workability for asset {asset.get('asset_code') or asset_id}")
        if st.button("Ask", key=f"ask_asset_{asset_id}", use_container_width=True):
            answer = api("POST", "/ask", json={"question": q})
            st.write(answer["answer"])


def asset_payload_form(existing: dict[str, Any] | None = None, key_prefix: str = "asset") -> dict[str, Any]:
    existing = existing or {}
    c1, c2, c3 = st.columns(3)
    payload: dict[str, Any] = {
        "title": c1.text_input("Title", value=existing.get("title", ""), key=f"{key_prefix}_title"),
        "asset_type": c2.selectbox(
            "Asset type",
            ASSET_TYPES[1:],
            index=type_index(existing.get("asset_type", "land")),
            key=f"{key_prefix}_asset_type",
        ),
        "status": c3.text_input("Status", value=existing.get("status", "lead"), key=f"{key_prefix}_status"),
    }
    c4, c5, c6, c7, c8 = st.columns(5)
    payload.update(
        {
            "source": c4.text_input("Source", value=existing.get("source") or "manual", key=f"{key_prefix}_source"),
            "district": c5.text_input("District", value=existing.get("district") or "", key=f"{key_prefix}_district"),
            "tehsil": c6.text_input("Tehsil", value=existing.get("tehsil") or "", key=f"{key_prefix}_tehsil"),
            "locality": c7.text_input("Locality", value=existing.get("locality") or "", key=f"{key_prefix}_locality"),
            "area_name": c8.text_input("Area/name", value=existing.get("area_name") or "", key=f"{key_prefix}_area_name"),
        }
    )
    c9, c10 = st.columns([2, 1])
    payload["address"] = c9.text_area("Address", value=existing.get("address") or "", key=f"{key_prefix}_address")
    payload["state"] = c10.text_input("State", value=existing.get("state") or "Rajasthan", key=f"{key_prefix}_state")
    c11, c12, c13, c14 = st.columns(4)
    payload.update(
        {
            "land_area": c11.text_input("Land area", value=existing.get("land_area") or "", key=f"{key_prefix}_land_area"),
            "built_up_area": c12.text_input("Built-up area", value=existing.get("built_up_area") or "", key=f"{key_prefix}_built_up_area"),
            "asking_price": c13.number_input("Asking price", min_value=0.0, value=to_float(existing.get("asking_price")), key=f"{key_prefix}_asking_price"),
            "expected_price": c14.number_input("Expected price", min_value=0.0, value=to_float(existing.get("expected_price")), key=f"{key_prefix}_expected_price"),
        }
    )
    c15, c16, c17, c18 = st.columns(4)
    payload.update(
        {
            "latitude": c15.number_input("Latitude", value=to_float(existing.get("latitude")), format="%.8f", key=f"{key_prefix}_latitude"),
            "longitude": c16.number_input("Longitude", value=to_float(existing.get("longitude")), format="%.8f", key=f"{key_prefix}_longitude"),
            "workability_rating": c17.number_input("Workability", min_value=0, max_value=10, value=to_int(existing.get("workability_rating")), key=f"{key_prefix}_workability"),
            "bottleneck_rating": c18.number_input("Bottleneck", min_value=0, max_value=10, value=to_int(existing.get("bottleneck_rating")), key=f"{key_prefix}_bottleneck"),
        }
    )
    payload["google_maps_link"] = st.text_input("Google Maps link", value=existing.get("google_maps_link") or "", key=f"{key_prefix}_google_maps_link")
    payload["legal_status"] = st.text_area("Legal status", value=existing.get("legal_status") or "", key=f"{key_prefix}_legal_status")
    payload["zoning_status"] = st.text_area("Zoning status", value=existing.get("zoning_status") or "", key=f"{key_prefix}_zoning_status")
    payload["bottleneck_notes"] = st.text_area("Bottleneck notes", value=existing.get("bottleneck_notes") or "", key=f"{key_prefix}_bottleneck_notes")
    return clean_payload(payload, keep_zero_fields={"workability_rating", "bottleneck_rating"})


def add_edit_page() -> None:
    page_hero(
        "Manual entry studio",
        "Add or Edit Property",
        "Create a clean confirmed record directly, or load an existing asset when a deal changes.",
        ["Postgres-backed", "Map fields included", "Ratings built in"],
    )
    mode = st.segmented_control("Mode", ["Add", "Edit"], default="Add") or "Add"
    existing = None
    asset_id = None
    if mode == "Edit":
        asset_id = st.number_input("Asset ID", min_value=1, step=1)
        if st.button("Load asset"):
            st.session_state["edit_asset"] = api("GET", f"/assets/{asset_id}")
        existing = st.session_state.get("edit_asset")
    with st.form("asset_form"):
        payload = asset_payload_form(existing, key_prefix=f"{mode.lower()}_asset")
        submitted = st.form_submit_button("Save asset", use_container_width=True)
    if submitted:
        try:
            if mode == "Edit" and existing:
                saved = api("PUT", f"/assets/{existing['id']}", json=payload)
            else:
                saved = api("POST", "/assets", json=payload)
            st.success(f"Saved {saved['asset_code']}: {saved['title']}")
        except Exception as exc:
            st.error(str(exc))


def approval_summary_rows(rows: list[dict[str, Any]]) -> pd.DataFrame:
    summary: list[dict[str, Any]] = []
    for row in rows:
        payload = row.get("edited_payload") or row.get("payload") or {}
        notes = payload.get("bottleneck_notes") or payload.get("review_reason") or ""
        summary.append(
            {
                "Select": False,
                "id": row["id"],
                "title": row.get("title") or payload.get("title") or "Untitled",
                "class": payload.get("asset_type") or "",
                "source": row.get("source") or "",
                "status": payload.get("status") or row.get("status") or "",
                "locality": payload.get("locality") or payload.get("area_name") or "",
                "district": payload.get("district") or "",
                "land_area": payload.get("land_area") or "",
                "asking_price": payload.get("asking_price") or "",
                "owner": payload.get("owner_name") or "",
                "broker": payload.get("broker_name") or "",
                "notes": str(notes).replace("\n", " ")[:140],
            }
        )
    return pd.DataFrame(summary)


def approval_payload_form(item: dict[str, Any]) -> tuple[dict[str, Any], str, bool, bool]:
    base = dict(item.get("edited_payload") or item["payload"])
    raw_source = base.get("raw_source") or {}
    key = f"approval_{item['id']}"

    c1, c2, c3 = st.columns(3)
    title = c1.text_input("Property / deal title", value=base.get("title") or item.get("title") or "", key=f"{key}_title")
    asset_type = c2.selectbox("Asset type", ASSET_TYPES[1:], index=type_index(base.get("asset_type")), key=f"{key}_asset_type")
    status = c3.text_input("Status", value=base.get("status") or "lead", key=f"{key}_status")

    c4, c5, c6, c7 = st.columns(4)
    locality = c4.text_input("Locality", value=base.get("locality") or raw_source.get("Location / Area") or "", key=f"{key}_locality")
    area_name = c5.text_input("Area / project name", value=base.get("area_name") or raw_source.get("Property / Deal Name") or "", key=f"{key}_area_name")
    tehsil = c6.text_input("Tehsil", value=base.get("tehsil") or "", key=f"{key}_tehsil")
    district = c7.text_input("District", value=base.get("district") or "", key=f"{key}_district")

    c8, c9 = st.columns([2, 1])
    address = c8.text_area("Address / location description", value=base.get("address") or "", key=f"{key}_address")
    state = c9.text_input("State", value=base.get("state") or "Rajasthan", key=f"{key}_state")

    c10, c11, c12, c13 = st.columns(4)
    land_area = c10.text_input("Land area", value=base.get("land_area") or raw_source.get("Area / Size") or "", key=f"{key}_land_area")
    built_up_area = c11.text_input("Built-up area", value=base.get("built_up_area") or "", key=f"{key}_built_up_area")
    asking_price = c12.number_input("Asking price", min_value=0.0, value=to_float(base.get("asking_price")), key=f"{key}_asking_price")
    expected_price = c13.number_input("Expected price", min_value=0.0, value=to_float(base.get("expected_price")), key=f"{key}_expected_price")

    c14, c15, c16 = st.columns(3)
    latitude = c14.number_input("Latitude", value=to_float(base.get("latitude")), format="%.8f", key=f"{key}_latitude")
    longitude = c15.number_input("Longitude", value=to_float(base.get("longitude")), format="%.8f", key=f"{key}_longitude")
    google_maps_link = c16.text_input("Google Maps link", value=base.get("google_maps_link") or "", key=f"{key}_google_maps_link")

    c17, c18, c19, c20 = st.columns(4)
    owner_name = c17.text_input("Owner name", value=base.get("owner_name") or "", key=f"{key}_owner")
    broker_name = c18.text_input("Broker / referrer", value=base.get("broker_name") or "", key=f"{key}_broker")
    workability_rating = c19.number_input("Workability", min_value=0, max_value=10, value=to_int(base.get("workability_rating")), key=f"{key}_workability")
    bottleneck_rating = c20.number_input("Bottleneck", min_value=0, max_value=10, value=to_int(base.get("bottleneck_rating")), key=f"{key}_bottleneck")

    key_people = st.text_input("Key people / parties", value=base.get("key_people") or raw_source.get("Key People / Referrer / Owner") or "", key=f"{key}_key_people")
    legal_status = st.text_area("Legal status", value=base.get("legal_status") or "", key=f"{key}_legal")
    zoning_status = st.text_area("Zoning status", value=base.get("zoning_status") or "", key=f"{key}_zoning")
    bottleneck_notes = st.text_area(
        "Notes, bottlenecks, risks, next step",
        value=base.get("bottleneck_notes") or raw_source.get("Detailed Notes") or "",
        height=180,
        key=f"{key}_notes",
    )
    docs_text = st.text_area(
        "Documents / collateral, one per line: name | url | type | notes",
        value=document_lines(base.get("documents"), raw_source.get("External Links Captured"), base.get("notion_page_url")),
        height=130,
        key=f"{key}_documents",
    )
    review_notes = st.text_input("Review decision notes", key=f"{key}_review_notes")

    merged = base | clean_payload(
        {
            "title": title,
            "asset_type": asset_type,
            "status": status,
            "source": base.get("source") or item["source"],
            "locality": locality,
            "area_name": area_name,
            "tehsil": tehsil,
            "district": district,
            "state": state,
            "address": address,
            "latitude": latitude,
            "longitude": longitude,
            "google_maps_link": google_maps_link,
            "land_area": land_area,
            "built_up_area": built_up_area,
            "asking_price": asking_price,
            "expected_price": expected_price,
            "owner_name": owner_name,
            "broker_name": broker_name,
            "key_people": key_people,
            "workability_rating": workability_rating,
            "bottleneck_rating": bottleneck_rating,
            "legal_status": legal_status,
            "zoning_status": zoning_status,
            "bottleneck_notes": bottleneck_notes,
            "documents": parse_document_lines(docs_text),
        },
        keep_zero_fields={"workability_rating", "bottleneck_rating"},
    )
    approve = st.form_submit_button("Approve into assets", use_container_width=True)
    reject = st.form_submit_button("Reject", use_container_width=True)
    return merged, review_notes, approve, reject


def approvals_page() -> None:
    page_hero(
        "Review gate",
        "Approval Inbox",
        "Turn raw Sheets and Notion leads into clean property records before they touch the confirmed database.",
        ["Bulk approve", "Classify lead type", "Edit before publish", "Source payload preserved"],
    )
    status_filter = st.segmented_control("Queue status", ["pending", "approved", "rejected", "all"], default="pending") or "pending"
    rows = api("GET", "/approvals", params={"status": status_filter})
    if not rows:
        empty_panel("No approval items in this status.")
        return
    pending_count = len([row for row in rows if row.get("status") == "pending"])
    source_count = len(set(row.get("source") for row in rows if row.get("source")))
    c0, c1, c2 = st.columns(3)
    c0.metric("Rows in view", len(rows))
    c1.metric("Pending here", pending_count)
    c2.metric("Sources", source_count)

    st.markdown('<div class="section-label">Triage list</div>', unsafe_allow_html=True)
    st.caption("Tick rows for bulk approval, or open one item below for the full pre-filled form.")
    summary_df = approval_summary_rows(rows)
    edited_df = st.data_editor(
        summary_df,
        hide_index=True,
        use_container_width=True,
        height=360,
        disabled=[column for column in summary_df.columns if column != "Select"],
        column_config={
            "Select": st.column_config.CheckboxColumn("Select", width="small"),
            "id": st.column_config.NumberColumn("ID", width="small"),
            "title": st.column_config.TextColumn("Title", width="large"),
            "notes": st.column_config.TextColumn("Notes", width="large"),
        },
        key=f"approval_table_{status_filter}",
    )
    selected_ids = edited_df.loc[edited_df["Select"], "id"].astype(int).tolist() if "Select" in edited_df else []
    if status_filter == "pending":
        st.markdown('<div class="section-label">Bulk decision</div>', unsafe_allow_html=True)
        c1, c2, c3, c4, c5 = st.columns([1.2, 1.2, 1, 1, 1])
        classification = c1.selectbox("Classification", ["Keep existing", "Land prospect", "Brokerage opportunity"], key="bulk_approval_classification")
        decision_notes = c2.text_input("Decision notes", key="bulk_approval_notes")
        c3.metric("Selected", len(selected_ids))
        asset_type_override = {
            "Land prospect": "land",
            "Brokerage opportunity": "brokerage_listing",
        }.get(classification)
        if c4.button("Approve", disabled=not selected_ids, use_container_width=True, type="primary"):
            try:
                result = api(
                    "POST",
                    "/approvals/bulk/approve",
                    json={
                        "approval_ids": selected_ids,
                        "asset_type_override": asset_type_override,
                        "notes": decision_notes,
                    },
                )
                st.success(f"Approved {result['approved_count']} items. Failed: {result['failed_count']}.")
                if result.get("failed"):
                    st.dataframe(pd.DataFrame(result["failed"]), use_container_width=True, hide_index=True)
                st.rerun()
            except Exception as exc:
                st.error(str(exc))
        if c5.button("Reject", disabled=not selected_ids, use_container_width=True):
            try:
                result = api("POST", "/approvals/bulk/reject", json={"approval_ids": selected_ids, "notes": decision_notes})
                st.success(f"Rejected {result['rejected_count']} items. Failed: {result['failed_count']}.")
                st.rerun()
            except Exception as exc:
                st.error(str(exc))
    st.markdown('<div class="section-label">Detailed approval form</div>', unsafe_allow_html=True)
    selected_id = st.selectbox(
        "Open one item for detailed review/edit",
        [row["id"] for row in rows],
        format_func=lambda item_id: next(
            f"#{row['id']} · {row.get('title') or 'Untitled'} · {row['source']} · {row['status']}"
            for row in rows
            if row["id"] == item_id
        ),
    )
    item = next(row for row in rows if row["id"] == selected_id)
    if item["status"] != "pending":
        st.info(f"Status: {labelize(item['status'])}")
        with st.expander("Stored approval payload", expanded=True):
            st.json(item.get("edited_payload") or item["payload"])
        return
    with st.form(f"approval_form_{item['id']}"):
        payload, notes, approve, reject = approval_payload_form(item)
        if approve:
            try:
                result = api("POST", f"/approvals/{item['id']}/approve", json={"edited_payload": payload, "notes": notes})
                st.success(f"Approved into {result['asset_code']}")
                st.rerun()
            except Exception as exc:
                st.error(str(exc))
        if reject:
            try:
                api("POST", f"/approvals/{item['id']}/reject", json={"notes": notes})
                st.success("Rejected")
                st.rerun()
            except Exception as exc:
                st.error(str(exc))
    if st.checkbox("Show advanced source payload", key=f"show_payload_{item['id']}"):
        st.json(item.get("edited_payload") or item["payload"])


def import_page() -> None:
    page_hero(
        "Excel off-ramp",
        "Import Historical Workbooks",
        "Bring older land and deal evaluations into the review queue without polluting the confirmed database.",
        ["Flexible columns", "Raw row stored", "Manual review first"],
    )
    with st.container(border=True):
        upload = st.file_uploader("Upload historical Excel", type=["xlsx", "xls"])
        if upload and st.button("Import to review queue", use_container_width=True, type="primary"):
            files = {"file": (upload.name, upload.getvalue(), upload.type)}
            try:
                result = api("POST", "/import/excel", files=files)
                st.success(f"Queued {result['queued_count']} rows. Incomplete rows flagged: {result['incomplete_count']}.")
                st.json(result)
            except Exception as exc:
                st.error(str(exc))


def sync_page() -> None:
    page_hero(
        "Source listeners",
        "Sync Status",
        "Run Google Sheets and Notion listeners manually. The scheduled job runs the same combined sync at 7 AM and 7 PM.",
        ["Sheets", "Pearl Spytech", "Brokerage New Deals", "Deduped queue"],
    )
    if st.button("Run all source listeners", use_container_width=True, type="primary"):
        try:
            result = api("POST", "/sync/all")
            st.json(result)
        except Exception as exc:
            st.error(str(exc))
    c1, c2 = st.columns(2)
    if c1.button("Run Google Sheets sync"):
        try:
            result = api("POST", "/sync/google-sheets")
            st.json(result)
        except Exception as exc:
            st.error(str(exc))
    if c2.button("Run Notion sync"):
        try:
            result = api("POST", "/sync/notion")
            st.json(result)
        except Exception as exc:
            st.error(str(exc))
    st.caption("Google Sheets needs a service-account JSON configured in env and the sheet shared with that service account.")
    st.markdown('<div class="section-label">Notion permission fix</div>', unsafe_allow_html=True)
    st.markdown(
        """
        <div class="note-panel">
            The listener reads only the three configured project pages and their <strong>Tasks</strong> /
            <strong>Notes</strong> relation properties. If a page says relation entries are hidden,
            open that project page and make sure the task/note pages shown inside it are visible to the
            <strong>Land/JV/Brokerage Tracker</strong> integration.
        </div>
        """,
        unsafe_allow_html=True,
    )
    n1, n2, n3 = st.columns(3)
    n1.link_button("Pearl Spytech", "https://www.notion.so/Pearl-Spytech-New-Projects-29a5c898ef91805c8f62caccbd26b0af", use_container_width=True)
    n2.link_button("Analyze LRM", "https://www.notion.so/Analyze-the-Property-deals-and-update-LRM-2995c898ef918040a360c467e4837e4c", use_container_width=True)
    n3.link_button("Brokerage New Deals", "https://www.notion.so/Brokerage-New-Deals-29a5c898ef91801598afdcf276fe057b", use_container_width=True)
    st.caption("WhatsApp ingestion is not implemented in this MVP. The approval queue and source model already reserve the `whatsapp` path for later extracted leads.")


def ai_page() -> None:
    page_hero(
        "Read-only intelligence",
        "AI Assistant",
        "Ask concise questions about assets, brokers, owners, bottlenecks, locations, approvals, and pipeline status.",
        ["Read-only SQL", "Source rows shown", "Good for review calls"],
    )
    with st.container(border=True):
        question = st.text_area("Question", placeholder="Which active Jaipur land leads have high workability and legal bottlenecks?", height=130)
        if st.button("Ask assistant", use_container_width=True, type="primary") and question:
            try:
                result = api("POST", "/ask", json={"question": question})
                st.markdown(f'<div class="note-panel">{escape(result["answer"])}</div>', unsafe_allow_html=True)
                with st.expander("Source rows"):
                    st.dataframe(pd.DataFrame(result["source_rows"]), use_container_width=True)
            except Exception as exc:
                st.error(str(exc))


def ai_agent_page() -> None:
    page_hero(
        "Database copilot",
        "AI Database Agent",
        "Tell the app what changed. It drafts database edits, then waits for your confirmation before touching the record.",
        ["Add updates", "Change ratings", "Revise price", "No deletes"],
    )
    instruction = st.text_area(
        "Instruction",
        placeholder="Example: For asset 12, add an update that seller revised price today and set workability rating to 8.",
        height=120,
        key="agent_instruction",
    )
    if st.button("Plan action", use_container_width=True, type="primary") and instruction:
        try:
            st.session_state["agent_plan"] = api("POST", "/agent/plan", json={"instruction": instruction})
            st.session_state["agent_instruction"] = instruction
        except Exception as exc:
            st.error(str(exc))

    plan = st.session_state.get("agent_plan")
    if not plan:
        return
    st.write(plan.get("summary"))
    if plan.get("answer"):
        st.info(plan["answer"])
    if plan.get("matched_assets"):
        with st.expander("Matched assets", expanded=True):
            st.dataframe(compact_df(plan["matched_assets"]), use_container_width=True, hide_index=True)
    actions = plan.get("actions") or []
    if actions:
        st.write("Proposed actions")
        st.dataframe(pd.DataFrame(actions), use_container_width=True, hide_index=True)
        if st.button("Apply proposed actions", type="primary", use_container_width=True):
            try:
                result = api(
                    "POST",
                    "/agent/apply",
                    json={"instruction": st.session_state.get("agent_instruction", instruction), "actions": actions},
                )
                st.success(f"Applied {result['applied_count']} action(s). Failed: {result['failed_count']}.")
                if result.get("failed"):
                    st.dataframe(pd.DataFrame(result["failed"]), use_container_width=True, hide_index=True)
                st.session_state.pop("agent_plan", None)
            except Exception as exc:
                st.error(str(exc))
    else:
        st.warning("No database edit action was proposed. Try naming an asset ID/code or a very specific property/location.")


def export_page() -> None:
    page_hero(
        "Safety valve",
        "Export Database",
        "Download a full Excel backup while the real working data stays in Postgres.",
        ["Assets", "Updates", "Contacts", "Documents", "Approval history"],
    )
    try:
        stats = api("GET", "/stats")
        c1, c2, c3 = st.columns(3)
        c1.metric("Confirmed assets", stats["total_assets"])
        c2.metric("Pending approvals", stats["pending_approvals"])
        c3.metric("New leads this week", stats["new_leads_this_week"])
    except Exception:
        pass

    if st.button("Prepare Excel export", use_container_width=True):
        try:
            st.session_state["export_bytes"] = api_bytes("GET", "/export/excel")
            st.session_state["export_filename"] = "land_jv_tracker_export.xlsx"
        except Exception as exc:
            st.error(str(exc))

    if st.session_state.get("export_bytes"):
        st.download_button(
            "Download Excel backup",
            data=st.session_state["export_bytes"],
            file_name=st.session_state.get("export_filename", "land_jv_tracker_export.xlsx"),
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
        )


def dashboard_page() -> None:
    page_hero(
        "Daily command center",
        "What needs attention today?",
        "Start with pending approvals, high-workability opportunities, brokerage leads, and recent pipeline movement.",
        ["Approve before publish", "Track every update", "Use AI for edits", "Export anytime"],
    )
    try:
        assets = api("GET", "/assets")
    except Exception as exc:
        st.error(str(exc))
        assets = []
    try:
        approvals = api("GET", "/approvals", params={"status": "pending"})
    except Exception:
        approvals = []

    c1, c2, c3 = st.columns([1.2, 1, 1], gap="large")
    with c1:
        st.markdown('<div class="section-label">Next best actions</div>', unsafe_allow_html=True)
        st.markdown(
            """
            <div class="note-panel">
                Suggested rhythm: sync sources, approve clean leads, update active assets after calls,
                export a backup when a major batch is complete.
            </div>
            """,
            unsafe_allow_html=True,
        )
    with c2:
        st.metric("Pending approvals", len(approvals))
        brokerage_count = len([row for row in assets if row.get("asset_type") == "brokerage_listing"])
        st.metric("Brokerage listings", brokerage_count)
    with c3:
        high_workability = len([row for row in assets if to_int(row.get("workability_rating")) >= 7])
        unmapped = len([row for row in assets if not row.get("latitude") or not row.get("longitude")])
        st.metric("High workability", high_workability)
        st.metric("Need map fix", unmapped)

    # Interactive Portfolio Map Section
    mapped_assets = [row for row in assets if row.get("latitude") and row.get("longitude")]
    if mapped_assets:
        st.markdown('<div class="section-label">Interactive Portfolio Map</div>', unsafe_allow_html=True)
        # Calculate mean coordinates to center the map
        mean_lat = sum(float(row["latitude"]) for row in mapped_assets) / len(mapped_assets)
        mean_lon = sum(float(row["longitude"]) for row in mapped_assets) / len(mapped_assets)
        
        fmap = folium.Map(location=[mean_lat, mean_lon], zoom_start=8, control_scale=True)
        
        # Color coding marker icons based on type
        for row in mapped_assets:
            color = "blue"
            if row.get("asset_type") == "brokerage_listing":
                color = "purple"
            elif row.get("asset_type") == "land":
                color = "green"
            elif row.get("asset_type") == "jv":
                color = "orange"
            
            loc_score = row.get("location_score")
            score_txt = f"{float(loc_score):.1f}/10" if loc_score is not None else "N/A"
            
            popup_html = f"""
            <div style="font-family: 'Inter', sans-serif; color: #111215; font-size: 12px; width: 220px; line-height: 1.4;">
                <strong style="font-size: 13px; color: #a67b1e; display: block; margin-bottom: 4px;">{escape(row['title'])}</strong>
                <b>Code:</b> {escape(row.get('asset_code') or '-')}<br>
                <b>Type:</b> {escape(labelize(row['asset_type']))}<br>
                <b>Locality:</b> {escape(row.get('locality') or '-')}<br>
                <b>Asking:</b> {escape(money(row.get('asking_price')))}<br>
                <b>Location Score:</b> {score_txt}<br>
            </div>
            """
            
            folium.Marker(
                location=[row["latitude"], row["longitude"]],
                popup=folium.Popup(popup_html, max_width=250),
                tooltip=row["title"],
                icon=folium.Icon(color=color, icon="info-sign")
            ).add_to(fmap)
            
        st_folium(fmap, height=380, use_container_width=True)

    left, right = st.columns([1.15, 0.85], gap="large")
    with left:
        st.markdown('<div class="section-label">Strongest opportunities</div>', unsafe_allow_html=True)
        strongest = sorted(assets, key=lambda row: to_int(row.get("workability_rating")), reverse=True)[:6]
        if strongest:
            for start in range(0, len(strongest), 3):
                cols = st.columns(3)
                for col, row in zip(cols, strongest[start : start + 3]):
                    with col:
                        asset_card(row)
        else:
            empty_panel("Approve or add assets to build your opportunity board.")
    with right:
        st.markdown('<div class="section-label">Approval inbox preview</div>', unsafe_allow_html=True)
        if approvals:
            preview = approval_summary_rows(approvals[:8])
            st.dataframe(
                preview.drop(columns=["Select"], errors="ignore"),
                use_container_width=True,
                hide_index=True,
                height=330,
            )
        else:
            empty_panel("No pending approvals right now.")


def main() -> None:
    if not st.session_state.get("token"):
        login_page()
        return

    apply_theme()
    with st.sidebar:
        st.title("Land and JV")
        st.caption(f"Signed in as {st.session_state.get('username', 'internal')}")
        page = st.radio(
            "Workspace",
            ["Dashboard", "Assets", "Brokerage", "Add / Edit", "Approvals", "Import", "Export", "Sync", "AI Assistant", "AI DB Agent"],
        )
        st.divider()
        st.caption("Daily flow: sync, approve, update, export.")
        if st.button("Log out", use_container_width=True):
            st.session_state.clear()
            st.rerun()

    stats_bar()
    if page == "Dashboard":
        dashboard_page()
    elif page == "Assets":
        asset_list_page()
    elif page == "Brokerage":
        brokerage_page()
    elif page == "Add / Edit":
        add_edit_page()
    elif page == "Approvals":
        approvals_page()
    elif page == "Import":
        import_page()
    elif page == "Export":
        export_page()
    elif page == "Sync":
        sync_page()
    elif page == "AI Assistant":
        ai_page()
    elif page == "AI DB Agent":
        ai_agent_page()


if __name__ == "__main__":
    main()
