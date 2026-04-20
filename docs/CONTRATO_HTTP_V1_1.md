# Contrato HTTP v1.1 — shadow compare (Waseller ↔ waseller-crew)

**Estado:** implementado en **waseller-crew** (modelos + auth opcional + fixture). El **PR en Waseller** añade envío de campos opcionales y header cuando corresponda.

**Compatibilidad:** `schemaVersion` y `kind` sin cambio; campos nuevos **opcionales**. Clientes solo v1 siguen válidos.

## Campos opcionales en el POST (v1.1)

| Campo | Tipo | Descripción |
|--------|------|-------------|
| `phone` | `string` | Teléfono / id de canal según Waseller. |
| `correlationId` | `string` | UUID de correlación del flujo. |
| `messageId` | `string` | UUID del `Message`. |
| `conversationId` | `string \| null` | UUID de conversación. |
| `recentMessages` | `array` | `{ "direction": "incoming" \| "outgoing", "message": "string" }[]`, tope **8** (se trunca si hay más). |
| `businessProfileSlug` | `string` | Opcional. `^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$`. Si existe `tenant_prompts/<slug>.txt` (o `CREW_TENANT_PROMPTS_DIR`), el crew inyecta ese texto al prompt. |
| `stockTable` | `array` de objetos | Opcional. Filas de inventario **tal cual** las serializa Waseller (mismas claves que la tabla interna). Tope **500** filas. El crew debe usar solo esas filas para afirmar producto/precio/stock; no inventar catálogo. |

**Endpoint versionado (alias):** mismo handler que `POST /shadow-compare` → `POST /v1/shadow-compare` (`kind` y `schemaVersion` sin cambio hasta v2).

## Auth (waseller-crew)

| Variable | Descripción |
|----------|-------------|
| `SHADOW_COMPARE_SECRET` | Mismo valor que `LLM_SHADOW_COMPARE_SECRET` en workers. |
| `SHADOW_COMPARE_REQUIRE_AUTH` | `true`: exige `Authorization: Bearer <secret>`. Sin esto o `false`: no 401 por auth. |

Waseller solo envía `Authorization: Bearer` si el secret está definido en workers (`LLM_SHADOW_COMPARE_SECRET`).

## Fixtures

- `fixtures/request.example.json` — solo v1.
- `fixtures/request.v1_1.example.json` — v1 + opcionales de ejemplo.

## Checklist coordinado

| Lado | Tarea |
|------|--------|
| Waseller (PR) | Serializar opcionales + header condicional; `.env.example` workers. |
| waseller-crew | Hecho: `ShadowCompareRequest`, auth, fixture, este doc. |
| Ops | Mismo secret en workers y servicio; prod con `SHADOW_COMPARE_REQUIRE_AUTH=true` si el endpoint es público. |
