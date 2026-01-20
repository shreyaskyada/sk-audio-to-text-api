from pydantic import BaseModel
from typing import Optional

class PatientSignatureRequest(BaseModel):
    soap_id: str
    signature_data: str
    patient_name: Optional[str] = None
    form_type: Optional[str] = "PR1"

class PatientSignatureResponse(BaseModel):
    status: str
    message: str
    signature_id: str
