# Esqueleto mínimo (FastAPI + Pydantic)

Copiá estos archivos a `src/crew_shadow_crewai/` en tu repo nuevo y adaptá `run_crew()` a tu CrewAI real.

## `main.py`

```python
from fastapi import FastAPI

from crew_shadow_crewai.routes import router

app = FastAPI(title="Waseller shadow compare", version="0.1.0")
app.include_router(router)


@app.get("/health")
def health():
    return {"ok": True}
```

## `models.py`

```python
from pydantic import BaseModel, Field


class ShadowCompareRequest(BaseModel):
    schemaVersion: int = Field(..., ge=1, le=1)
    kind: str
    tenantId: str
    leadId: str
    incomingText: str
    interpretation: dict
    baselineDecision: dict


class CandidateDecision(BaseModel):
    draftReply: str | None = None
    intent: str | None = None
    nextAction: str | None = None
    recommendedAction: str | None = None
    confidence: float | None = None
    reason: str | None = None


class ShadowCompareResponse(BaseModel):
    candidateDecision: CandidateDecision | None = None
    candidateInterpretation: dict | None = None
```

## `routes.py`

```python
from fastapi import APIRouter, HTTPException

from crew_shadow_crewai.crew_app import run_crew
from crew_shadow_crewai.models import ShadowCompareRequest, ShadowCompareResponse

router = APIRouter()


@router.post("/shadow-compare", response_model=ShadowCompareResponse)
def shadow_compare(body: ShadowCompareRequest) -> ShadowCompareResponse:
    if body.kind != "waseller.shadow_compare.v1":
        raise HTTPException(status_code=400, detail="unsupported kind")
    return run_crew(body)
```

## `crew_app.py` (stub: reemplazar por CrewAI)

```python
from crew_shadow_crewai.models import (
    CandidateDecision,
    ShadowCompareRequest,
    ShadowCompareResponse,
)


def run_crew(body: ShadowCompareRequest) -> ShadowCompareResponse:
    # TODO: Crew(...).kickoff() y mapear salida al modelo.
    # Mientras tanto, eco mínimo para probar el cable Waseller → servicio.
    baseline = body.baselineDecision
    draft = str(baseline.get("draftReply", ""))
    return ShadowCompareResponse(
        candidateDecision=CandidateDecision(
            draftReply=f"[crew-stub] {draft}"[:2000],
            intent=str(baseline.get("intent", "")) or None,
            nextAction=str(baseline.get("nextAction", "")) or None,
            recommendedAction=str(baseline.get("recommendedAction", "")) or None,
            confidence=float(baseline.get("confidence", 0.5)),
            reason="stub",
        )
    )
```

## Probar con curl

Desde la raíz del repo Python, con el servidor en `localhost:8080`:

```bash
curl -sS -X POST http://127.0.0.1:8080/shadow-compare \
  -H "Content-Type: application/json" \
  -d @fixtures/request.example.json
```

Respuesta esperada (ejemplo):

```json
{
  "candidateDecision": {
    "draftReply": "[crew-stub] …",
    "intent": "consultar_precio",
    "nextAction": "reply_only",
    "recommendedAction": "reply_only",
    "confidence": 0.72,
    "reason": "stub"
  },
  "candidateInterpretation": null
}
```

## Comando Waseller

En workers: `LLM_SHADOW_COMPARE_URL=http://127.0.0.1:8080/shadow-compare` (solo en red que llegue al contenedor; en la nube usá URL pública).
