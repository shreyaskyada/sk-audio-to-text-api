
from pydantic import BaseModel
from typing import Optional

class ClientLogRequest(BaseModel):
    """Request model for client-side logs"""
    level: str  # error, warning, info
    message: str
    component: Optional[str] = None
    stack_trace: Optional[str] = None
    url: Optional[str] = None
    user_agent: Optional[str] = None
    additional_data: Optional[dict] = None
