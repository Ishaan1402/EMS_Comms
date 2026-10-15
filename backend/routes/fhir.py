"""The verified handoff as a FHIR R4 Bundle (fhir_export.py), for the hospital's record system."""
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from backend import fhir_export
from backend.middleware.auth import require_role
from backend.routes.cases import get_case_for_user

router = APIRouter()


@router.get("/{case_id}/fhir")
async def case_as_fhir(case_id: str, current_user: dict = Depends(require_role(["emt", "doctor"]))):
    """The case's current handoff as a FHIR R4 collection Bundle; the same access as the case itself."""
    case = get_case_for_user(case_id, current_user)
    return JSONResponse(fhir_export.build(case), media_type="application/fhir+json")
