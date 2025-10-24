from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from pymongo import MongoClient
import os
from dotenv import load_dotenv
import logging

load_dotenv()

logger = logging.getLogger(__name__)

class MongoDB:
    client: AsyncIOMotorClient = None
    database: AsyncIOMotorDatabase = None

mongodb = MongoDB()

async def connect_to_mongo():
    """Create database connection"""
    try:
        # Get MongoDB connection string from environment
        mongodb_url = os.getenv("MONGODB_URL", "mongodb://localhost:27017")
        database_name = os.getenv("MONGODB_DATABASE", "audio_transcription")
        
        logger.info(f"Attempting to connect to MongoDB: {database_name}")
        logger.info(f"MongoDB URL: {mongodb_url[:mongodb_url.find('@')] if '@' in mongodb_url else 'Local MongoDB'}...")
        
        # Create async client
        mongodb.client = AsyncIOMotorClient(mongodb_url)
        mongodb.database = mongodb.client[database_name]
        
        # Test connection
        await mongodb.client.admin.command('ping')
        logger.info(f"Connected to MongoDB database: {database_name}")
        
    except Exception as e:
        logger.error(f"Failed to connect to MongoDB: {str(e)}")
        raise

async def close_mongo_connection():
    """Close database connection"""
    if mongodb.client:
        mongodb.client.close()
        logger.info("Disconnected from MongoDB")

def get_database() -> AsyncIOMotorDatabase:
    """Get database instance"""
    return mongodb.database

# Collection names
FEEDBACK_COLLECTION = "feedback"
AUDIT_LOGS_COLLECTION = "audit_logs"

