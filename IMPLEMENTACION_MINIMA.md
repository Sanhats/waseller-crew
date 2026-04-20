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

## Probar contra **producción** (Railway / HTTPS)

En el **servicio** crew (Railway): `USE_CREW_STUB` vacío o `false`, `OPENAI_API_KEY` definida para LLM real, `SHADOW_COMPARE_SECRET` alineado con workers y `SHADOW_COMPARE_REQUIRE_AUTH=true` si el endpoint es público.

En tu máquina, **desde la raíz de este repo**, exportá la URL pública (sin barra final) y el secret (mismo valor que `LLM_SHADOW_COMPARE_SECRET` en Waseller si usan Bearer):

```bash
export CREW_BASE_URL="https://tu-servicio.up.railway.app"
export SHADOW_COMPARE_SECRET="el-mismo-secreto-que-workers"
```

Health:

```bash
curl -sS "${CREW_BASE_URL}/health"
```

Shadow compare (contrato v1.1 + `stockTable`; path versionado recomendado):

```bash
curl -sS -w "\nHTTP %{http_code}\n" \
  "${CREW_BASE_URL}/v1/shadow-compare" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer ${SHADOW_COMPARE_SECRET}" \
  -d @fixtures/request.v1_1.example.json
```

Si en tu deploy **no** exigís auth (`SHADOW_COMPARE_REQUIRE_AUTH` no es `true`), omití la línea `Authorization`.

**Script** (mismo flujo):

```bash
chmod +x scripts/smoke-prod.sh   # una vez
./scripts/smoke-prod.sh
# otro fixture: ./scripts/smoke-prod.sh fixtures/request.example.json
# path legacy: CREW_SHADOW_PATH=/shadow-compare ./scripts/smoke-prod.sh
```

**Windows PowerShell** (equivalente; usá `curl.exe` para no chocar con el alias de PowerShell):

```powershell
$env:CREW_BASE_URL = "https://tu-servicio.up.railway.app"
$env:SHADOW_COMPARE_SECRET = "tu-secreto"
curl.exe -sS "$env:CREW_BASE_URL/health"
curl.exe -sS -w "`nHTTP %{http_code}`n" `
  "$env:CREW_BASE_URL/v1/shadow-compare" `
  -H "Content-Type: application/json" `
  -H "Authorization: Bearer $env:SHADOW_COMPARE_SECRET" `
  -d "@fixtures/request.v1_1.example.json"
```

## Probar en local (solo desarrollo)

Servidor en `127.0.0.1:8080` con stub (no llama a OpenAI):

```bash
USE_CREW_STUB=1 uv run uvicorn crew_shadow_crewai.main:app --host 127.0.0.1 --port 8080
```

Otra terminal:

```bash
curl -sS -X POST http://127.0.0.1:8080/v1/shadow-compare \
  -H "Content-Type: application/json" \
  -d @fixtures/request.v1_1.example.json
```

Con stub, la respuesta incluye `[crew-stub]` en `draftReply`. Sin stub y con `OPENAI_API_KEY`, el texto lo genera el crew.

## Comando Waseller (workers)

`LLM_SHADOW_COMPARE_URL` debe ser la URL **HTTPS** pública del crew, por ejemplo `https://tu-servicio.up.railway.app/v1/shadow-compare` (o `/shadow-compare`). Tiene que ser alcanzable desde los workers de Railway.
