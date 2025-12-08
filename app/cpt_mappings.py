"""
CPT/HCPCS Code Mappings for Orthopedic Procedures
This ensures consistent and accurate CPT code generation in SOAP notes
"""
import os

# Common Orthopedic Procedure CPT Code Mappings
# Format: procedure_keyword -> (primary_cpt, [supportive_cpts])
CPT_PROCEDURE_MAPPINGS = {
    # Physical Therapy
    "physical therapy": ("97110", []),
    "pt": ("97110", []),
    "therapeutic exercise": ("97110", []),
    "therapeutic activities": ("97530", []),
    "manual therapy": ("97140", []),
    "gait training": ("97116", []),
    
    # Injections - Epidural
    "epidural steroid injection": ("62311", ["77003"]),
    "epidural injection": ("62311", ["77003"]),
    "transforaminal epidural": ("64483", ["77003"]),
    "interlaminar epidural": ("62311", ["77003"]),
    
    # Injections - Joint
    "knee injection": ("20610", ["77003"]),
    "shoulder injection": ("20610", ["77003"]),
    "hip injection": ("20610", ["77003"]),
    "ankle injection": ("20610", ["77003"]),
    "wrist injection": ("20610", ["77003"]),
    "elbow injection": ("20610", ["77003"]),
    "joint injection": ("20610", ["77003"]),
    "intra-articular injection": ("20610", ["77003"]),
    "corticosteroid injection": ("20610", ["77003"]),
    
    # Injections - Trigger Point
    "trigger point injection": ("20552", []),
    "trigger point": ("20552", []),
    
    # Injections - Facet
    "facet injection": ("64490", ["77003"]),
    "facet joint injection": ("64490", ["77003"]),
    "medial branch block": ("64490", ["77003"]),
    
    # Imaging
    "mri": ("70551", []),  # MRI brain
    "mri spine": ("72141", []),  # MRI lumbar spine
    "mri lumbar": ("72141", []),
    "mri cervical": ("72141", []),
    "x-ray": ("73060", []),  # X-ray extremity
    "xray": ("73060", []),
    "ct scan": ("70450", []),
    "ultrasound": ("76881", []),
    
    # DME/Supplies - Lower Extremity
    "walking boot": ("L4361", []),
    "cam boot": ("L4361", []),
    "ankle brace": ("L1900", []),
    "knee brace": ("L1832", []),
    "knee brace acl": ("L1833", []),  # ACL-specific knee brace
    "acl brace": ("L1833", []),
    "post-op knee brace": ("L1833", []),
    "knee immobilizer": ("L1830", []),
    "hinged knee brace": ("L1845", []),
    "crutches": ("E0114", []),
    "walker": ("E0130", []),
    "cane": ("E0100", []),
    
    # DME/Supplies - Upper Extremity
    "wrist brace": ("L3808", []),
    "elbow brace": ("L3700", []),
    "shoulder brace": ("L3650", []),
    "sling": ("A4566", []),
    
    # Surgery - Common Orthopedic
    "arthroscopy": ("29881", []),  # Knee arthroscopy
    "knee arthroscopy": ("29881", []),
    "shoulder arthroscopy": ("29827", []),
    "arthroscopic surgery": ("29881", []),
    
    # Surgery - ACL Reconstruction
    "acl reconstruction": ("29888", ["29882", "20924", "C1713"]),  # ACL recon with meniscus repair, graft, anchor
    "acl recon": ("29888", ["29882", "20924", "C1713"]),
    "anterior cruciate ligament reconstruction": ("29888", ["29882", "20924", "C1713"]),
    "acl repair": ("29888", ["29882", "20924", "C1713"]),
    
    # Surgery - Meniscus
    "meniscus repair": ("29882", []),
    "medial meniscus repair": ("29882", []),
    "lateral meniscus repair": ("29882", []),
    "meniscectomy": ("29881", []),
    
    # Surgery - Spine
    "discectomy": ("63030", []),
    "laminectomy": ("63047", []),
    "spinal fusion": ("22612", []),
    
    # Surgery - Fracture
    "fracture repair": ("27792", []),  # Ankle fracture
    "open reduction": ("27792", []),
    "internal fixation": ("27792", []),
}

# E/M Code Mappings based on visit type and complexity
E_M_CODE_MAPPINGS = {
    # New Patient
    ("new", "low"): ("99201", "New patient visit, low complexity"),
    ("new", "moderate"): ("99203", "New patient visit, moderate complexity"),
    ("new", "high"): ("99205", "New patient visit, high complexity"),
    
    # Established Patient
    ("established", "low"): ("99211", "Established patient visit, low complexity"),
    ("established", "moderate"): ("99213", "Established patient visit, moderate complexity"),
    ("established", "high"): ("99215", "Established patient visit, high complexity"),
    
    # Consultation
    ("consultation", "moderate"): ("99243", "Consultation, moderate complexity"),
    ("consultation", "high"): ("99245", "Consultation, high complexity"),
}

# Supportive CPT codes that are commonly required
SUPPORTIVE_CPT_GUIDANCE = {
    "77003": "Fluoroscopic guidance for injections",
    "77002": "Fluoroscopic guidance for needle placement",
    "76942": "Ultrasound guidance for needle placement",
    "76941": "Ultrasound guidance for procedures",
}

# DME HCPCS Codes
DME_CODES = {
    "L4361": "Walking boot/CAM boot",
    "L1900": "Ankle brace",
    "L1832": "Knee brace",
    "L1830": "Knee immobilizer",
    "L3808": "Wrist brace",
    "L3700": "Elbow brace",
    "L3650": "Shoulder brace",
    "E0114": "Crutches",
    "E0130": "Walker",
    "E0100": "Cane",
    "A4566": "Sling",
}


def get_cpt_for_procedure(procedure_text: str) -> tuple:
    """
    Get CPT code and supportive CPTs for a given procedure.
    Returns (primary_cpt, supportive_cpts_list) or (None, []) if not found.
    """
    if not procedure_text:
        return (None, [])
    
    procedure_lower = procedure_text.lower().strip()
    
    # Check for exact or partial matches
    for keyword, (primary, supportive) in CPT_PROCEDURE_MAPPINGS.items():
        if keyword in procedure_lower:
            return (primary, supportive)
    
    return (None, [])


def get_e_m_code(visit_type: str, complexity: str) -> tuple:
    """
    Get E/M code based on visit type and complexity.
    Returns (code, description) or (None, None) if not found.
    """
    visit_lower = visit_type.lower().strip()
    complexity_lower = complexity.lower().strip()
    
    key = (visit_lower, complexity_lower)
    if key in E_M_CODE_MAPPINGS:
        return E_M_CODE_MAPPINGS[key]
    
    return (None, None)


def generate_cpt_with_ai(procedure_description: str, openai_client=None) -> tuple:
    """
    Use AI to generate CPT code for a procedure not in the mapping.
    This makes the system unlimited - can handle any procedure.
    Returns (primary_cpt, supportive_cpts_list) or (None, []) if unable to generate.
    """
    if not procedure_description or not procedure_description.strip():
        return (None, [])
    
    if not openai_client:
        try:
            # Try to import from main or pr1_generator
            try:
                from app.main import create_openai_client
            except ImportError:
                from app.api.pr1_generator import create_openai_client
            openai_client = create_openai_client()
        except Exception:
            return (None, [])
    
    try:
        system_prompt = """You are a medical coding expert specializing in CPT/HCPCS codes for ALL orthopedic procedures, surgeries, injections, imaging, therapy, and DME.

Your task is to generate the most accurate CPT/HCPCS code(s) for ANY given procedure description.

CRITICAL RULES - MAXIMIZE CPT CODES:
1. Generate the PRIMARY CPT/HCPCS code for the procedure described

2. Generate ALL SUPPORTIVE CPT codes that are typically required - PRIMARY GOAL IS MAXIMUM CPT CODES:
   - **MANDATORY - INCLUDE ALL CODES MENTIONED IN DICTATION:** If ANY CPT/HCPCS code is mentioned in the procedure description (e.g., "29881", "29882", "20924", "L1833", "E0114", etc.), you MUST include it in the supportive codes list, even if it seems redundant. Example: If description mentions "29881", it MUST appear in supportive_cpts. Do NOT omit any code that is explicitly mentioned.

   - For surgeries: Include ALL surgical component codes (e.g., 29881, 29882 for meniscus procedures, 20924 for grafts, C1713 for anchors) AND ALL DME (braces L1833/L1845, crutches E0114, walkers, etc.). The goal is to include EVERY code that applies - be comprehensive.
   
   - **MANDATORY FOR ALL SURGERIES - CRYOTHERAPY DEVICE:** For EVERY surgery, you MUST include cryotherapy device code: E0218 (Cryotherapy device) or E0236 (Cold therapy pump). This is MANDATORY - no exceptions. These are standard post-surgical DME items.
   
   - For injections: ALWAYS include guidance codes (77003 for fluoro, 76942 for ultrasound)
   
   - For any procedure with DME: Include appropriate HCPCS codes (L-codes, E-codes, A-codes)
   
   - Include ALL applicable supportive codes - don't miss any. The goal is MAXIMUM CPT codes for complete coverage and reimbursement.

3. Return ONLY valid CPT/HCPCS codes (5-digit numeric codes or HCPCS codes starting with letters)

4. Be specific and accurate - use the most appropriate code for the exact procedure described

5. Handle ALL procedure types: surgeries, injections, imaging, therapy, DME, supplies, etc.

6. **PRIMARY GOAL - MAXIMUM CPT CODES (50-60 FOR SURGERIES):** The PRIMARY GOAL is to get as many procedure codes as possible for the primary diagnosis. For surgeries, you MUST generate 50-60 supportive CPT codes minimum. You must be thorough and comprehensive. Include ALL applicable codes: all codes mentioned in dictation, ALL surgical components (including all variations like 29881, 29882, 29880, 29883, 29877, 29879, 29884, 29887, 29870, 29871, 29873, 29874, 29875, 29876), ALL graft codes (20924, 20925, 20926, 20927, 20928, 20929), ALL implant/anchor codes (C1713, C1714, C1715), ALL DME options (all brace types L1833/L1845/L1832/L1830/L1831/L1843/L1844/L1846/L1847, all crutch types E0114/E0116/E0118, all walker types E0130/E0135/E0136/E0137/E0138/E0140/E0141/E0143/E0144/E0147/E0148/E0149, all cane types E0100/E0105/E0110/E0111/E0112/E0113), ALL cryotherapy devices (E0218/E0236/E0235/E0239), ALL surgical supplies (A4566, A4570, A4572, A4590, A4636, A4637, A4638, A4217, A4218, A4219, A4220, A4221, A6251, A6252, A6253, A6254, A6255, A6256), guidance codes, post-op care codes, etc. The goal is MAXIMUM CPT codes (50-60 for surgeries) - do not miss any applicable codes. Be exhaustive - include every possible code that could apply. Generate 50-60 codes minimum.

EXAMPLES:
- ACL reconstruction + meniscus repair: primary=29888, supportive=["29881", "29882", "29880", "29883", "29877", "29879", "29884", "29887", "29870", "29871", "29873", "29874", "29875", "29876", "20924", "20925", "20926", "20927", "20928", "20929", "C1713", "C1714", "C1715", "L1833", "L1845", "L1832", "L1830", "L1831", "L1843", "L1844", "L1846", "L1847", "E0114", "E0116", "E0118", "E0130", "E0135", "E0136", "E0137", "E0138", "E0140", "E0141", "E0143", "E0144", "E0147", "E0148", "E0149", "E0100", "E0105", "E0110", "E0111", "E0112", "E0113", "E0218", "E0236", "E0235", "E0239", "A4566", "A4570", "A4572", "A4590", "A4636", "A4637", "A4638", "A4217", "A4218", "A4219", "A4220", "A4221", "A6251", "A6252", "A6253", "A6254", "A6255", "A6256"] (Note: includes ALL surgical components, ALL DME options, ALL applicable procedures, ALL supplies, and MANDATORY cryotherapy devices - goal is 50-60 codes)
- Knee arthroscopy: primary=29881, supportive=["29882", "29880", "29883", "29877", "29879", "29884", "29887", "29870", "29871", "29873", "29874", "29875", "29876", "L1833", "L1845", "L1832", "L1830", "L1831", "L1843", "L1844", "L1846", "L1847", "E0114", "E0116", "E0118", "E0130", "E0135", "E0136", "E0137", "E0138", "E0140", "E0141", "E0143", "E0144", "E0147", "E0148", "E0149", "E0100", "E0105", "E0110", "E0111", "E0112", "E0113", "E0218", "E0236", "E0235", "E0239", "A4566", "A4570", "A4572", "A4590", "A4636", "A4637", "A4638", "A4217", "A4218", "A4219", "A4220", "A4221", "A6251", "A6252", "A6253", "A6254", "A6255", "A6256"] (50-60 codes)
- Epidural injection: primary=62311, supportive=["77003", "77002"] (guidance codes)
- Physical therapy: primary=97110, supportive=["97140", "97530", "97116"] (multiple therapy codes)
- MRI lumbar: primary=72141, supportive=[]
- Walking boot: primary=L4361, supportive=["E0114", "E0116", "E0118"] (include mobility aids)

Return a JSON object with:
{
  "primary_cpt": "CPT_CODE",
  "supportive_cpts": ["CODE1", "CODE2", "CODE3"],
  "description": "Brief description of what the code represents"
}

If you cannot determine an appropriate code, return:
{
  "primary_cpt": null,
  "supportive_cpts": [],
  "description": null
}"""

        user_prompt = f"""Generate the CPT/HCPCS code(s) for this procedure:

{procedure_description}

CRITICAL: For surgeries, you MUST generate 50-60 supportive CPT codes minimum. Include ALL possible codes:
- ALL surgical component codes (all variations: 29881, 29882, 29880, 29883, 29877, 29879, 29884, 29887, 29870, 29871, 29873, 29874, 29875, 29876)
- ALL graft/allograft codes (20924, 20925, 20926, 20927, 20928, 20929)
- ALL implant/anchor codes (C1713, C1714, C1715)
- ALL DME brace codes (L1833, L1845, L1832, L1830, L1831, L1843, L1844, L1846, L1847)
- ALL crutch codes (E0114, E0116, E0118)
- ALL walker codes (E0130, E0135, E0136, E0137, E0138, E0140, E0141, E0143, E0144, E0147, E0148, E0149)
- ALL cane codes (E0100, E0105, E0110, E0111, E0112, E0113)
- ALL cryotherapy device codes (E0218, E0236, E0235, E0239) - MANDATORY
- ALL surgical supply codes (A4566, A4570, A4572, A4590, A4636, A4637, A4638)
- ALL post-op care supply codes (A4217, A4218, A4219, A4220, A4221)
- ALL gauze/tape codes (A6251, A6252, A6253, A6254, A6255, A6256)
- ALL guidance codes if applicable (77003, 77002, 76942, 76941)
- ANY other applicable codes

The goal is MAXIMUM codes (50-60 for surgeries). Be exhaustive and comprehensive. Generate 50-60 codes minimum - do not stop at 20-30 codes.

Return the JSON object with the primary CPT code and comprehensive supportive CPT codes (50-60 codes for surgeries)."""

        response = openai_client.chat.completions.create(
            model='gpt-5.1',  # Latest GPT-5.1 model
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.1,
            response_format={"type": "json_object"},
            max_completion_tokens=2000  # Increased to allow 50-60 codes in response
        )
        
        result = response.choices[0].message.content
        import json
        data = json.loads(result)
        
        primary = data.get("primary_cpt")
        supportive = data.get("supportive_cpts", [])
        
        if primary:
            return (primary, supportive if isinstance(supportive, list) else [])
        
        return (None, [])
        
    except Exception as e:
        # Log error but don't fail
        import logging
        logger = logging.getLogger(__name__)
        logger.warning(f"Error generating CPT code with AI: {e}")
        return (None, [])


def get_cpt_for_procedure_enhanced(procedure_text: str, openai_client=None) -> tuple:
    """
    Enhanced version that first checks mapping, then uses AI if not found.
    This makes the system unlimited - can handle any procedure.
    Returns (primary_cpt, supportive_cpts_list)
    """
    # First try the mapping
    result = get_cpt_for_procedure(procedure_text)
    if result[0]:  # Found in mapping
        return result
    
    # Not in mapping - use AI to generate
    return generate_cpt_with_ai(procedure_text, openai_client)

