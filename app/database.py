import logging
from motor.motor_asyncio import AsyncIOMotorClient
from typing import Optional
from app.config import settings

logger = logging.getLogger(__name__)

class Database:
    client: Optional[AsyncIOMotorClient] = None
    db = None

db_connection = Database()

async def connect_to_mongo():
    """Connect to MongoDB"""
    try:
        db_connection.client = AsyncIOMotorClient(settings.MONGODB_URL)
        db_connection.db = db_connection.client[settings.MONGODB_DB_NAME]
        
        # Test connection
        await db_connection.client.admin.command('ping')
        
        # Log masked URL
        masked_url = settings.MONGODB_URL
        if "@" in masked_url:
            try:
                part1 = masked_url.split("@")[1]
                proto = masked_url.split("://")[0]
                masked_url = f"{proto}://***@{part1}"
            except:
                pass
        
        logger.info(f"✅ Connected to MongoDB: {settings.MONGODB_DB_NAME} at {masked_url}")
        return True
    except Exception as e:
        logger.error(f"❌ MongoDB connection failed: {e}")
        return False

async def close_mongo_connection():
    """Close MongoDB connection"""
    if db_connection.client:
        db_connection.client.close()
        logger.info("MongoDB connection closed")

def get_database():
    """Get MongoDB database instance"""
    return db_connection.db
