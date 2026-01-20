from pydantic import BaseModel
from typing import Optional, List

class ReportType(BaseModel):
    periodicReport: Optional[bool] = False
    changeInTreatmentPlan: Optional[bool] = False
    releaseFromCare: Optional[bool] = False
    changeInWorkStatus: Optional[bool] = False
    needForReferral: Optional[bool] = False
    responseToRequest: Optional[bool] = False
    changeInPatientCondition: Optional[bool] = False
    needForSurgery: Optional[bool] = False
    requestForAuthorization: Optional[bool] = False
    other: Optional[bool] = False
    otherText: Optional[str] = None

class Patient(BaseModel):
    lastName: Optional[str] = None
    firstName: Optional[str] = None
    middleInitial: Optional[str] = None
    streetAddress: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    zipCode: Optional[str] = None
    sex: Optional[str] = None
    occupation: Optional[str] = None
    phoneNumber: Optional[str] = None
    dateOfBirth: Optional[str] = None
    claimsAdministrator: Optional[str] = None
    dateOfInjury: Optional[str] = None

class ClaimsAdministrator(BaseModel):
    name: Optional[str] = None
    claimNumber: Optional[str] = None
    streetAddress: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    zipCode: Optional[str] = None
    phoneNumber: Optional[str] = None
    faxNumber: Optional[str] = None
    employerName: Optional[str] = None
    employerPhoneNumber: Optional[str] = None

class Diagnosis(BaseModel):
    diagnosis: Optional[str] = None
    icd10: Optional[str] = None

class WorkStatus(BaseModel):
    remainOffWorkUntil: Optional[str] = None
    returnToModifiedWorkOn: Optional[str] = None
    limitationsRestrictions: Optional[str] = None
    returnToFullDutyOn: Optional[str] = None

class Physician(BaseModel):
    signature: Optional[str] = None
    dateOfExam: Optional[str] = None
    executedAt: Optional[str] = None
    date: Optional[str] = None
    physicianName: Optional[str] = None
    specialty: Optional[str] = None
    address: Optional[str] = None
    phoneNumber: Optional[str] = None
    californiaLicenseNumber: Optional[str] = None

class PR2Form(BaseModel):
    reportType: ReportType
    patient: Patient
    claimsAdministrator: ClaimsAdministrator
    subjectiveComplaints: Optional[str] = None
    objectiveFindings: Optional[str] = None
    diagnoses: Optional[List[Diagnosis]] = []
    treatmentPlan: Optional[str] = None
    workStatus: WorkStatus
    physician: Physician
    soap_id: Optional[str] = None
    transcription_id: Optional[str] = None
