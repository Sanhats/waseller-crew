from fastapi import APIRouter, Depends, HTTPException

from crew_shadow_crewai.auth import check_shadow_compare_bearer
from crew_shadow_crewai.crew_app import run_crew
from crew_shadow_crewai.models import ShadowCompareRequest, ShadowCompareResponse

router = APIRouter()


@router.post(
    "/shadow-compare",
    response_model=ShadowCompareResponse,
    dependencies=[Depends(check_shadow_compare_bearer)],
)
def shadow_compare(body: ShadowCompareRequest) -> ShadowCompareResponse:
    if body.kind != "waseller.shadow_compare.v1":
        raise HTTPException(status_code=400, detail="unsupported kind")
    return run_crew(body)
