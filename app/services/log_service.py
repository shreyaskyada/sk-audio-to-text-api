
import logging
from datetime import datetime
from typing import Optional
from app.mongodb import get_database

logger = logging.getLogger(__name__)

async def log_client_error_service(log_data: dict, ip_address: Optional[str] = None):
    """
    Log an error from the frontend/client to the database.
    """
    db = get_database()
    if db is None:
        logger.warning("Database not available for logging client error")
        return False
        
    try:
        log_entry = log_data.copy()
        log_entry["timestamp"] = datetime.utcnow()
        log_entry["source"] = "client"
        log_entry["ip_address"] = ip_address
        
        await db.api_logs.insert_one(log_entry)
        return True
    except Exception as e:
        logger.error(f"Failed to log client error: {e}")
        return False
