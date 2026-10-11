from fastapi import APIRouter, Depends

import sas_sync
from middleware.auth import require_role

router = APIRouter()


@router.get("/status")
async def sas_status(check: bool = False, current_user: dict = Depends(require_role(["doctor"]))):
    """
    State of the push to SAS Viya: whether it's running, the last success and error, and rows
    per table. ?check=true also signs in to SAS and reads the CAS server (no writes).
    """
    status = sas_sync.sync.describe()
    if check:
        if not sas_sync.sync.settings.configured:
            status["check"] = {"ok": False, "error": "SAS is not configured"}
        else:
            try:
                status["check"] = {"ok": True, "details": await sas_sync.check(sas_sync.sync.settings)}
            except sas_sync.SasError as error:
                status["check"] = {"ok": False, "error": str(error)}
            except Exception as error:
                status["check"] = {"ok": False, "error": f"{type(error).__name__}: could not reach SAS"}
    return status
