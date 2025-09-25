from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
import os
from dotenv import load_dotenv

load_dotenv()

# Database configuration - Force in-memory SQLite for Vercel deployment
# Check if we're running on Vercel (no persistent storage available)
if os.getenv("VERCEL"):
    DATABASE_URL = "sqlite:///:memory:"  # In-memory database for Vercel
else:
    DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./transcription.db")

# Create engine
if DATABASE_URL.startswith("sqlite"):
    engine = create_engine(
        DATABASE_URL, 
        connect_args={"check_same_thread": False}
    )
else:
    engine = create_engine(DATABASE_URL)

# Create session
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

def get_db():
    """Dependency to get database session"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

