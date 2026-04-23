# Sincronización waseller-crew ↔ Waseller (TypeScript)

Este documento sirve para **verificar** que el servicio Python **waseller-crew** está alineado con el comportamiento y los contratos que Waseller usa hoy en producción. Viví en el repo **waseller-crew**; actualizalo cuando cambien enums, payloads HTTP o tablas relevantes en **cualquiera** de los dos lados.

**Contrato HTTP resumido en este repo:** [`CONTRATO_HTTP_V1_1.md`](CONTRATO_HTTP_V1_1.md). **Contrato canónico coordinado (monorepo Waseller):** `docs/integrations/waseller-crew/CONTRATO_V1_1.md`.

---

## 1. Superficie HTTP

| Aspecto | En Waseller | Comprobar en waseller-crew |
|--------|-------------|----------------------------|
| URL | `LLM_SHADOW_COMPARE_URL` (workers) | Misma ruta y método `POST` (`/shadow-compare` o `/v1/shadow-compare`; mismo handler en `routes.py`) |
| Auth opcional | `Authorization: Bearer` si hay `LLM_SHADOW_COMPARE_SECRET` / `SHADOW_COMPARE_SECRET` | `auth.py` + `SHADOW_COMPARE_REQUIRE_AUTH` |
| Timeout cliente | `LLM_SHADOW_COMPARE_TIMEOUT_MS` (máx. 120000 en workers) | El crew no impone el timeout del cliente; la respuesta debe ser **estable** por debajo del valor configurado en Waseller (ver [`CONTRATO_HTTP_V1_1.md`](CONTRATO_HTTP_V1_1.md) §3) |
| Cuerpo | `kind: waseller.shadow_compare.v1`, `schemaVersion: 1` | `ShadowCompareRequest` en `src/crew_shadow_crewai/models.py` con **`extra = "ignore"`** |

Referencia detallada de campos (lado crew): [`CONTRATO_HTTP_V1_1.md`](CONTRATO_HTTP_V1_1.md). Checklist operativo: [`integrations/waseller-crew/README.md`](integrations/waseller-crew/README.md).

---

## 2. Tipos canónicos (fuente de verdad en Waseller)

| Artefacto | Ubicación en monorepo Waseller |
|-----------|--------------------------------|
| Interpretación | `ConversationInterpretationV1` en `packages/queue/src/contracts.ts` |
| Decisión / reply | `LlmDecisionV1` en el mismo archivo |
| Validación JSON externo | `packages/queue/src/external-agent-contract.ts` (`parseExternalConversationInterpretation`, `parseExternalLlmDecision`) |
| JSON Schema export | `conversationInterpretationV1JsonSchema`, `llmDecisionV1JsonSchema` en el mismo módulo |

**Checklist:** los literales de `ConversationNextActionV1` y `ConversationStageV1` que acepta Waseller deben coincidir con los que modela el crew (`constants.py` / `CandidateDecision` / `CandidateInterpretation` en `models.py`) — sin sinónimos ni valores legacy.

---

## 3. Pipeline de workers (orden real, Waseller)

1. **Ingesta** → cola `incoming_messages` (`IncomingMessageJobV1`).
2. **`message-processor.worker`** — matcher, lead en Prisma, reglas; con URL de crew y delegación activa encola **siempre** `llmOrchestration`.
3. **`conversation-orchestrator.worker`** — en modo delegación al crew: stub de interpretación + baseline mínimo + **POST** al crew (`tryWasellerCrewPrimaryReplacement`); verificador y guardrails sobre el `draftReply` devuelto.
4. **`lead.worker`** — efectos deterministas (p. ej. Mercado Pago, reservas), armado del mensaje al cliente y cola `outgoing`.
5. **`sender.worker`** — envío al canal.

Documento de alto nivel en Waseller: `architecture/agent-pipeline.md` (ruta relativa al raíz del monorepo Waseller).

---

## 4. Datos de negocio que Waseller inyecta al POST

| Bloque | Origen en Waseller | Notas para verificación en crew |
|--------|---------------------|----------------------------------|
| `tenantBrief` | `tenant_knowledge` → perfil normalizado (`buildCrewTenantBriefFromProfile`) | Tono, envíos, pagos, políticas (sin secretos); prompt en `crew_app.py` |
| `tenantCommercialContext` | Derivado del brief | Texto plano opcional |
| `tenantRuntimeContext` | Fila `tenants` + integraciones (sin secretos); `loadCrewTenantRuntimeContextForCrewPayload` en `apps/workers/.../shadow-compare.service.ts` | Opcional; Pydantic en `tenant_runtime_context.py` + campo en `ShadowCompareRequest`. Si `identity.tenantId` ≠ `tenantId` raíz: **warning** por defecto, o **422** con `SHADOW_COMPARE_STRICT_TENANT_RUNTIME_IDENTITY=true`. Ver fixture [`../fixtures/request.tenant_runtime_context.v1.json`](../fixtures/request.tenant_runtime_context.v1.json) |
| `interpretation` | Processor u orquestador | El crew expone `candidateInterpretation` alineado a subset Waseller |
| `baselineDecision` | LLM interno o stub en modo crew-only | El crew devuelve `candidateDecision`; relleno desde baseline si `draftReply` vacío (logs `shadow_compare_empty_draft_*`) |
| `recentMessages` | Hasta 8 mensajes, orden cronológico | Truncado en validador si vinieran más |
| `stockTable` / RAG productos | SQL / catálogo | Prioridad en prompts: **stockTable** gana sobre brief / runtime / interpretación |
| `activeOffer`, `memoryFacts`, `etapa` | Job + memoria de conversación | Opcionales v1.1 |

Si el **dashboard** marca perfil incompleto (`crewCommercialContextComplete === false`), Waseller **sigue** llamando al crew cuando hay URL; el comerciante ve un aviso para completar tono y entregas y mejorar la calidad del contexto.

---

## 5. Variables de entorno críticas

### 5.1 Workers (Waseller)

| Variable | Efecto |
|----------|--------|
| `LLM_SHADOW_COMPARE_URL` | Habilita POST al crew |
| `WASELLER_CREW_DELEGATE_CONVERSATION=false` | Opt-out: no asumir delegación total solo por URL |
| `WASELLER_CREW_PRIMARY` / `WASELLER_CREW_SOLE_MODE` | Modos explícitos legacy; ver README de integración en Waseller / [`GUIA_INTEGRACION_WASELLER_MAIN.md`](GUIA_INTEGRACION_WASELLER_MAIN.md) |
| `LLM_SHADOW_COMPARE_TIMEOUT_MS` | Timeout del cliente hacia el crew |
| `LLM_SHADOW_COMPARE_SECRET` | Bearer hacia el crew (debe igualar `SHADOW_COMPARE_SECRET` en crew) |

### 5.2 Servicio waseller-crew

| Variable | Efecto |
|----------|--------|
| `SHADOW_COMPARE_SECRET` | Secreto Bearer (par con workers) |
| `SHADOW_COMPARE_REQUIRE_AUTH` | `true` / `1` / `yes`: exige `Authorization: Bearer` |
| `SHADOW_COMPARE_STRICT_TENANT_RUNTIME_IDENTITY` | Opcional: si `tenantRuntimeContext.identity.tenantId` no coincide con `tenantId` raíz → **422** |
| `USE_CREW_STUB` | Tests / entornos sin LLM real |

Detalle: [`CONTRATO_HTTP_V1_1.md`](CONTRATO_HTTP_V1_1.md) §2.

---

## 6. Prueba de humo cruzada

1. **Fixture v1.1 en este repo:** [`../fixtures/request.v1_1.example.json`](../fixtures/request.v1_1.example.json). **Fixture `tenantRuntimeContext`:** [`../fixtures/request.tenant_runtime_context.v1.json`](../fixtures/request.tenant_runtime_context.v1.json).
2. **En Waseller:** job real con `correlationId` y traza en `LlmTrace` / eventos de respuesta bot.
3. **Comparar** `draftReply` y `nextAction` del crew con lo persistido tras guardrails en workers.

Tests automatizados (parseo + POST con stub): `tests/test_api.py`.

---

## 7. Cambios que obligan a revisar este documento

- Nuevos valores en `LeadStatus`, intents comerciales, o acciones `ConversationNextActionV1`.
- Cambios en `TenantBusinessProfile` o en `isTenantCrewCommercialContextComplete` (`packages/shared/src/tenant-business-profile.ts` en Waseller).
- Nuevos campos obligatorios en el POST shadow-compare.
- Cambio de versión `JOB_SCHEMA_VERSION` o `schemaVersion` del payload HTTP.
- Cambios en **`TenantRuntimeContextV1`** / `loadCrewTenantRuntimeContextForCrewPayload` / `assembleShadowCompareOutboundBody` (Waseller) → revisar `tenant_runtime_context.py`, prompts y [`CONTRATO_HTTP_V1_1.md`](CONTRATO_HTTP_V1_1.md).

---

**Responsabilidad:** mantener alineados el contrato en Waseller (`docs/integrations/waseller-crew/CONTRATO_V1_1.md`), este archivo, y [`CONTRATO_HTTP_V1_1.md`](CONTRATO_HTTP_V1_1.md) al mergear cambios de contrato o de pipeline.
