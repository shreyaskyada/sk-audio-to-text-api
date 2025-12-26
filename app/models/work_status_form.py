from pydantic import BaseModel, Field
from typing import Optional


class EmployeeInfo(BaseModel):
    employeeName: Optional[str] = None
    claimNumber: Optional[str] = None
    dateOfInjury: Optional[str] = None
    dateOfEvaluation: Optional[str] = None
    bodyPartsInjured: Optional[str] = None
    nextFollowUpAppointment: Optional[str] = None


class WorkStatus(BaseModel):
    status: Optional[str] = None  # "fullDuty", "modifiedDuty", "offWork", "permanentStationary"
    fullDutyEffectiveDate: Optional[str] = None
    modifiedDutyFrom: Optional[str] = None
    modifiedDutyTo: Optional[str] = None
    offWorkFrom: Optional[str] = None
    offWorkTo: Optional[str] = None
    permanentStationaryDate: Optional[str] = None


class LiftingPushingPulling(BaseModel):
    noLiftingOver: Optional[bool] = False
    weightLimit: Optional[str] = None  # "5", "10", "15", "25", or "custom"
    customWeight: Optional[str] = None


class UpperExtremity(BaseModel):
    noAboveShoulderReaching: Optional[bool] = False
    aboveShoulderRight: Optional[bool] = False
    aboveShoulderLeft: Optional[bool] = False
    useLimited: Optional[bool] = False
    useLimitedSide: Optional[str] = None  # "right", "left"
    useLimitedHours: Optional[str] = None
    noRepetitiveGripping: Optional[bool] = False
    noRepetitiveGrippingRight: Optional[bool] = False
    noRepetitiveGrippingLeft: Optional[bool] = False


class LowerExtremity(BaseModel):
    noRepetitiveKneeling: Optional[bool] = False
    walkingLimited: Optional[bool] = False
    walkingLimit: Optional[str] = None  # "2hrs", "4hrs", "custom", "other"
    walkingCustomMin: Optional[str] = None
    walkingOther: Optional[str] = None
    noClimbingStairs: Optional[bool] = False


class SpinalTrunk(BaseModel):
    noRepetitiveBending: Optional[bool] = False
    noRepetitiveTwisting: Optional[bool] = False
    twistingNeck: Optional[bool] = False
    twistingWaist: Optional[bool] = False


class PositionTolerance(BaseModel):
    alternateSittingStanding: Optional[bool] = False
    alternateInterval: Optional[str] = None  # "30min", "60min", "other"
    alternateOther: Optional[str] = None
    standingLimited: Optional[bool] = False
    standingLimit: Optional[str] = None  # "2hrs", "4hrs", "custom"
    standingCustomMin: Optional[str] = None
    sittingLimited: Optional[bool] = False
    sittingLimit: Optional[str] = None  # "2hrs", "4hrs", "custom"
    sittingCustomMin: Optional[str] = None


class HandFineMotor(BaseModel):
    productiveUseEnabled: Optional[bool] = False
    productiveUseMinutes: Optional[str] = None
    productiveUseRight: Optional[bool] = False
    productiveUseLeft: Optional[bool] = False


class WorkplaceConditions(BaseModel):
    noWorkingAtHeights: Optional[bool] = False
    noSafetySensitiveDuties: Optional[bool] = False


class FunctionalRestrictions(BaseModel):
    liftingPushingPulling: Optional[LiftingPushingPulling] = None
    upperExtremity: Optional[UpperExtremity] = None
    lowerExtremity: Optional[LowerExtremity] = None
    spinalTrunk: Optional[SpinalTrunk] = None
    positionTolerance: Optional[PositionTolerance] = None
    handFineMotor: Optional[HandFineMotor] = None
    workplaceConditions: Optional[WorkplaceConditions] = None
    otherRestrictions: Optional[str] = None


class ProviderInfo(BaseModel):
    providerName: Optional[str] = None
    clinic: Optional[str] = None
    phone: Optional[str] = None
    signature: Optional[str] = None
    date: Optional[str] = None


class WorkStatusForm(BaseModel):
    soap_id: Optional[str] = None
    employeeInfo: EmployeeInfo
    workStatus: WorkStatus
    functionalRestrictions: FunctionalRestrictions
    providerInfo: ProviderInfo

