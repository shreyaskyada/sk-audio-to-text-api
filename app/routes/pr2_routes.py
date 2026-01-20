from fastapi import APIRouter, HTTPException
from app.schemas.pr2_schema import PR2Form
from app.services import pr2_service

router = APIRouter()

@router.post("/")
async def create_pr2_form(data: PR2Form):
    try:
        doc_id = await pr2_service.save_pr2_form(data.model_dump(exclude_none=True))
        return {"status": "success", "message": "PR2 form saved successfully.", "document_id": doc_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/latest")
async def get_latest_pr2_form():
    doc = await pr2_service.get_latest_pr2_form()
    if not doc: raise HTTPException(status_code=404, detail="No PR2 forms found")
    return doc

@router.get("/{form_id}")
async def get_pr2_form(form_id: str):
    doc = await pr2_service.get_pr2_form_by_id(form_id)
    if not doc: raise HTTPException(status_code=404, detail="PR2 form not found")
    return doc
