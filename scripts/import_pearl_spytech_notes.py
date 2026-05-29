from __future__ import annotations

import hashlib
import json
import re
import shutil
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET
from zipfile import ZipFile


NS = {
    "a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
}


def col_index(cell_ref: str) -> int:
    letters = re.sub(r"[^A-Z]", "", cell_ref.upper())
    index = 0
    for letter in letters:
        index = index * 26 + ord(letter) - ord("A") + 1
    return index - 1


def clean(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        if value == "":
            return None
    return value


def excel_serial_to_iso(value: Any) -> str | None:
    if value in (None, ""):
        return None
    try:
        serial = float(value)
    except (TypeError, ValueError):
        return str(value)
    date_value = datetime(1899, 12, 30, tzinfo=timezone.utc) + timedelta(days=serial)
    return date_value.isoformat()


def read_shared_strings(zip_file: ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in zip_file.namelist():
        return []
    root = ET.fromstring(zip_file.read("xl/sharedStrings.xml"))
    return ["".join(node.text or "" for node in item.findall(".//a:t", NS)) for item in root.findall("a:si", NS)]


def cell_value(cell: ET.Element, shared_strings: list[str]) -> Any:
    cell_type = cell.attrib.get("t")
    if cell_type == "inlineStr":
        text_node = cell.find(".//a:t", NS)
        return clean(text_node.text if text_node is not None else None)
    value_node = cell.find("a:v", NS)
    if value_node is None:
        return None
    raw = value_node.text
    if cell_type == "s" and raw is not None:
        return clean(shared_strings[int(raw)])
    return clean(raw)


def parse_workbook(xlsx_path: Path) -> dict[str, list[dict[str, Any]]]:
    with ZipFile(xlsx_path) as zip_file:
        shared_strings = read_shared_strings(zip_file)
        workbook = ET.fromstring(zip_file.read("xl/workbook.xml"))
        rels = ET.fromstring(zip_file.read("xl/_rels/workbook.xml.rels"))
        relmap = {rel.attrib["Id"]: rel.attrib["Target"].lstrip("/") for rel in rels}
        sheets: dict[str, list[dict[str, Any]]] = {}

        for sheet in workbook.find("a:sheets", NS):
            name = sheet.attrib["name"]
            rel_id = sheet.attrib[f"{{{NS['r']}}}id"]
            sheet_path = relmap[rel_id]
            root = ET.fromstring(zip_file.read(sheet_path))
            rows: list[list[Any]] = []
            for row in root.findall(".//a:sheetData/a:row", NS):
                values: list[Any] = []
                for cell in row.findall("a:c", NS):
                    index = col_index(cell.attrib["r"])
                    while len(values) <= index:
                        values.append(None)
                    values[index] = cell_value(cell, shared_strings)
                rows.append(values)

            if not rows:
                sheets[name] = []
                continue
            headers = [
                str(value).strip() if value not in (None, "") else f"Column {index + 1}"
                for index, value in enumerate(rows[0])
            ]
            sheet_rows: list[dict[str, Any]] = []
            for values in rows[1:]:
                row_dict = {
                    headers[index] if index < len(headers) else f"Column {index + 1}": clean(value)
                    for index, value in enumerate(values)
                }
                if any(value not in (None, "") for value in row_dict.values()):
                    sheet_rows.append(row_dict)
            sheets[name] = sheet_rows
        return sheets


def split_semicolon(value: Any) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in str(value).split(";") if item.strip()]


def make_documents(file_names: Any, external_links: Any, notion_page_url: str | None, access_note: str | None) -> list[dict[str, Any]]:
    documents: list[dict[str, Any]] = []
    for name in split_semicolon(file_names):
        document_type = "external_reference"
        lowered = name.lower()
        if lowered.endswith(".pdf"):
            document_type = "pdf"
        elif lowered.endswith((".jpg", ".jpeg", ".png", ".heic")) or "embedded image" in lowered:
            document_type = "image"
        elif lowered.endswith((".xlsx", ".csv")):
            document_type = "spreadsheet"
        documents.append(
            {
                "document_name": name,
                "document_type": document_type,
                "url": notion_page_url,
                "notes": access_note,
            }
        )
    for link in split_semicolon(external_links):
        documents.append(
            {
                "document_name": link,
                "document_type": "external_link",
                "url": link,
                "notes": f"Linked from Notion page: {notion_page_url}" if notion_page_url else None,
            }
        )
    return documents


def unique_documents(documents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str | None, str | None]] = set()
    unique: list[dict[str, Any]] = []
    for document in documents:
        key = (document.get("document_name"), document.get("url"))
        if key in seen:
            continue
        seen.add(key)
        unique.append(document)
    return unique


def normalize_asset_type(category: Any, deal_type: Any, title: Any) -> str:
    text = " ".join(str(item or "").lower() for item in [category, deal_type, title])
    if "jv" in text or "joint venture" in text:
        return "jv"
    if "resale" in text or "refurbish" in text or "redevelopment" in text:
        return "resale_unit"
    if "commercial" in text:
        return "commercial"
    if "rent" in text:
        return "rental"
    if "contact" in text or "proposal" in text:
        return "brokerage_listing"
    return "land"


def source_uid(source: str, row: dict[str, Any], fallback: str) -> str:
    stable = row.get("Notion Page URL") or row.get("Note Title") or row.get("Plot") or fallback
    digest = hashlib.sha256(f"{source}:{stable}".encode("utf-8")).hexdigest()[:24]
    return f"{source}:{digest}"


def attachment_index(rows: list[dict[str, Any]]) -> tuple[dict[str, list[dict[str, Any]]], dict[str, list[dict[str, Any]]]]:
    by_serial: dict[str, list[dict[str, Any]]] = {}
    by_url: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        serial = str(row.get("S No") or "").strip()
        notion_url = row.get("Notion Page URL")
        documents = make_documents(
            row.get("Attachment/File Names"),
            row.get("External Links"),
            notion_url,
            row.get("Access Note"),
        )
        if serial:
            by_serial.setdefault(serial, []).extend(documents)
        if notion_url:
            by_url.setdefault(str(notion_url), []).extend(documents)
    return by_serial, by_url


def property_payload(row: dict[str, Any], serial_docs: dict[str, list[dict[str, Any]]], url_docs: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    serial = str(row.get("S No") or "").strip()
    notion_url = row.get("Notion Page URL")
    documents = make_documents(
        row.get("Attachments / Files Captured"),
        row.get("External Links Captured"),
        notion_url,
        "Private Notion file links were not directly dereferenced; use the Notion page URL to open/download the attachment.",
    )
    documents.extend(serial_docs.get(serial, []))
    if notion_url:
        documents.extend(url_docs.get(str(notion_url), []))
    if row.get("Source URL"):
        documents.extend(make_documents(None, row.get("Source URL"), notion_url, "Source URL captured from notes workbook."))

    notes = []
    if row.get("Detailed Notes"):
        notes.append(str(row["Detailed Notes"]))
    if row.get("Status / Next Step"):
        notes.append(f"Next step: {row['Status / Next Step']}")
    if row.get("Data Quality / Missing Info"):
        notes.append(f"Data quality: {row['Data Quality / Missing Info']}")

    return {
        "title": row.get("Property / Deal Name") or row.get("Note Title"),
        "asset_type": normalize_asset_type(row.get("Record Category"), row.get("Deal Type"), row.get("Note Title")),
        "status": "lead",
        "source": "excel_pearl_spytech_notes",
        "locality": row.get("Location / Area"),
        "area_name": row.get("Property / Deal Name"),
        "land_area": row.get("Area / Size"),
        "bottleneck_notes": "\n\n".join(notes) if notes else None,
        "notion_page_url": notion_url,
        "source_url": row.get("Source URL"),
        "note_title": row.get("Note Title"),
        "record_category": row.get("Record Category"),
        "deal_type": row.get("Deal Type"),
        "price_terms": row.get("Price / Commercial Terms"),
        "key_people": row.get("Key People / Referrer / Owner"),
        "created_at_source": excel_serial_to_iso(row.get("Created")),
        "updated_at_source": excel_serial_to_iso(row.get("Updated")),
        "note_date": excel_serial_to_iso(row.get("Note Date")),
        "documents": unique_documents(documents),
        "raw_source": row,
    }


def older_deal_payload(row: dict[str, Any]) -> dict[str, Any]:
    plot = row.get("Plot")
    street = row.get("Street")
    title = " - ".join(str(part) for part in [plot, street] if part)
    financials = {
        key: row.get(key)
        for key in [
            "Rate",
            "Land Cost",
            "BUA",
            "Dev Cost",
            "Saleable Area",
            "Sale Rate",
            "Revenue",
            "Extras",
            "Profit",
            "Interest on Land for 2 Years",
            "Profit After Interest",
        ]
        if row.get(key) is not None
    }
    return {
        "title": title or "Older investible deal",
        "asset_type": "land",
        "status": "lead",
        "source": "excel_investible_deals_older",
        "locality": street,
        "area_name": plot,
        "land_area": f"{row['Area of Plot']} Gaj" if row.get("Area of Plot") else None,
        "owner_name": row.get("Owner"),
        "asking_price": row.get("Land Cost"),
        "expected_price": row.get("Revenue"),
        "bottleneck_notes": row.get("Comments"),
        "deal_financials": financials,
        "raw_source": row,
    }


def prune_none(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if value not in (None, "", [])}


def insert_queue_item(conn: sqlite3.Connection, source: str, source_uid_value: str, title: str, payload: dict[str, Any], now: str) -> bool:
    try:
        conn.execute(
            """
            insert into approval_queue
            (source, source_uid, title, payload, status, created_by_source, created_at, updated_at)
            values (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                source,
                source_uid_value,
                title,
                json.dumps(prune_none(payload), ensure_ascii=False),
                "pending",
                "pearl_spytech_notes_workbook",
                now,
                now,
            ),
        )
        return True
    except sqlite3.IntegrityError:
        return False


def main() -> None:
    if len(sys.argv) < 3:
        raise SystemExit("Usage: import_pearl_spytech_notes.py WORKBOOK.xlsx DB.sqlite")

    workbook_path = Path(sys.argv[1])
    db_path = Path(sys.argv[2])
    uploads_dir = db_path.parent / "data" / "uploads"
    uploads_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(workbook_path, uploads_dir / workbook_path.name)

    sheets = parse_workbook(workbook_path)
    serial_docs, url_docs = attachment_index(sheets.get("Attachments Index", []))
    now = datetime.now(timezone.utc).isoformat()
    queued = skipped = 0

    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        insert into ingestion_logs
        (source, filename, status, total_rows, created_count, review_count, skipped_count, created_at, updated_at)
        values (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "excel_pearl_spytech_notes",
            workbook_path.name,
            "running",
            len(sheets.get("Property Notes", [])) + len(sheets.get("Investible Deals Older", [])),
            0,
            0,
            0,
            now,
            now,
        ),
    )
    log_id = conn.execute("select last_insert_rowid()").fetchone()[0]

    for index, row in enumerate(sheets.get("Property Notes", []), start=1):
        payload = property_payload(row, serial_docs, url_docs)
        uid = source_uid("excel_pearl_spytech_notes", row, f"property:{index}")
        if insert_queue_item(conn, "excel_pearl_spytech_notes", uid, payload["title"], payload, now):
            queued += 1
        else:
            skipped += 1

    for index, row in enumerate(sheets.get("Investible Deals Older", []), start=1):
        payload = older_deal_payload(row)
        uid = "excel_investible_deals_older:" + hashlib.sha256(
            json.dumps(row, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()[:24]
        if insert_queue_item(conn, "excel_investible_deals_older", uid, payload["title"], payload, now):
            queued += 1
        else:
            skipped += 1

    summary_payload = {
        "summary": sheets.get("Summary", []),
        "attachments_index_count": len(sheets.get("Attachments Index", [])),
        "property_notes_count": len(sheets.get("Property Notes", [])),
        "older_deals_count": len(sheets.get("Investible Deals Older", [])),
    }
    insert_queue_item(
        conn,
        "excel_pearl_spytech_summary",
        source_uid("excel_pearl_spytech_summary", {"Note Title": workbook_path.name}, "summary"),
        f"{workbook_path.stem} import summary",
        {"title": f"{workbook_path.stem} import summary", "source": "excel_pearl_spytech_summary", **summary_payload},
        now,
    )

    conn.execute(
        """
        update ingestion_logs
        set status='completed', review_count=?, skipped_count=?, updated_at=?
        where id=?
        """,
        (queued, skipped, now, log_id),
    )
    conn.commit()

    documents_count = 0
    for row in conn.execute(
        "select payload from approval_queue where source in ('excel_pearl_spytech_notes','excel_investible_deals_older')"
    ):
        documents_count += len(json.loads(row[0]).get("documents", []))

    print(
        json.dumps(
            {
                "workbook": str(workbook_path),
                "log_id": log_id,
                "property_notes": len(sheets.get("Property Notes", [])),
                "older_deals": len(sheets.get("Investible Deals Older", [])),
                "attachments_index_rows": len(sheets.get("Attachments Index", [])),
                "queued_count": queued,
                "skipped_duplicates": skipped,
                "documents_or_links_attached": documents_count,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
