# Work Status Extraction API - Documentation

## Overview

This document describes the new Work Status Extraction API that extracts work status data from PR1 forms and formats it according to the Work Status Form API structure.

---

## New Endpoints

### 1. `/pr1/extract-work-status` (POST)

Extracts work status data from PR1 in the Work Status Form format.

**Request Body:**
Same as `/pr1/generate` endpoint - uses `PR1GenerateRequest` model:

```json
{
  "use_latest_intake": true,
  "use_latest_followup": true,
  "soap_id": "507f1f77bcf86cd799439013",
  "flags": {
    "progress_report": true
  }
}
```

**Response:**

```json
{
    "status": "success",
    "work_status_data": {
        "employeeInfo": {
            "employeeName": "John Doe",
            "claimNumber": "CLM-12345",
            "dateOfInjury": "2024-01-15",
            "dateOfEvaluation": "2024-03-20",
            "bodyPartsInjured": "Lower back, right shoulder",
            "nextFollowUpAppointment": "2024-04-15"
        },
        "workStatus": {
            "status": "modifiedDuty",
            "fullDutyEffectiveDate": "",
            "modifiedDutyFrom": "2024-03-20",
            "modifiedDutyTo": "2024-04-20",
            "offWorkFrom": "",
            "offWorkTo": "",
            "permanentStationaryDate": ""
        },
        "functionalRestrictions": {
            "liftingPushingPulling": {
                "noLiftingOver": true,
                "weightLimit": "25",
                "customWeight": ""
            },
            "upperExtremity": { ... },
            "lowerExtremity": { ... },
            "spinalTrunk": { ... },
            "positionTolerance": { ... },
            "handFineMotor": { ... },
            "workplaceConditions": { ... },
            "otherRestrictions": "..."
        },
        "providerInfo": {
            "providerName": "Dr. Jane Smith",
            "clinic": "Pilot Clinic",
            "phone": "(555) 123-4567",
            "signature": "Dr. Jane Smith",
            "date": "2024-03-20"
        }
    },
    "metadata": {
        "intake_source": "latest",
        "followup_source": "latest",
        "soap_source": "id",
        "intake_id": "...",
        "followup_id": "...",
        "soap_id": "..."
    }
}
```

---

### 2. `/pr1/extract-work-status-from-soap` (POST)

Convenience endpoint that extracts work status from a SOAP note by ID.

**Request (multipart/form-data):**

- `soap_id`: MongoDB ObjectId string (required)
- `use_latest_intake`: boolean (default: false)
- `use_latest_followup`: boolean (default: false)
- `flags`: JSON string (optional)

**Response:**
Same format as `/pr1/extract-work-status`

---

## Implementation Details

### Key Functions

#### 1. `extract_work_status_format()`

Main function that extracts and formats work status data from PR1 payload.

**Parameters:**

- `pr1_payload`: Complete PR1 payload from `build_pr1_payload()`
- `intake_doc`: Optional intake document
- `soap_doc`: Optional SOAP document

**Returns:**
Complete work status data structure matching the Work Status Form API format.

---

#### 2. `map_pr1_restrictions_to_new_format()`

Maps PR1 restrictions format to the new functional restrictions structure.

**Mapping Logic:**

- `liftCarryPounds` → `liftingPushingPulling.weightLimit` or `customWeight`
- `standing` → `positionTolerance.standingLimit` or `standingCustomMin`
- `sitting` → `positionTolerance.sittingLimit` or `sittingCustomMin`
- `walking` → `lowerExtremity.walkingLimit` or `walkingCustomMin`
- `kneeling` → `lowerExtremity.noRepetitiveKneeling`
- `forwardBending` → `spinalTrunk.noRepetitiveBending`
- `twisting` → `spinalTrunk.noRepetitiveTwisting`
- `climbing` → `lowerExtremity.noClimbingStairs`
- `graspingRight/Left` → `upperExtremity.noRepetitiveGripping*`
- `pushingPullingRight/Left` → `upperExtremity.useLimited*`
- `keyboarding` → `handFineMotor.productiveUse*`

---

#### 3. `to_yyyy_mm_dd()`

Converts date strings to YYYY-MM-DD format (ISO 8601).

**Supported Input Formats:**

- MM/DD/YYYY
- YYYY-MM-DD
- DD/MM/YYYY
- MM-DD-YYYY
- YYYY/MM/DD
- DD-MM-YYYY

---

#### 4. `extract_body_parts_from_diagnoses()`

Extracts body parts injured from diagnoses or assessment text.

**Sources:**

- SOAP document diagnoses list
- Assessment/discussion_assessment text
- Section B diagnoses (primary, secondary, additional)

---

## Work Status Mapping

### From PR1 to New Format

| PR1 Field                             | New Format Value                            |
| ------------------------------------- | ------------------------------------------- |
| `returnToFullDuty = true`             | `workStatus.status = "fullDuty"`            |
| `returnToWorkWithRestrictions = true` | `workStatus.status = "modifiedDuty"`        |
| `unableToReturnToWork = true`         | `workStatus.status = "offWork"`             |
| MMI date exists                       | `workStatus.status = "permanentStationary"` |

**Date Mapping:**

- `returnToFullDutyDate` → `workStatus.fullDutyEffectiveDate`
- Evaluation date → `workStatus.modifiedDutyFrom`
- `returnToFullDutyDate` → `workStatus.modifiedDutyTo` (if modified duty)
- `unableToReturnStartDate` → `workStatus.offWorkFrom`
- `unableToReturnEndDate` → `workStatus.offWorkTo`
- `mmi_date` → `workStatus.permanentStationaryDate`

---

## Employee Information Mapping

| PR1 Source                                                  | New Format Field                       |
| ----------------------------------------------------------- | -------------------------------------- |
| `header_admin.patient_name`                                 | `employeeInfo.employeeName`            |
| `header_admin.claim_number`                                 | `employeeInfo.claimNumber`             |
| `header_admin.date_of_injury`                               | `employeeInfo.dateOfInjury`            |
| `header_admin.date_of_first_examination`                    | `employeeInfo.dateOfEvaluation`        |
| Extracted from diagnoses                                    | `employeeInfo.bodyPartsInjured`        |
| `soap_doc.next_visit_date` or `patientStatus.nextVisitDate` | `employeeInfo.nextFollowUpAppointment` |

---

## Provider Information Mapping

| PR1 Source                                                                | New Format Field            |
| ------------------------------------------------------------------------- | --------------------------- |
| `header_admin.physician.physician_name`                                   | `providerInfo.providerName` |
| `header_admin.physician.practice_name`                                    | `providerInfo.clinic`       |
| `header_admin.physician.telephone`                                        | `providerInfo.phone`        |
| `header_admin.physician.physician_name`                                   | `providerInfo.signature`    |
| `page2_signature_and_included_sections.signature_date` or evaluation date | `providerInfo.date`         |

---

## Functional Restrictions Mapping

### Lifting/Pushing/Pulling

- **PR1:** `restrictions.liftCarryPounds`
- **New Format:** `functionalRestrictions.liftingPushingPulling`
  - If value is "5", "10", "15", or "25" → `weightLimit = value`
  - Otherwise → `weightLimit = "custom"` and `customWeight = value`

### Position Tolerance

- **PR1:** `restrictions.standing`
- **New Format:** `functionalRestrictions.positionTolerance.standingLimit`

  - If contains "2" → `"2hrs"`
  - If contains "4" → `"4hrs"`
  - Otherwise → `"custom"` with `standingCustomMin = value`

- **PR1:** `restrictions.sitting`
- **New Format:** Similar logic for `sittingLimit`

### Upper Extremity

- **PR1:** `restrictions.graspingRight/Left = false`
- **New Format:** `functionalRestrictions.upperExtremity.noRepetitiveGripping = true`

- **PR1:** `restrictions.pushingPullingRight/Left = false`
- **New Format:** `functionalRestrictions.upperExtremity.useLimited = true`

### Lower Extremity

- **PR1:** `restrictions.walking`
- **New Format:** `functionalRestrictions.lowerExtremity.walkingLimit`

- **PR1:** `restrictions.kneeling`
- **New Format:** `functionalRestrictions.lowerExtremity.noRepetitiveKneeling = true`

- **PR1:** `restrictions.climbing`
- **New Format:** `functionalRestrictions.lowerExtremity.noClimbingStairs = true`

### Spinal/Trunk

- **PR1:** `restrictions.forwardBending`
- **New Format:** `functionalRestrictions.spinalTrunk.noRepetitiveBending = true`

- **PR1:** `restrictions.twisting`
- **New Format:** `functionalRestrictions.spinalTrunk.noRepetitiveTwisting = true`
  - If text contains "neck" or "cervical" → `twistingNeck = true`
  - Otherwise → `twistingWaist = true`

---

## Usage Examples

### Example 1: Extract from Latest Data

```python
import requests

response = requests.post(
    "http://localhost:8000/api/pr1/extract-work-status",
    json={
        "use_latest_intake": True,
        "use_latest_followup": True,
        "soap_id": "507f1f77bcf86cd799439013",
        "flags": {
            "progress_report": True
        }
    }
)

work_status_data = response.json()["work_status_data"]
```

### Example 2: Extract from SOAP Note ID

```python
import requests

files = {
    "soap_id": "507f1f77bcf86cd799439013",
    "use_latest_intake": "true",
    "use_latest_followup": "true"
}

response = requests.post(
    "http://localhost:8000/api/pr1/extract-work-status-from-soap",
    data=files
)

work_status_data = response.json()["work_status_data"]
```

---

## Integration with PR1 Generation

The work status extraction integrates seamlessly with the existing PR1 generation workflow:

1. **Generate PR1** using `/pr1/generate` or `/pr1/generate-from-soap`
2. **Extract Work Status** using `/pr1/extract-work-status` or `/pr1/extract-work-status-from-soap`

Both endpoints use the same data fetching logic, ensuring consistency between PR1 generation and work status extraction.

---

## Error Handling

All endpoints return standard HTTP status codes:

- **200**: Success
- **400**: Bad Request (invalid parameters)
- **404**: Document not found
- **500**: Internal server error

Error responses include detailed error messages:

```json
{
  "detail": "SOAP note not found with ID: 507f1f77bcf86cd799439013"
}
```

---

## Notes

1. **Date Formats**: All dates are converted from PR1 format (MM/DD/YYYY) to Work Status Form format (YYYY-MM-DD)

2. **Body Parts Extraction**: Body parts are extracted from diagnoses using keyword matching. Common patterns like "lower back", "right shoulder", etc. are recognized.

3. **Restrictions Mapping**: The mapping is intelligent and tries to parse text-based restrictions into structured fields. Some restrictions may require manual review.

4. **Default Values**: Fields that cannot be determined from PR1 data will be set to empty strings or false, matching the Work Status Form structure.

5. **Backwards Compatibility**: The new endpoints work alongside existing PR1 generation endpoints and do not modify any existing functionality.

---

## Future Enhancements

Potential improvements:

1. Enhanced body part extraction using NLP
2. More sophisticated restrictions parsing
3. Support for additional restriction categories
4. Validation and verification of extracted data
5. Bulk extraction from multiple PR1 forms
