from pydantic import BaseModel, EmailStr, Field
from typing import Optional, List
from datetime import datetime

class UserBase(BaseModel):
    username: str = Field(..., min_length=3, max_length=50)
    email: EmailStr

class UserCreate(UserBase):
    password: str = Field(..., min_length=8)

class UserResponse(UserBase):
    id: str
    is_active: bool
    is_admin: bool
    created_at: datetime
    
    class Config:
        from_attributes = True

class UserLogin(BaseModel):
    username: str
    password: str

class Token(BaseModel):
    access_token: str
    token_type: str
    expires_in: int

class TranscriptionRequest(BaseModel):
    filename: Optional[str] = None
    language: Optional[str] = None

class TranscriptionResponse(BaseModel):
    transcription_id: str
    text: str
    confidence: float
    language: str
    duration: float
    created_at: datetime
    soap_note: Optional[str] = None
    
    class Config:
        from_attributes = True

class TranscriptionListItem(BaseModel):
    id: str
    filename: str
    language: str
    confidence: float
    duration: float
    status: str
    created_at: datetime
    
    class Config:
        from_attributes = True

class TranscriptionListResponse(BaseModel):
    transcriptions: List[TranscriptionListItem]
    total: int
    skip: int
    limit: int

class AuditLogResponse(BaseModel):
    id: str
    action: str
    resource_type: str
    resource_id: Optional[str]
    details: Optional[str]
    ip_address: Optional[str]
    success: bool
    created_at: datetime
    
    class Config:
        from_attributes = True

class ErrorResponse(BaseModel):
    detail: str
    error_code: Optional[str] = None
    timestamp: datetime = Field(default_factory=datetime.utcnow)

class HealthCheckResponse(BaseModel):
    status: str
    timestamp: datetime
    version: str = "1.0.0"

