from pydantic import BaseModel, Field
from typing import Optional, Dict, Any
from datetime import datetime

class APILog(BaseModel):
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    method: str
    url: str
    status_code: Optional[int] = None
    process_time_ms: Optional[float] = None
    client_host: Optional[str] = None
    user_agent: Optional[str] = None
    error_message: Optional[str] = None
    stack_trace: Optional[str] = None
    request_body: Optional[Dict[str, Any]] = None
    response_body: Optional[Any] = None
