from __future__ import annotations

import re
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload

from backend.app import models
from backend.app.config import get_settings
from backend.app.services.assets import asset_to_dict


def _serialize(row: dict[str, Any]) -> dict[str, Any]:
    return {key: str(value) if value is not None and not isinstance(value, (int, float, str, bool)) else value for key, value in row.items()}


def _candidate_assets(db: Session, question: str) -> list[dict[str, Any]]:
    tokens = [token for token in re.findall(r"[A-Za-z0-9_]+", question.lower()) if len(token) > 2]
    stmt = select(models.Asset).options(selectinload(models.Asset.owner), selectinload(models.Asset.broker))
    if tokens:
        clauses = []
        for token in tokens[:8]:
            like = f"%{token}%"
            clauses.extend(
                [
                    models.Asset.title.ilike(like),
                    models.Asset.locality.ilike(like),
                    models.Asset.district.ilike(like),
                    models.Asset.tehsil.ilike(like),
                    models.Asset.source.ilike(like),
                    models.Asset.status.ilike(like),
                    models.Asset.bottleneck_notes.ilike(like),
                ]
            )
        stmt = stmt.where(or_(*clauses))
    stmt = stmt.order_by(models.Asset.updated_at.desc()).limit(25)
    return [_serialize(asset_to_dict(asset)) for asset in db.scalars(stmt)]


def _fallback_answer(question: str, rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "I could not find matching assets yet. Try asking with a locality, district, broker, owner, status, or bottleneck keyword."
    if "pending" in question.lower() or "approval" in question.lower():
        pending = [row for row in rows if row.get("approval_status") == "pending"]
        return f"Found {len(pending)} matching pending/review-related assets in the current result set. Source rows are attached below."
    top = rows[:5]
    lines = [f"I found {len(rows)} relevant asset rows. Top matches:"]
    for row in top:
        lines.append(
            f"- {row.get('asset_code') or row.get('id')}: {row.get('title')} | {row.get('asset_type')} | "
            f"{row.get('locality') or '-'}, {row.get('district') or '-'} | status {row.get('status')}"
        )
    return "\n".join(lines)


def answer_question(db: Session, question: str, asked_by: str | None = None) -> dict[str, Any]:
    rows = _candidate_assets(db, question)
    settings = get_settings()
    answer: str
    if settings.openai_api_key and rows:
        from openai import OpenAI

        client = OpenAI(api_key=settings.openai_api_key)
        response = client.chat.completions.create(
            model=settings.openai_model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are an internal real-estate intelligence assistant. Answer only from the provided "
                        "asset rows. Be concise, mention uncertainty, and include relevant asset codes or ids."
                    ),
                },
                {"role": "user", "content": f"Question: {question}\n\nRows: {rows}"},
            ],
            temperature=0.2,
        )
        answer = response.choices[0].message.content or "No answer generated."
    else:
        answer = _fallback_answer(question, rows)

    db.add(models.AiQueryLog(question=question, answer=answer, source_rows=rows, asked_by=asked_by))
    db.commit()
    return {"answer": answer, "source_rows": rows[:10]}
