# Contrato HTTP v1.1 — shadow compare (Waseller ↔ waseller-crew)

**Fuente de verdad coordinada (Waseller):** `docs/integrations/waseller-crew/CONTRATO_V1_1.md` en el monorepo Waseller. Este archivo resume lo que **waseller-crew** implementa y cómo responder al checklist de integración.

**Compatibilidad:** `schemaVersion: 1` y `kind: waseller.shadow_compare.v1` sin cambio; los campos v1.1 son **opcionales**. Clientes solo v1 siguen válidos.

---

## 1. Campos opcionales en el POST (v1.1)

Alineados al documento Waseller (mismos nombres y semántica).

| Campo | Tipo | Notas |
|--------|------|--------|
| `phone` | `string` | Opcional. |
| `correlationId` | `string` | Opcional. UUID de correlación. |
| `messageId` | `string` | Opcional. UUID del `Message`. |
| `conversationId` | `string \| null` | Opcional. |
| `recentMessages` | `array` | `{ "direction": "incoming" \| "outgoing", "message": "string" }[]`. En crew: tope **8** (truncado si hay más). |
| `businessProfileSlug` | `string` | Opcional. Patrón `^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$` (ej. `indumentaria_calzado`). Overlay de prompts: `tenant_prompts/<slug>.txt` o directorio `CREW_TENANT_PROMPTS_DIR`. |
| `stockTable` | `array` | Opcional. Filas alineadas a **`GET /products`** (una variante por fila). Propiedades típicas: `variantId`, `productId`, `name`, `sku`, `attributes`, `stock`, `reservedStock`, `availableStock`, `effectivePrice`, `imageUrl`, `isActive`, `tags`, `basePrice`, `variantPrice`. **Tope 500** filas por request (validado en crew). |
| `inventoryNarrowingNote` | `string` | Opcional. Texto de Waseller sobre cómo se acotó el inventario; se inyecta al prompt del crew. |

**Endpoints:** `POST /shadow-compare` y alias **`POST /v1/shadow-compare`** (mismo handler, misma validación Pydantic y misma auth).

**Campos futuros en el body:** `ShadowCompareRequest` usa `extra = "ignore"`: claves nuevas que aún no estén en el modelo no rompen el POST (se descartan del modelo interno).

**Respuesta (modo primary / parseo Waseller):** `candidateDecision` con `draftReply` **string no vacío** cuando el baseline trae borrador: si el LLM devuelve `draftReply` vacío, el servicio **rellena desde** `baselineDecision.draftReply` y deja traza `shadow_compare_empty_draft_filled_from_baseline` en logs. `nextAction` / `recommendedAction` siguen el enum Waseller (coerción a `null` si viene inválido).

---

## 2. Auth (waseller-crew)

| Variable | Descripción |
|----------|-------------|
| `SHADOW_COMPARE_SECRET` | Mismo valor que `LLM_SHADOW_COMPARE_SECRET` en workers Waseller. |
| `SHADOW_COMPARE_REQUIRE_AUTH` | `true`: exige `Authorization: Bearer <secret>`. En prod público: **`true`** recomendado. |

Waseller **solo envía** `Authorization: Bearer` si `LLM_SHADOW_COMPARE_SECRET` está definido y no vacío.

Implementación: dependencia FastAPI en la ruta POST (`auth.py` — no middleware global).

---

## 3. Timeout y payloads grandes (500 filas)

- **Parseo / validación Pydantic** del body con hasta 500 filas: coste bajo en CPU; no hay límite propio de “timeout de lectura del body” distinto al de la plataforma.
- **Cuello de botella:** llamada al **LLM (CrewAI)** con JSON grande en el prompt. Si Waseller usa `LLM_SHADOW_COMPARE_TIMEOUT_MS` bajo (p. ej. 8 s), requests con `stockTable` máximo pueden **abortar por timeout del cliente** antes de que termine el crew.
- **Recomendación ops:** con `stockTable` cercano al tope, subir **`LLM_SHADOW_COMPARE_TIMEOUT_MS`** en workers (p. ej. 60–120 s según modelo y latencia) o reducir filas enviadas si basta con un subconjunto para shadow.

---

## 4. Fixtures

- `fixtures/request.example.json` — solo v1.
- `fixtures/request.v1_1.example.json` — v1 + opcionales; `stockTable` en forma **canónica Waseller** (shape `GET /products`).

## 4.1 Comandos contra producción

Definí `CREW_BASE_URL` (HTTPS, sin `/` final) y, si aplica, `SHADOW_COMPARE_SECRET`. Ver **`IMPLEMENTACION_MINIMA.md`** y **`scripts/smoke-prod.sh`** en la raíz del repo.

---

## 4.2 Observabilidad (logs estructurados)

Cada request exitosa al POST emite una línea JSON con `event: shadow_compare_completed` (`tenant_id`, `lead_id`, `correlation_id` si viene, `http_status`, `latency_ms`, `stock_table_rows`, `has_business_profile_slug`).

- `shadow_compare_reject_kind`: `kind` inválido (HTTP 400).
- `crew_failure`: error CrewAI / parseo JSON (`error_type`); luego se degrada a stub.
- `shadow_compare_empty_draft_filled_from_baseline` / `shadow_compare_empty_draft_no_fallback`: ajuste de `draftReply` vacío.

**Health:** `GET /health` → `{"ok": true}` (Railway / probes).

**Timeouts:** el cliente Waseller usa `LLM_SHADOW_COMPARE_TIMEOUT_MS`; con `stockTable` grande y LLM lento, subir ese valor en workers (ver §3).

---

## 5. Checklist coordinado

| Lado | Estado |
|------|--------|
| **Waseller** | Body extendido + Bearer condicional (según su doc). |
| **waseller-crew** | `ShadowCompareRequest` con opcionales; auth Bearer; fixture; prompts con `stockTable` / tenant; alias `/v1/shadow-compare`. |
| **Ops** | Mismo secret workers ↔ crew; prod: `SHADOW_COMPARE_REQUIRE_AUTH=true`; timeout worker acorde al LLM. |

---

## 6. Texto sugerido para confirmar a Waseller main

*(Copiar y ajustar URL de deploy y evidencia de logs/métricas.)*

> Confirmamos: **waseller-crew** en deploy acepta y valida `stockTable` (≤500 filas, dict flexible alineado a filas tipo `GET /products`) y `businessProfileSlug` (mismo regex seguro). Auth: `SHADOW_COMPARE_SECRET` + `SHADOW_COMPARE_REQUIRE_AUTH` como en `CONTRATO_V1_1.md`. El servicio no impone un timeout propio más estricto que el de la plataforma sobre el body; el tiempo total depende sobre todo del LLM — recomendamos revisar `LLM_SHADOW_COMPARE_TIMEOUT_MS` si envían contexto muy grande. URL crew: `<HTTPS…/shadow-compare o /v1/shadow-compare>`. Tráfico 2xx: `<adjuntar ventana de logs Railway / métricas o confirmar tras primer smoke>`.
