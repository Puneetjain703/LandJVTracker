from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET
from zipfile import ZipFile


NS = {"a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def col_index(cell_ref: str) -> int:
    letters = re.sub(r"[^A-Z]", "", cell_ref.upper())
    index = 0
    for letter in letters:
        index = index * 26 + (ord(letter) - ord("A") + 1)
    return index - 1


def clean(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        if value == "":
            return None
        try:
            number = float(value)
            return int(number) if number.is_integer() else number
        except ValueError:
            return value
    return value


def read_shared_strings(zip_file: ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in zip_file.namelist():
        return []
    root = ET.fromstring(zip_file.read("xl/sharedStrings.xml"))
    strings: list[str] = []
    for item in root.findall("a:si", NS):
        parts = [node.text or "" for node in item.findall(".//a:t", NS)]
        strings.append("".join(parts))
    return strings


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


def iter_sheet_rows(xlsx_path: Path) -> tuple[list[str], list[dict[str, Any]]]:
    with ZipFile(xlsx_path) as zip_file:
        shared_strings = read_shared_strings(zip_file)
        root = ET.fromstring(zip_file.read("xl/worksheets/sheet1.xml"))
    parsed_rows: list[list[Any]] = []
    for row in root.findall(".//a:sheetData/a:row", NS):
        values: list[Any] = []
        for cell in row.findall("a:c", NS):
            index = col_index(cell.attrib["r"])
            while len(values) <= index:
                values.append(None)
            values[index] = cell_value(cell, shared_strings)
        parsed_rows.append(values)
    if not parsed_rows:
        return [], []
    headers = [str(value).strip() if value not in (None, "") else f"Column {i + 1}" for i, value in enumerate(parsed_rows[0])]
    dict_rows = []
    for values in parsed_rows[1:]:
        dict_rows.append({headers[i] if i < len(headers) else f"Column {i + 1}": clean(value) for i, value in enumerate(values)})
    return headers, dict_rows


def is_blank(raw: dict[str, Any]) -> bool:
    for value in raw.values():
        if value in (None, ""):
            continue
        if isinstance(value, (int, float)) and value == 0:
            continue
        return False
    return True


def normalize_asset_type(purpose: Any) -> str:
    text = str(purpose or "").lower()
    if "jv" in text or "joint venture" in text:
        return "jv"
    if "resale" in text:
        return "resale_unit"
    if "rent" in text:
        return "rental"
    if "commercial" in text:
        return "commercial"
    return "land"


def parse_coordinates(raw: dict[str, Any]) -> tuple[float | None, float | None]:
    for key in ("Standard Coordinates2", "Standard Coordinates", "COORDINATES (IF AVAILABLE)"):
        value = raw.get(key)
        if not value:
            continue
        text = str(value).replace(" ", "")
        if "," not in text:
            continue
        left, right = text.split(",", 1)
        try:
            return float(left), float(right)
        except ValueError:
            continue
    return None, None


def row_uid(filename: str, row_number: int, raw: dict[str, Any]) -> str:
    digest = hashlib.sha256(f"{filename}:{row_number}:{raw}".encode("utf-8")).hexdigest()[:24]
    return f"excel:{digest}"


def build_payload(raw: dict[str, Any]) -> dict[str, Any]:
    latitude, longitude = parse_coordinates(raw)
    size = raw.get("Size (for calculation)") or raw.get("Acreage")
    unit = raw.get("Area Unit")
    land_area = f"{size} {unit}".strip() if size else None
    last_update = raw.get("LAST UPDATE")
    reference = raw.get("REFERENCE")
    notes = None
    if last_update and reference:
        notes = f"{last_update}\n\nHistory: {reference}"
    elif last_update or reference:
        notes = last_update or reference

    payload = {
        "title": raw.get("LOCATION"),
        "locality": raw.get("LOCATION"),
        "asset_type": normalize_asset_type(raw.get("PURPOSE")),
        "source": "excel",
        "approval_status": "pending",
        "land_area": land_area,
        "asking_price": raw.get("PRICE"),
        "bottleneck_notes": notes,
        "owner_name": raw.get("OWNER ") or raw.get("OWNER"),
        "broker_name": raw.get("BROKER"),
        "raw_source": raw,
    }
    if latitude is not None and longitude is not None:
        payload["latitude"] = latitude
        payload["longitude"] = longitude
        payload["google_maps_link"] = f"https://www.google.com/maps?q={latitude},{longitude}"
    if not payload["title"] or not payload["locality"]:
        payload["needs_manual_review"] = True
        payload["review_reason"] = "Missing title and/or location fields"
    return {key: value for key, value in payload.items() if value is not None}


def main() -> None:
    xlsx_path = Path(sys.argv[1])
    db_path = Path(sys.argv[2])
    headers, rows = iter_sheet_rows(xlsx_path)
    now = datetime.now(timezone.utc).isoformat()
    queued = skipped = incomplete = 0

    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        insert into ingestion_logs
        (source, filename, status, total_rows, created_count, review_count, skipped_count, created_at, updated_at)
        values (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ("excel", xlsx_path.name, "running", len(rows), 0, 0, 0, now, now),
    )
    log_id = conn.execute("select last_insert_rowid()").fetchone()[0]

    for row_number, raw in enumerate(rows, start=2):
        if is_blank(raw):
            skipped += 1
            continue
        payload = build_payload(raw)
        if payload.get("needs_manual_review"):
            incomplete += 1
        source_uid = row_uid(xlsx_path.name, row_number, raw)
        try:
            conn.execute(
                """
                insert into approval_queue
                (source, source_uid, title, payload, status, created_by_source, created_at, updated_at)
                values (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "excel",
                    source_uid,
                    payload.get("title") or f"Excel row {row_number}",
                    json.dumps(payload, ensure_ascii=False),
                    "pending",
                    "excel_import",
                    now,
                    now,
                ),
            )
            queued += 1
        except sqlite3.IntegrityError:
            skipped += 1

    conn.execute(
        """
        update ingestion_logs
        set status='completed', review_count=?, skipped_count=?, updated_at=?
        where id=?
        """,
        (queued, skipped, now, log_id),
    )
    conn.commit()
    print(
        json.dumps(
            {
                "headers": headers,
                "total_rows": len(rows),
                "queued_count": queued,
                "skipped_count": skipped,
                "incomplete_count": incomplete,
                "log_id": log_id,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

