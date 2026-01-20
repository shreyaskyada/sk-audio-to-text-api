from pydantic import BaseModel
from typing import Optional, Any, Dict

class ClientLogRequest(BaseModel):
    level: str
    message: str
    stack: Optional[str] = None
    url: Optional[str] = None
    userAgent: Optional[str] = None
    context: Optional[Dict[str, Any]] = None
