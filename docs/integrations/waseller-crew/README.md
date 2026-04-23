# Waseller ↔ waseller-crew (integración)

**Fuente de verdad extendida en el monorepo Waseller:** `docs/integrations/waseller-crew/CONTRATO_V1_1.md` (si existe allí). En **waseller-crew** el resumen implementado y los campos HTTP están en [`../../CONTRATO_HTTP_V1_1.md`](../../CONTRATO_HTTP_V1_1.md).

## Endpoints

- `POST /shadow-compare` y `POST /v1/shadow-compare` — mismo body y validación.

## Checklist waseller-crew (operativo)

1. **Body alineado al contrato** — Enviar todo el contexto que use el LLM:
   - `recentMessages`, `interpretation`, `baselineDecision`, `stockTable`, `inventoryNarrowingNote`
   - `tenantCommercialContext` y/o **`tenantBrief`**, **`etapa`**, **`activeOffer`**, **`memoryFacts`** (opcionales; se inyectan al prompt cuando vienen)
   - **`publicCatalogSlug`** y **`publicCatalogBaseUrl`** (opcionales; enlace literal `publicCatalogBaseUrl + "/tienda/" + publicCatalogSlug`; en main: `resolvePublicCatalogBaseUrlForCrew()` para la base)
   - `businessProfileSlug`, `correlationId`, etc. según [`CONTRATO_HTTP_V1_1.md`](../../CONTRATO_HTTP_V1_1.md)

2. **Respuesta** — JSON con `candidateDecision.draftReply` **no vacío** (y `intent` / `confidence` / `nextAction` coherentes) para que Waseller haga merge y reemplace el baseline cuando corresponda. Si el crew falla o el borrador queda vacío, el servicio puede rellenar desde baseline (ver logs `shadow_compare_empty_draft_*`).

3. **Timeouts y payload** — El cliente Waseller usa `LLM_SHADOW_COMPARE_TIMEOUT_MS`. Con `stockTable` grande (hasta **500** filas) y 3 pasos de LLM (director + redactor + crítico), subir timeout (p. ej. 60–120 s) o acotar filas. El cuello de botella es el LLM, no el parseo del body.

4. **Prompts / agentes** — El crew sigue el hilo (última oferta en `activeOffer` + últimos mensajes), evita repetir cierre genérico si el usuario cambia de eje, y **ató** precio/stock a `stockTable` (ver reglas en `crew_app.py`).

5. **Bearer** — Mismo secreto: `LLM_SHADOW_COMPARE_SECRET` (workers) = `SHADOW_COMPARE_SECRET` (crew). Producción: `SHADOW_COMPARE_REQUIRE_AUTH=true`.

6. **Prueba de regresión** — Fixture de diálogo mesa → colores: [`../../../fixtures/request.mesa_colores.json`](../../../fixtures/request.mesa_colores.json) + test `test_mesa_colores_fixture_parses_and_posts` en `tests/test_api.py`.

7. **Baseline de respaldo** — Si el crew no devuelve `draftReply` válido o falla HTTP, Waseller debe seguir con baseline (LLM interno o plantillas). Por eso el crew tiene que ser **confiable** antes de apagar el camino corto en producción.
