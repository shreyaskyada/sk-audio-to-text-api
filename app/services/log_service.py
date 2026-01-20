from datetime import datetime
from typing import Dict, Any
from app.database import get_database

async def log_entry(data: Dict[str, Any]) -> str:
    db = get_database()
    if db is None: return "skipped"
    data["timestamp"] = datetime.utcnow()
    res = await db.api_logs.insert_one(data)
    return str(res.inserted_id)
