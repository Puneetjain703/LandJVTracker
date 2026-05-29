from __future__ import annotations

import json

from backend.app.db import SessionLocal, create_all
from backend.app.services.source_sync import sync_all_sources


if __name__ == "__main__":
    create_all()
    with SessionLocal() as db:
        result = sync_all_sources(db)
    print(json.dumps(result, indent=2, default=str))
