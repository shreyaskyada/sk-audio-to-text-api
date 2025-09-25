from sqlalchemy import Column, Integer, String, Text, DateTime, Float, Boolean, JSON
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.sql import func
from datetime import datetime

Base = declarative_base()

class User(Base):
    __tablename__ = "users"
    
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True)
    hashed_password = Column(String)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

class Transcription(Base):
    __tablename__ = "transcriptions"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, index=True)
    filename = Column(String)
    file_size = Column(Integer)
    transcription_text = Column(Text)
    confidence_score = Column(Float)
    processing_time = Column(Float)
    model_used = Column(String)
    language_detected = Column(String)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

class TranscriptionFeedback(Base):
    __tablename__ = "transcription_feedback"
    
    id = Column(Integer, primary_key=True, index=True)
    transcription_id = Column(Integer, index=True)
    user_id = Column(Integer, index=True)
    
    # Rating (1-5 stars)
    overall_rating = Column(Integer)  # 1-5
    
    # Specific feedback categories
    accuracy_rating = Column(Integer)  # 1-5
    medical_terminology_rating = Column(Integer)  # 1-5
    punctuation_rating = Column(Integer)  # 1-5
    speed_rating = Column(Integer)  # 1-5
    
    # Text feedback
    feedback_text = Column(Text)
    corrections_needed = Column(Text)  # What should be corrected
    
    # Technical feedback
    issues_found = Column(JSON)  # List of issues like ["missing punctuation", "wrong medical term"]
    suggestions = Column(Text)  # Suggestions for improvement
    
    # Metadata
    feedback_type = Column(String, default="user")  # user, admin, system
    created_at = Column(DateTime(timezone=True), server_default=func.now())

class ModelImprovement(Base):
    __tablename__ = "model_improvements"
    
    id = Column(Integer, primary_key=True, index=True)
    feedback_id = Column(Integer, index=True)
    
    # Improvement tracking
    issue_type = Column(String)  # medical_terminology, punctuation, accuracy, etc.
    current_prompt = Column(Text)
    suggested_prompt = Column(Text)
    status = Column(String, default="pending")  # pending, implemented, rejected
    
    # Implementation tracking
    implemented_at = Column(DateTime(timezone=True))
    improvement_notes = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())