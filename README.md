# Servicio externo CrewAI (shadow compare) para Waseller

Esta carpeta en Waseller contiene todo el paquete para el **otro repo**:

| Archivo | Uso |
|---------|-----|
| [`README.md`](./README.md) | Contrato HTTP, enums, checklist, despliegue (este archivo). |
| [`pyproject.toml.example`](./pyproject.toml.example) | Copiar como `pyproject.toml` y ajustar nombre/versions de `crewai`. |
| [`.env.example`](./.env.example) | Variables del servicio Python. |
| [`fixtures/request.example.json`](./fixtures/request.example.json) | Body de ejemplo idéntico al que envía Waseller. |
| [`fixtures/request.v1_1.example.json`](./fixtures/request.v1_1.example.json) | Body v1 + campos opcionales y `recentMessages` (contrato v1.1). |
| [`docs/CONTRATO_HTTP_V1_1.md`](./docs/CONTRATO_HTTP_V1_1.md) | Resumen crew + checklist; **canon** en Waseller: `docs/integrations/waseller-crew/CONTRATO_V1_1.md`. |
| [`IMPLEMENTACION_MINIMA.md`](./IMPLEMENTACION_MINIMA.md) | Esqueleto + **`curl` producción** (`CREW_BASE_URL` + Bearer) y local stub. |
| [`scripts/smoke-prod.sh`](./scripts/smoke-prod.sh) | Smoke HTTPS contra el deploy (health + POST v1.1). |

Podés copiar **toda la carpeta** `docs/integrations/waseller-crew/` al nuevo repositorio (como `docs/` o raíz del proyecto Python).

Este documento es **autocontenido** para implementar el microservicio con **`uv`**. Waseller llama a este servicio cuando:

- `LLM_SHADOW_COMPARE_URL` apunta a tu URL (HTTPS en producción).
- El job de orquestación va en **`executionMode: "shadow"`** (ver `LlmRolloutService` / tenant).

Referencia en Waseller:

- Cliente HTTP: [`apps/workers/src/services/shadow-compare.service.ts`](../../../apps/workers/src/services/shadow-compare.service.ts)
- Validación de respuesta: [`packages/queue/src/external-agent-contract.ts`](../../../packages/queue/src/external-agent-contract.ts)

---

## Requisitos previos

| Requisito | Notas |
|-----------|--------|
| **Python** | 3.11 o 3.12 recomendado (CrewAI / deps suelen seguir estas versiones). |
| **uv** | Instalación: [documentación oficial de uv](https://docs.astral.sh/uv/getting-started/installation/). En Windows: instalador o `pip install uv`. |
| **API de LLM** | `OPENAI_API_KEY` u otro proveedor compatible con lo que use CrewAI en tu proyecto. |
| **Red** | URL pública HTTPS (Railway, Fly.io, Cloud Run, etc.) accesible **desde los workers** de Waseller. |
| **Seguridad** | `SHADOW_COMPARE_SECRET` + `SHADOW_COMPARE_REQUIRE_AUTH` (ver `docs/CONTRATO_HTTP_V1_1.md`). Waseller envía `Authorization: Bearer` si `LLM_SHADOW_COMPARE_SECRET` está definido. |

---

## Contrato HTTP (Waseller → tu servicio)

### Método y cabeceras

- **POST** a la URL exacta configurada en `LLM_SHADOW_COMPARE_URL` (puede ser `https://host/shadow-compare` o la raíz si así lo configurás).
- **Content-Type:** `application/json`
- **Timeout del cliente Waseller:** `LLM_SHADOW_COMPARE_TIMEOUT_MS` (default **8000** ms, máximo 120000). Tu servicio debe responder por debajo de ese valor o Waseller abortará la petición. Con **`stockTable` grande** + CrewAI, subí el timeout en workers o el cliente cortará antes de que termine el LLM (ver `docs/CONTRATO_HTTP_V1_1.md`).

### Cuerpo JSON (request)

**Núcleo v1 (siempre):** `schemaVersion`, `kind`, `tenantId`, `leadId`, `incomingText`, `interpretation`, `baselineDecision`.

**v1.1 (opcional, ya enviados por Waseller en prod/staging cuando aplica):** `phone`, `correlationId`, `messageId`, `conversationId`, `recentMessages`, `stockTable` (filas tipo `GET /products`, ≤500), `businessProfileSlug` (rubro seguro). Detalle y auth: [`docs/CONTRATO_HTTP_V1_1.md`](./docs/CONTRATO_HTTP_V1_1.md) y contrato canónico en el repo Waseller.

| Campo | Tipo | Descripción |
|--------|------|-------------|
| `schemaVersion` | `number` | Hoy siempre **1**. |
| `kind` | `string` | **`waseller.shadow_compare.v1`**. |
| `tenantId` | `string` | UUID del tenant. |
| `leadId` | `string` | UUID del lead. |
| `incomingText` | `string` | Último mensaje del cliente en claro. |
| `interpretation` | `object` | **`ConversationInterpretationV1`** (resumen abajo). |
| `baselineDecision` | `object` | **`LlmDecisionV1`** (resumen abajo). |

#### `ConversationInterpretationV1` (resumen)

- `intent`: string  
- `confidence`: number  
- `entities`: objeto; valores: string | number | boolean | null | objeto plano string→string  
- `references`: array de `{ kind, value?, axis?, index?, confidence?, metadata? }`  
- `conversationStage?`: uno de los estados de conversación Waseller  
- `missingFields`: string[]  
- `nextAction`: uno de los valores de **ConversationNextActionV1** (lista abajo)  
- `source`: `"rules"` \| `"openai"`  
- `notes?`: string[]  

#### `LlmDecisionV1` (resumen)

- `intent`, `leadStage` (`discovery` \| `consideration` \| `decision` \| `handoff`), `confidence`, `entities`, `nextAction`, `reason`, `requiresHuman`, `recommendedAction`, `draftReply`, `handoffRequired`, `qualityFlags`, `source` (`llm` \| `fallback`)  
- Opcionales: `policyBand`, `executionMode`, `policy`, `verification`, `provider`, `model`  

#### Valores permitidos: `ConversationNextActionV1`

```
reply_only | ask_clarification | confirm_variant | offer_reservation | reserve_stock
| share_payment_link | suggest_alternative | handoff_human | close_lead | manual_review
```

#### Valores permitidos: `ConversationStageV1`

```
waiting_product | waiting_variant | variant_offered | waiting_reservation_confirmation
| reserved_waiting_payment_method | payment_link_sent | waiting_payment_confirmation | sale_confirmed
```

Si devolvés `candidateInterpretation.nextAction` o `conversationStage`, deben ser **exactamente** uno de los literales anteriores (Waseller valida con sets fijos).

---

## Respuesta JSON (tu servicio → Waseller)

Waseller parsea el cuerpo con `parseShadowCompareHttpResponse`. Debe ser un **objeto JSON** (no HTML ni texto plano).

### Forma válida (mínima)

```json
{}
```

Válido pero poco útil: no habrá `candidateDecision` y el diff quedará como “skipped”.

### Forma recomendada (comparación útil)

```json
{
  "candidateDecision": {
    "draftReply": "Texto propuesto por Crew…",
    "intent": "consultar_precio",
    "nextAction": "reply_only",
    "recommendedAction": "reply_only",
    "confidence": 0.85,
    "reason": "Breve justificación interna"
  },
  "candidateInterpretation": {
    "intent": "consultar_precio",
    "confidence": 0.9,
    "nextAction": "reply_only",
    "source": "openai"
  }
}
```

**Reglas:**

- `candidateDecision` y `candidateInterpretation` son **opcionales**.
- Tipos estrictos: `draftReply` string, `confidence` number, `nextAction` string en el enum, etc. Si un campo tiene tipo incorrecto, Waseller marca la respuesta como inválida y guarda `issues` en la traza.
- `candidateInterpretation`, si se envía, se valida de forma **parcial**: si incluís `source`, debe ser `rules` o `openai`; `nextAction` y `conversationStage` deben ser literales válidos si están presentes.

### Código HTTP

Waseller **no** exige `2xx` para parsear: lee el body igualmente. Igual conviene devolver **`200 OK`** con JSON cuando el crew terminó bien, y **`4xx/5xx`** solo si querés dejar constancia en `httpStatus` (se persiste en la traza).

---

## Qué hace Waseller con tu respuesta

1. Si el JSON es inválido → traza `shadow_compare` con `error` y/o `issues`.  
2. Si es válido → calcula `diff` (`draftReplyEqual`, `intentMatch`, `nextActionMatch`, `recommendedActionMatch`, `confidenceDelta`) comparando `baselineDecision` con `candidateDecision`.  
3. **Importante:** en modo shadow, el **cliente no recibe** el `draftReply` del LLM de Waseller como mensaje final según la lógica actual del `lead.worker`; tu `candidateDecision` es **solo telemetría** hasta que integreis otro flujo.

---

## Crear el repo con `uv` (paso a paso)

En una carpeta vacía (fuera de Waseller):

```bash
uv init --package waseller-crew --python 3.12
cd waseller-crew
```

Editá `pyproject.toml` y añadí dependencias (ver archivo de ejemplo en esta carpeta: [`pyproject.toml.example`](./pyproject.toml.example)).

Sincronizar entorno:

```bash
uv sync
```

Estructura sugerida:

```
waseller-crew/
  pyproject.toml
  README.md                 # copia de este doc o resumen + enlace
  .env.example
  src/
    crew_shadow_crewai/
      __init__.py
      main.py                 # uvicorn: app FastAPI
      routes.py               # POST /shadow-compare
      models.py               # Pydantic: ShadowCompareRequest, ShadowCompareResponse
      crew_app.py             # Crew + tasks + agents
```

Arranque local:

```bash
uv run uvicorn crew_shadow_crewai.main:app --host 0.0.0.0 --port 8080
```

**Probar el deploy en producción:** definí `CREW_BASE_URL` y `SHADOW_COMPARE_SECRET` en el shell y usá el script o los `curl` de [`IMPLEMENTACION_MINIMA.md`](./IMPLEMENTACION_MINIMA.md). Fixture recomendado: `fixtures/request.v1_1.example.json`.

---

## Variables de entorno (servicio Python)

| Variable | Obligatoria | Descripción |
|----------|-------------|-------------|
| `OPENAI_API_KEY` | Sí (si usás OpenAI con Crew) | Clave del proveedor LLM. |
| `PORT` | No | Puerto del servidor (p. ej. 8080). Plataformas suelen inyectar `PORT`. |
| `SHADOW_COMPARE_SECRET` | Recomendada (prod) | Igual que `LLM_SHADOW_COMPARE_SECRET` en workers. |
| `SHADOW_COMPARE_REQUIRE_AUTH` | Opcional | `true` en prod si el endpoint es público. |
| `CREW_TENANT_PROMPTS_DIR` | Opcional | Overlay `tenant_prompts/<businessProfileSlug>.txt`. |
| `LOG_LEVEL` | No | `INFO`, `DEBUG`, etc. |

En **Waseller (workers)** ya existen:

- `LLM_SHADOW_COMPARE_URL` — URL de tu `POST`.
- `LLM_SHADOW_COMPARE_TIMEOUT_MS` — timeout del fetch.

---

## CrewAI: enfoque mínimo

1. **Un agente “redactor”** que reciba en contexto el JSON completo del POST (incl. `recentMessages`, `stockTable`, `businessProfileSlug` si vienen) y genere `candidateDecision` / `candidateInterpretation`.  
2. **Opcional: agente “crítico”** que revise el JSON y lo ajuste (proceso secuencial CrewAI).  
3. **Salida:** serializar **solo** el objeto que cumple el contrato de respuesta (mejor con Pydantic `model_dump()` para tipos correctos).

El contrato con Waseller es **stateless**: inventario y rubro llegan en el body; prompts por tenant pueden vivir en archivos (`tenant_prompts/`) en el deploy del crew.

---

## Despliegue

- Contenedor Docker con `CMD` tipo `uv run uvicorn ...` o `uv run gunicorn` si preferís.  
- Healthcheck: `GET /health` → `200` con `{"ok":true}`.  
- Waseller solo usa **POST** a la URL configurada; si montás el app en `/`, la variable sería `https://tu-dominio/` (o path explícito si el worker apunta a `/shadow-compare`).

---

## Checklist antes de enchufar producción

- [ ] `POST` responde en menos que `LLM_SHADOW_COMPARE_TIMEOUT_MS`.  
- [ ] JSON siempre parseable; errores del LLM capturados y devueltos como `500` + cuerpo JSON con `error` humano (Waseller igual intentará parsear `candidateDecision` si lo incluís).  
- [ ] `nextAction` / `conversationStage` / `source` dentro de enums permitidos.  
- [ ] HTTPS y secret compartido o red privada/VPN según tu amenaza.  
- [ ] Coste y rate limit: un mensaje shadow = al menos una corrida de crew; considerá cuotas por `tenantId`.

---

## Extensión futura (opcional)

- Segunda fase: sustituir el pipeline interno por la respuesta del crew **solo** tras verificación humana o métricas — diseño de producto, no solo shadow.
