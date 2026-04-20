import logging
import time

from fastapi import APIRouter, Depends, HTTPException

from crew_shadow_crewai.auth import check_shadow_compare_bearer
from crew_shadow_crewai.crew_app import run_crew
from crew_shadow_crewai.models import ShadowCompareRequest, ShadowCompareResponse
from crew_shadow_crewai.observability import structured_log_line

router = APIRouter()
log = logging.getLogger("crew_shadow_crewai.routes")


@router.post(
    "/shadow-compare",
    response_model=ShadowCompareResponse,
    dependencies=[Depends(check_shadow_compare_bearer)],
)
@router.post(
    "/v1/shadow-compare",
    response_model=ShadowCompareResponse,
    dependencies=[Depends(check_shadow_compare_bearer)],
)
def shadow_compare(body: ShadowCompareRequest) -> ShadowCompareResponse:
    t0 = time.perf_counter()
    if body.kind != "waseller.shadow_compare.v1":
        log.warning(
            structured_log_line(
                "shadow_compare_reject_kind",
                tenant_id=body.tenantId,
                lead_id=body.leadId,
                kind=body.kind,
                http_status=400,
            )
        )
        raise HTTPException(status_code=400, detail="unsupported kind")
    out = run_crew(body)
    elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 2)
    stock_rows = len(body.stockTable) if body.stockTable is not None else None
    log.info(
        structured_log_line(
            "shadow_compare_completed",
            tenant_id=body.tenantId,
            lead_id=body.leadId,
            correlation_id=body.correlationId,
            http_status=200,
            latency_ms=elapsed_ms,
            stock_table_rows=stock_rows,
            has_business_profile_slug=bool(body.businessProfileSlug),
        )
    )
    return out
