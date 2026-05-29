from backend.app.db import SessionLocal
from backend.app.models import NotionSyncLog, ApprovalQueue
from sqlalchemy import select

def check():
    with SessionLocal() as db:
        print("--- RECENT NOTION SYNC LOGS ---")
        logs = db.scalars(select(NotionSyncLog).order_by(NotionSyncLog.created_at.desc()).limit(10)).all()
        for log in logs:
            print(f"ID: {log.id} | Source: {log.source_name} | Status: {log.status} | Fetched: {log.fetched_count} | Queued: {log.queued_count} | Error: {log.error_message}")
        
        print("\n--- RECENT APPROVAL QUEUE ITEMS (NOTION) ---")
        items = db.scalars(select(ApprovalQueue).where(ApprovalQueue.source.like('%notion%')).order_by(ApprovalQueue.created_at.desc()).limit(5)).all()
        for item in items:
            print(f"ID: {item.id} | Source: {item.source} | Title: {item.title} | Status: {item.status}")

if __name__ == "__main__":
    check()
