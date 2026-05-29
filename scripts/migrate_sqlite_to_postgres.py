from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, delete, func, select, text
from sqlalchemy.engine import Engine

from backend.app.db import Base
from backend.app import models  # noqa: F401


def normalize_value(value: Any) -> Any:
    if isinstance(value, str):
        for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
            try:
                return datetime.strptime(value, fmt)
            except ValueError:
                pass
    return value


def table_count(engine: Engine, table_name: str) -> int:
    with engine.connect() as conn:
        return int(conn.execute(text(f"select count(*) from {table_name}")).scalar_one())


def reset_sequences(engine: Engine) -> None:
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if "id" not in table.c:
                continue
            conn.execute(
                text(
                    """
                    select setval(
                        pg_get_serial_sequence(:table_name, 'id'),
                        coalesce((select max(id) from {table_name}), 1),
                        (select count(*) from {table_name}) > 0
                    )
                    """.format(table_name=table.name)
                ),
                {"table_name": table.name},
            )


def migrate(sqlite_path: Path, postgres_url: str, replace: bool = False) -> None:
    sqlite_engine = create_engine(f"sqlite:///{sqlite_path}")
    postgres_engine = create_engine(postgres_url, pool_pre_ping=True)

    Base.metadata.create_all(bind=postgres_engine)

    with postgres_engine.begin() as pg_conn:
        if replace:
            for table in reversed(Base.metadata.sorted_tables):
                pg_conn.execute(delete(table))
        else:
            populated = [table.name for table in Base.metadata.sorted_tables if table_count(postgres_engine, table.name)]
            if populated:
                names = ", ".join(populated[:8])
                suffix = "..." if len(populated) > 8 else ""
                raise SystemExit(
                    f"Destination is not empty ({names}{suffix}). Re-run with --replace if you want to overwrite it."
                )

    total = 0
    with sqlite_engine.connect() as sqlite_conn, postgres_engine.begin() as pg_conn:
        for table in Base.metadata.sorted_tables:
            rows = sqlite_conn.execute(select(table)).mappings().all()
            if not rows:
                print(f"{table.name}: 0")
                continue
            payloads = [
                {column.name: normalize_value(row[column.name]) for column in table.columns}
                for row in rows
            ]
            pg_conn.execute(table.insert(), payloads)
            total += len(payloads)
            print(f"{table.name}: {len(payloads)}")

    reset_sequences(postgres_engine)
    print(f"Done. Migrated {total} rows.")


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("Usage: migrate_sqlite_to_postgres.py SQLITE_PATH [--replace]")
    sqlite_path = Path(sys.argv[1]).expanduser().resolve()
    postgres_url = os.environ.get("DATABASE_URL")
    if not postgres_url:
        raise SystemExit("DATABASE_URL is required")
    migrate(sqlite_path, postgres_url, replace="--replace" in sys.argv[2:])


if __name__ == "__main__":
    main()
