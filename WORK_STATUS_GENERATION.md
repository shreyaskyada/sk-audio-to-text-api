# Work Status Generation in PR1 - Detailed Analysis

## Overview

The work status generation in PR1 is handled in the `build_section_c()` function located in `app/api/pr1_generator.py`. This function uses a comprehensive, priority-based approach to extract work status information from multiple sources.

---

## Data Source Priority Order

The system checks for work status in the following priority order (stopping at the first successful match):

### Priority 1: Nested `work_status` Structure (SOAP Document)

**Location:** `soap_doc.get("work_status")` as a dictionary

**Fields Extracted:**

- `work_status.work_status` or `work_status.status` or `work_status.workStatus`
- `work_status.restrictions`
- `work_status.dates.duration`
- `work_status.medication_effects.affect_alertness`
- `work_status.medication_effects.description`

**Code Location:** Lines 1983-2009

---

### Priority 2: Flat `work_status` Field (SOAP Document)

**Location:** `soap_doc.get("work_status")` or `soap_doc.get("workStatus")` as string

**Code Location:** Lines 2011-2016

---

### Priority 3: `page7` Structure (SOAP Document)

**Location:** `soap_doc.get("page7").workStatus` or `soap_doc.get("page7").work_status`

**Code Location:** Lines 2018-2026

---

### Priority 4: `plan` Section (SOAP Document)

**Location:** `soap_doc.get("plan").work_status` or `soap_doc.get("plan").workStatus`

**Code Location:** Lines 2030-2038

---

### Priority 5: `clinical_information.plan` Nested Structure (SOAP Document)

**Location:** `soap_doc.get("clinical_information").get("plan").work_status`

**Code Location:** Lines 2040-2049

---

### Priority 6: Text Extraction from `formatted_soap_note` (SOAP Document)

**Two-step process:**

#### Step 6a: Regex Pattern Matching

- Searches for patterns like:
  - `WORK STATUS: ...`
  - `Work Status: ...`
  - `RTW status: ...`
  - `Full Duty`, `Modified Duty`, `TTD`, `Temporary Total`

**Code Location:** Lines 2051-2071

#### Step 6b: GPT Extraction (if regex fails)

- Uses `extract_work_status_from_text()` function
- Calls GPT-4o API to extract structured work status from text
- Returns JSON with work_status, restrictions, restrictions_details, dates, etc.

**Code Location:** Lines 2073-2086

---

### Priority 6b: Transcription Text Extraction (SOAP Document)

**Location:** `soap_doc.get("transcription")` or `soap_doc.get("corrected_transcription")`

- Uses GPT extraction via `extract_work_status_from_text()`

**Code Location:** Lines 2088-2102

---

### Priority 7: Intake Form (Fallback)

**Location:** `intake_doc.get("section_e").work_status`

**Code Location:** Lines 2104-2112

---

### Priority 8: Follow-up Form (Final Fallback)

**Location:** `follow_doc.get("section_b").work_status_perception` or `follow_doc.get("section_b").workStatusPerception`

**Code Location:** Lines 2170-2176

---

### Default (If Nothing Found)

**Default Value:** `"[Not documented]"`

**Code Location:** Lines 2178-2183

---

## GPT Extraction Process

### Function: `extract_work_status_from_text()`

**Location:** Lines 1744-1851

**Process:**

1. Validates OpenAI API key is available
2. Creates GPT-4o client
3. Sends system prompt with detailed extraction instructions
4. Limits text to 5000 characters to avoid token limits
5. Calls GPT API with temperature=0.1 for consistency
6. Returns structured JSON with:
   - `work_status`: Full Duty/Modified Duty/TTD/etc.
   - `restrictions`: Text description
   - `restrictions_details`: Detailed structured restrictions object
   - Date fields: `return_full_duty_date`, `unable_to_return_start_date`, etc.

**Model Used:** `gpt-4o`
**Response Format:** JSON object
**Max Tokens:** 1500

---

## Restrictions Parsing

### Function: `parse_restrictions_from_text()`

**Location:** Lines 1854-1952

**Purpose:** Parses free-text restrictions into structured format

**Extracted Fields:**

- `liftCarryPounds`: Weight limit
- `liftCarryHeight`: Height restriction
- `standing`: Standing tolerance/restrictions
- `walking`: Walking tolerance/restrictions
- `sitting`: Sitting tolerance/restrictions
- `climbing`, `forwardBending`, `kneeling`, `crawling`, `twisting`, `keyboarding`: Activity restrictions
- `graspingRight`, `graspingLeft`, `graspingBilateral`: Boolean flags
- `graspingHours`: Hours limit for grasping
- `pushingPullingRight`, `pushingPullingLeft`, `pushingPullingBilateral`: Boolean flags
- `pushingPullingHours`: Hours limit for pushing/pulling

**Key Rule:** Only extracts EXPLICITLY mentioned restrictions (no inference)

---

## Field Fetching Mechanism

### Document Fetching Functions

#### 1. `fetch_if_needed()`

**Location:** Lines 139-166

**Purpose:** Fetches document from MongoDB if ID provided, otherwise uses payload object

**Logic:**

- If `payload_obj` provided → use it directly
- If `oid` provided → fetch from MongoDB using `ObjectId(oid)`
- Returns `None` if neither provided

**Collections Used:**

- `COLL_INTAKE = "intake_forms"`
- `COLL_FOLLOWUP = "followup_intake_forms"`
- `COLL_SOAP = "soap_notes"`

---

#### 2. `fetch_latest_document()`

**Location:** Lines 169-197

**Purpose:** Fetches the latest document from a MongoDB collection

**Logic:**

- Sorts by `created_at` descending
- Returns the first document (most recent)
- Converts `ObjectId` to string for JSON serialization

---

## Document Fetching Priority in API Endpoints

### `/pr1/generate` Endpoint

**Location:** Lines 2475-2700

**Fetching Priority:**

1. **Embedded object** (provided in payload)
2. **Specific ID** (`intake_id`, `followup_id`, `soap_id`)
3. **Latest document** (if `use_latest_intake` or `use_latest_followup` is True)
4. **None** (if no source provided)

**Code Flow:**

```python
# Intake form
if payload.intake:
    intake_doc = payload.intake.model_dump()
elif payload.intake_id:
    intake_doc = await fetch_if_needed(None, payload.intake_id, COLL_INTAKE)
elif payload.use_latest_intake:
    intake_doc = await fetch_latest_document(COLL_INTAKE)

# Follow-up form (same pattern)
# SOAP note (same pattern, but no automatic latest fetching)
```

---

### `/pr1/generate-from-soap` Endpoint

**Location:** Lines 2913-3200

**Special Features:**

- Always fetches SOAP note by `soap_id` (required parameter)
- Optionally fetches latest intake/follow-up forms
- Can merge existing structured SOAP fields with GPT-extracted data
- Processes `formatted_soap_note` field through GPT conversion

---

## Work Status Normalization

**Location:** Lines 2185-2206

After extracting work status, the system normalizes it to determine boolean flags:

```python
work_status_lower = str(work_status).lower()
return_to_full_duty = "full duty" in work_status_lower or "full" in work_status_lower
unable_to_return_to_work = "ttd" in work_status_lower or "temporary total" in work_status_lower
return_to_work_with_restrictions = (
    "modified" in work_status_lower or
    "restriction" in work_status_lower or
    has_restrictions_text or
    has_detailed_restrictions
)
```

---

## Restrictions Object Building

**Location:** Lines 2245-2313

**Process:**

1. Creates default restrictions object with all fields set to empty strings or False
2. Merges with `detailed_restrictions` if available (from page7, GPT extraction, or parsed text)
3. Prioritizes non-empty values
4. Handles both boolean and string field types
5. Extracts `otherRestrictions` from page7 or restrictions text

**Final Structure:**

```python
{
    "patientName": str,
    "returnToFullDuty": bool,
    "returnToFullDutyDate": str (MM/DD/YYYY format),
    "unableToReturnToWork": bool,
    "unableToReturnStartDate": str,
    "unableToReturnEndDate": str,
    "unableToReturnReason": str,
    "returnToWorkWithRestrictions": bool,
    "restrictions": {
        "liftCarryPounds": str,
        "standing": str,
        "walking": str,
        # ... all restriction fields
    },
    "otherRestrictions": str
}
```

---

## Date Extraction

**Location:** Lines 2208-2243

**Sources (in priority order):**

1. `soap_doc.get("patientStatus")` object
2. Flat fields in SOAP document (`return_full_duty_date`, etc.)
3. `page7` structure dates

**Date Format:** Normalized to MM/DD/YYYY using `to_mmddyyyy()` function

---

## Key Features

1. **Comprehensive Source Checking:** Checks 8+ different locations for work status
2. **GPT-Powered Extraction:** Falls back to GPT when structured data not available
3. **Structured Restrictions:** Parses free-text restrictions into detailed structured format
4. **Priority-Based Merging:** Prioritizes structured data over text extraction
5. **Extensive Logging:** Logs source of work status extraction for debugging
6. **Default Handling:** Sets "[Not documented]" if nothing found (mandatory field per mapping)

---

## Logging and Debugging

The function logs extensively:

- ✓ Found Work Status from [source]
- ✅ Work Status successfully extracted from: [source]
- ⚠ Work Status not found in any source
- Attempting GPT extraction...
- Successfully parsed restrictions text

All log messages help track the extraction process and identify data sources.

---

## Data Mapping Requirements

Per the code comments:

- **Functional and Work Status:** Work Capacity / Restrictions (Mandatory)
- **Data Source:** Providers' Dictation (SOAP)
- **RTW status:** Full Duty / Modified Duty / TTD (Temporary Total Disability)

---

## Summary Flow Diagram

```
build_section_c() called
    ↓
Priority 1: Check nested work_status structure
    ↓ (if not found)
Priority 2: Check flat work_status field
    ↓ (if not found)
Priority 3: Check page7 structure
    ↓ (if not found)
Priority 4: Check plan section
    ↓ (if not found)
Priority 5: Check clinical_information.plan
    ↓ (if not found)
Priority 6: Extract from formatted_soap_note (regex → GPT)
    ↓ (if not found)
Priority 6b: Extract from transcription (GPT)
    ↓ (if not found)
Priority 7: Check intake form
    ↓ (if not found)
Priority 8: Check follow-up form
    ↓ (if not found)
Default: "[Not documented]"
    ↓
Normalize work_status → set boolean flags
    ↓
Extract restrictions (structured or parse from text)
    ↓
Build final restrictions object
    ↓
Return Section C structure
```

---

## Related Functions

- `extract_work_status_from_text()`: GPT extraction from text
- `parse_restrictions_from_text()`: GPT parsing of restrictions text
- `fetch_if_needed()`: MongoDB document fetching
- `fetch_latest_document()`: Latest document fetching
- `to_mmddyyyy()`: Date normalization
- `build_pr1_payload()`: Main PR1 structure builder (calls build_section_c)

---

## Notes

- All MongoDB collections use `ObjectId` which is converted to string for JSON serialization
- GPT extraction requires `OPENAI_API_KEY` environment variable
- Text is limited to 5000 characters for GPT calls to avoid token limits
- The system prioritizes existing structured fields over GPT-extracted data
- Work status is a mandatory field per data mapping requirements
