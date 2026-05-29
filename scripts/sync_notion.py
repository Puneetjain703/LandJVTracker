from backend.app.db import SessionLocal, create_all
from backend.app.services.notion_sync import sync_notion_to_queue


if __name__ == "__main__":
    create_all()
    with SessionLocal() as db:
        result = sync_notion_to_queue(db)
    print(result)

