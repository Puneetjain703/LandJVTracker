from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.db import SessionLocal, create_all
from backend.app.services.source_sync import sync_all_sources


if __name__ == "__main__":
    create_all()
    with SessionLocal() as db:
        result = sync_all_sources(db)
    print(json.dumps(result, indent=2, default=str))
