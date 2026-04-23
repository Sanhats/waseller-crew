# Guía de integración para el equipo de Waseller main

**A quién va dirigido:** desarrolladores del monorepo Waseller que necesitan conectar el sistema actual con `waseller-crew` para mejorar la calidad de las respuestas a leads.

**Qué hace waseller-crew:** recibe el contexto de una conversación (mensaje del lead, inventario, historial reciente, rubro del tenant) y devuelve una respuesta candidata generada por IA orientada a ventas. En modo shadow, Waseller compara esa respuesta con la del sistema base para telemetría y mejora continua.

---

## 1. Variables de entorno — qué agregar en workers

En el archivo de configuración de los workers de Waseller, agregar o verificar estas variables:

```env
# URL del servicio waseller-crew (sin barra al final)
LLM_SHADOW_COMPARE_URL=https://tu-dominio.railway.app/v1/shadow-compare

# Secret compartido con waseller-crew (mismo valor que SHADOW_COMPARE_SECRET en el crew)
LLM_SHADOW_COMPARE_SECRET=un-secret-largo-y-aleatorio

# Timeout en ms. Con stockTable grande subir a 60000 o más (ver sección 5)
LLM_SHADOW_COMPARE_TIMEOUT_MS=30000
```

> **Importante:** `LLM_SHADOW_COMPARE_SECRET` debe coincidir **exactamente** con `SHADOW_COMPARE_SECRET` en el deploy de waseller-crew. Si no coincide, el crew rechaza el request con HTTP 401.

---

## 2. Qué tiene que enviar Waseller en el body del POST

El servicio espera un JSON con campos obligatorios y opcionales. Los opcionales son los que más impactan en la calidad de las respuestas — cuanto más contexto se envíe, mejor responde el agente.

### 2.1 Campos obligatorios (ya implementados en v1)

```json
{
  "schemaVersion": 1,
  "kind": "waseller.shadow_compare.v1",
  "tenantId": "uuid-del-tenant",
  "leadId": "uuid-del-lead",
  "incomingText": "mensaje exacto del cliente",
  "interpretation": { /* ConversationInterpretationV1 */ },
  "baselineDecision": { /* LlmDecisionV1 */ }
}
```

Estos ya se envían. No requieren cambios.

### 2.2 Campos opcionales — críticos para mejorar respuestas

Estos campos **no son opcionales en la práctica**: sin ellos el agente trabaja a ciegas y la calidad de respuesta baja significativamente.

#### `recentMessages` — historial de la conversación

```json
"recentMessages": [
  { "direction": "outgoing", "message": "Hola, ¿en qué te ayudo?" },
  { "direction": "incoming", "message": "¿Tenés la remera en talle M?" },
  { "direction": "outgoing", "message": "Sí, la remera negra talle M sale $12.990. ¿Te la reservo?" },
  { "direction": "incoming", "message": "¿Tenés en otro color?" }
]
```

- Enviar los últimos **8 mensajes** como máximo (el crew descarta el resto).
- El orden es cronológico: el mensaje más antiguo primero, el más nuevo al final.
- `direction`: `"outgoing"` = mensajes que envió el sistema/vendedor; `"incoming"` = mensajes del lead.
- **Por qué importa:** sin este campo, el agente no sabe qué se dijo antes y puede repetir la misma respuesta cuando el lead pide una variante (color, talle, modelo). El campo `incomingText` solo trae el último mensaje, no el contexto previo.

#### `stockTable` — inventario filtrado para este lead

```json
"stockTable": [
  {
    "variantId": "uuid",
    "productId": "uuid",
    "name": "Remera básica",
    "sku": "REM-BLK-M",
    "attributes": { "talle": "M", "color": "negro" },
    "stock": 4,
    "reservedStock": 0,
    "availableStock": 4,
    "effectivePrice": 12990,
    "basePrice": 12990,
    "variantPrice": null,
    "isActive": true
  }
]
```

- El shape es el mismo que devuelve `GET /products` de Waseller — no hay que transformar nada.
- Enviar **solo las variantes relevantes** para la conversación actual (producto que preguntó el lead, variantes del mismo producto, alternativas cercanas). No enviar todo el catálogo.
- Máximo **500 filas** (el crew trunca si hay más).
- **Por qué importa:** el agente usa `stockTable` para cotizar, informar disponibilidad, detectar variantes, generar urgencia cuando el stock es bajo (`availableStock` entre 1 y 3) y ofrecer cross-sell con productos reales del listado.

#### `businessProfileSlug` — rubro del tenant

```json
"businessProfileSlug": "indumentaria_calzado"
```

- Indica el tipo de negocio del tenant. El crew carga instrucciones de venta específicas para ese rubro.
- Es un string con el slug del rubro (letras, números, guiones y puntos, máximo 64 caracteres).
- Se configura una vez por tenant, no por request.
- **Ver sección 3** para la tabla de slugs disponibles.

#### `inventoryNarrowingNote` — por qué se acotó el inventario

```json
"inventoryNarrowingNote": "Solo variantes activas con stock > 0, filtradas por producto consultado por el lead"
```

- Texto libre que explica al agente cómo y por qué se filtró `stockTable`.
- Útil cuando el inventario completo tiene muchos productos pero se manda solo un subconjunto.
- Ejemplo: `"Variantes del producto 'Remera básica' con stock disponible y precio activo"`.

#### `correlationId`, `messageId`, `conversationId`

```json
"correlationId": "uuid",
"messageId": "uuid",
"conversationId": "uuid"
```

- Se usan para correlacionar logs entre Waseller y waseller-crew.
- Recomendados para debugging en producción. No afectan la calidad de la respuesta.

#### `publicCatalogSlug` y `publicCatalogBaseUrl` — enlace al catálogo público

En base de datos el segmento del path viene de **`public.tenants.public_catalog_slug`** (Prisma: `Tenant.publicCatalogSlug`). El enlace literal que debe recibir el lead (y que usa waseller-crew al armar el texto) es:

**`publicCatalogBaseUrl + "/tienda/" + publicCatalogSlug`**

con `publicCatalogBaseUrl` **sin** barra final (misma convención que en Stock: `window.location.origin` + `"/tienda/"` + slug).

**En Waseller (monorepo main):** quedó exportada **`resolvePublicCatalogBaseUrlForCrew()`** para resolver el origen del storefront de forma centralizada; podés reutilizarla o testearla al construir el body del POST.

**Modelo cliente en Waseller:** pueden seguir usando **`extra = "ignore"`** (o equivalente) hasta que agreguen `publicCatalogSlug` y `publicCatalogBaseUrl` al modelo Pydantic/TypeScript del payload; mientras no los envíen, waseller-crew igual acepta el POST y solo no tendrá enlace explícito en los cierres de catálogo. **waseller-crew** ya valida estos campos en su `ShadowCompareRequest` y sigue con `extra = "ignore"` para cualquier otra clave futura.

Enviá ambos campos cuando el worker tenga slug + origen:

```json
"publicCatalogSlug": "mi-tienda-ejemplo",
"publicCatalogBaseUrl": "https://app.midominio.com"
```

- **Por qué importa:** en rechazos (“no gracias”) o derivación a asesor, el crew puede **cerrar con tono conclusivo** e **invitar al lead** al catálogo público con ese **enlace pegable**.
- Si solo enviás **`publicCatalogSlug`**, el texto puede mencionar la ruta `/tienda/{slug}` sin inventar el dominio.
- Slug y URL base se validan en el crew (caracteres seguros, `http(s)://` para la base). Valores inválidos se **descartan** sin fallar el POST.

---

## 3. Configuración de `businessProfileSlug` por tenant

El slug le dice al agente qué tipo de negocio es el tenant para adaptar el tono, las técnicas de venta y el vocabulario. Se configura en el perfil del tenant en Waseller y se envía en cada request.

### Slugs disponibles hoy

| Slug | Rubro | Qué hace el agente |
|------|-------|-------------------|
| `indumentaria_calzado` | Ropa y calzado | Maneja talles, colores, precios con formato $, cierre rápido, urgencia por unidades |
| `muebles_deco` | Muebles y decoración | Da dimensiones, materiales, maneja ciclo de decisión largo, coordina flete |
| `repuestos_lubricentro` | Autopartes y lubricentros | Pide marca/modelo/año del vehículo, verifica compatibilidad, urgencia por auto parado |
| *(sin slug o slug no reconocido)* | Cualquier rubro | Aplica solo la guía de ventas global (funciona para cualquier negocio) |

### Cómo asignar el slug a un tenant

1. En la configuración del tenant en Waseller, guardar el valor del slug en el campo correspondiente (ej. `businessProfileSlug` o equivalente en el modelo de tenant).
2. Al construir el body del POST a waseller-crew, leer ese campo del tenant y enviarlo.
3. Si el tenant no tiene slug configurado, no enviar el campo (o enviar `null`) — el agente usa la guía global igualmente.

### Agregar un rubro nuevo

Si un tenant tiene un rubro que no está en la lista, avisar al equipo de waseller-crew para que creen el archivo de prompt correspondiente. El proceso es:
1. Definir el slug (ej. `gastronomia`, `electronica`, `cosmetica`).
2. El equipo de waseller-crew crea `tenant_prompts/<slug>.txt` con las instrucciones específicas.
3. Configurar el slug en el tenant en Waseller — listo, sin redeploy.

---

## 4. Cómo construir `stockTable` correctamente

### Regla principal: filtrar antes de enviar

No enviar todo el catálogo. El agente trabaja mejor con pocas filas relevantes que con 500 filas de productos sin relación.

**Estrategia recomendada:**

1. Si el lead preguntó por un producto específico: enviar todas las variantes de ese producto (colores, talles, modelos disponibles).
2. Si el lead hizo una consulta genérica (ej. "¿qué remeras tienen?"): enviar las variantes activas con stock de esa categoría, máximo 20-30 filas.
3. Si hay productos similares como alternativa: incluirlos (máximo 2-3 productos alternativos).
4. Excluir siempre: variantes sin stock (`availableStock = 0`), variantes inactivas (`isActive = false`), productos de otra categoría.

### Campos más importantes para el agente

| Campo | Por qué importa |
|-------|----------------|
| `name` | El agente lo usa para nombrar el producto en la respuesta |
| `attributes` | Colores, talles, modelos — el agente los usa para responder variantes |
| `availableStock` | Si es 1-3, el agente genera urgencia natural ("quedan pocas unidades") |
| `effectivePrice` | Precio final que el cliente paga — prioridad sobre `basePrice` |
| `sku` | Útil para búsquedas internas, no se muestra al cliente |

### `inventoryNarrowingNote`: cuándo usarla

Usar este campo cuando la razón del filtro ayuda al agente a no asumir que "eso es todo el stock":

```
// Bien: le aclara al agente que hay más productos pero se filtró
"inventoryNarrowingNote": "Variantes del producto consultado con stock > 0. El catálogo completo tiene más de 200 productos."

// Bien: le aclara que el filtro fue intencional
"inventoryNarrowingNote": "Solo remeras activas talle M y L, que es lo que preguntó el lead."
```

---

## 5. Timeout: cuánto configurar

El tiempo de respuesta de waseller-crew depende del LLM. Con `stockTable` grande (cerca de 500 filas), el prompt es más grande y el LLM tarda más.

| Tamaño de `stockTable` | Timeout recomendado |
|------------------------|-------------------|
| 0-20 filas | 15.000 ms (15 s) |
| 20-100 filas | 30.000 ms (30 s) |
| 100-500 filas | 60.000 ms (60 s) |

En modo shadow el timeout no afecta al lead: si waseller-crew no responde a tiempo, Waseller ignora la respuesta y sigue con el baseline. No hay impacto en la experiencia del cliente.

---

## 6. Cómo usar la respuesta de waseller-crew

El servicio devuelve:

```json
{
  "candidateDecision": {
    "draftReply": "Respuesta propuesta por el agente IA",
    "intent": "consultar_precio",
    "nextAction": "offer_reservation",
    "recommendedAction": "offer_reservation",
    "confidence": 0.87,
    "reason": "stock_disponible|cierre_activo"
  },
  "candidateInterpretation": {
    "intent": "consultar_precio",
    "confidence": 0.9,
    "nextAction": "offer_reservation",
    "source": "openai",
    "conversationStage": "waiting_variant"
  }
}
```

### En modo shadow (actual)

- `candidateDecision` se almacena como telemetría.
- Waseller calcula el diff con `baselineDecision` (si `draftReply`, `intent`, `nextAction` coinciden o difieren).
- El cliente **no recibe** el `draftReply` del crew — recibe la respuesta del sistema base de Waseller.
- Usar el diff para medir cuánto mejora el crew respecto al baseline.

### En modo primary (fase siguiente)

- Cuando las métricas de diff muestran que el crew supera al baseline consistentemente, evaluar usar `candidateDecision.draftReply` como la respuesta real al lead.
- Esto requiere decisión de producto, no solo técnica.

### Campos a persistir en la traza

Guardar al menos: `candidateDecision.draftReply`, `candidateDecision.nextAction`, `candidateDecision.confidence`, `candidateDecision.reason`, `candidateInterpretation` completo si viene.

---

## 7. Checklist de implementación

### Variables de entorno en workers
- [ ] `LLM_SHADOW_COMPARE_URL` apunta a la URL correcta del deploy (HTTPS, con path `/v1/shadow-compare`)
- [ ] `LLM_SHADOW_COMPARE_SECRET` configurado e igual a `SHADOW_COMPARE_SECRET` en waseller-crew
- [ ] `LLM_SHADOW_COMPARE_TIMEOUT_MS` ajustado según tamaño esperado de `stockTable`

### Body del request
- [ ] `recentMessages` se envía con los últimos 8 mensajes de la conversación
- [ ] `interpretation` se envía con la lectura OpenAI/reglas de Waseller (intent, entidades, `nextAction` sugerido, etc.)
- [ ] `baselineDecision` completo para merge y fallback de `draftReply`
- [ ] `stockTable` se envía filtrado (solo variantes relevantes con stock > 0)
- [ ] `businessProfileSlug` se envía con el slug del rubro del tenant
- [ ] `inventoryNarrowingNote` se incluye cuando el inventario fue filtrado
- [ ] Opcionales alineados al contrato: `tenantCommercialContext`, `tenantBrief`, `etapa`, `activeOffer`, `memoryFacts`, `publicCatalogSlug`, `publicCatalogBaseUrl` (catálogo público `/tienda/{slug}`; ver `docs/CONTRATO_HTTP_V1_1.md` y `docs/integrations/waseller-crew/README.md`)

### Configuración por tenant
- [ ] Cada tenant tiene asignado su `businessProfileSlug` en Waseller
- [ ] Los slugs usados existen en waseller-crew (verificar con el equipo de crew)

### Verificación
- [ ] Smoke test con `fixtures/request.v1_1.example.json` contra el endpoint de producción
- [ ] Verificar en logs de waseller-crew que llegan eventos `shadow_compare_completed`
- [ ] Verificar que `correlation_id` en logs de crew coincide con el de Waseller para correlacionar trazas

---

## 8. Derivación, negación al cierre y catálogo (crew + qué hacer en main)

waseller-crew ajustó **prompt** y **guards** post-LLM para estos casos:

| Situación | `nextAction` típico en la respuesta candidata | Comportamiento esperado del mensaje al lead |
|-----------|-----------------------------------------------|-----------------------------------------------|
| El lead pide **asesor / persona / derivación** | `handoff_human` | Confirmar derivación **sin** repetir la ficha ni insistir con reserva; invitar a seguir explorando el catálogo con criterios (rubro, nombre, palabras clave), honesto respecto al subconjunto enviado en `stockTable`. |
| El lead **niega** la reserva o el cierre (“no”, “no gracias”, etc.) y el borrador seguía empujando reserva | `reply_only` (o el que defina el merge) | **No** repetir el mismo “¿te reservo?”; respuesta breve + invitación a seguir el catálogo con búsqueda; sin inventar productos fuera de `stockTable`. |

**Variables de entorno opcionales en waseller-crew** (por defecto activas): `CREW_SHADOW_HANDOFF_REQUEST_GUARD`, `CREW_SHADOW_NEGATION_FOLLOWUP_GUARD`. Solo hace falta tocarlas si querés desactivar el comportamiento en un entorno de prueba.

### Texto listo para pegar en un issue/PR de Waseller main

> **Integración shadow-compare / waseller-crew:** el crew ya devuelve `handoff_human` cuando el mensaje del lead pide hablar con un asesor o derivación, y refuerza el corte cuando el lead niega el cierre y el modelo insistía con la misma reserva. **En main no hace falta cambiar el contrato HTTP** si ya envían `incomingText`, `recentMessages` (últimos 8) y `stockTable` como en esta guía.
>
> **Sí conviene revisar en main:**
> 1. **Consumo de `nextAction` / `recommendedAction`:** si Waseller hoy ignora `handoff_human` y siempre manda la respuesta candidata como mensaje automático, alinear la lógica para que **`handoff_human` dispare** la cola o flujo de **contacto humano** (o marque el lead para operador), en lugar de tratarlo como un mensaje de venta más.
> 2. **Merge baseline vs candidata:** si al comparar o elegir respuesta se prioriza siempre el baseline cuando la candidata pide handoff, evaluar **dar prioridad a la candidata** cuando `nextAction === "handoff_human"` o cuando el texto del lead matchea pedido explícito de asesor.
> 3. **Telemetría:** registrar en logs/analytics el par `(nextAction, recommendedAction)` de la respuesta aplicada al lead para medir cuántos pedidos de derivación se cumplen end-to-end.

Si no tocan nada de lo anterior, el texto al lead igual mejora porque el **cuerpo del mensaje** ya viene alineado; el riesgo es que **operación** (derivar a humano) no se ejecute si main no interpreta `handoff_human`.

---

## 9. Ejemplos de body completo por rubro

### Indumentaria y calzado

```json
{
  "schemaVersion": 1,
  "kind": "waseller.shadow_compare.v1",
  "tenantId": "uuid-tenant",
  "leadId": "uuid-lead",
  "correlationId": "uuid-correlacion",
  "incomingText": "¿Tenés en otro color?",
  "businessProfileSlug": "indumentaria_calzado",
  "recentMessages": [
    { "direction": "incoming", "message": "¿Cuánto sale la remera negra talle M?" },
    { "direction": "outgoing", "message": "La remera negra talle M sale $12.990. ¿Te la reservo?" }
  ],
  "inventoryNarrowingNote": "Variantes de 'Remera básica' con stock activo",
  "stockTable": [
    {
      "name": "Remera básica",
      "sku": "REM-BLK-M",
      "attributes": { "talle": "M", "color": "negro" },
      "availableStock": 4,
      "effectivePrice": 12990
    }
  ],
  "interpretation": { "intent": "consultar_variante", "nextAction": "ask_clarification", "confidence": 0.9, "source": "openai" },
  "baselineDecision": { "draftReply": "La remera negra talle M sale $12.990. ¿Te la reservo?", "nextAction": "reply_only", "confidence": 0.7, "reason": "baseline_waseller" }
}
```

### Muebles y decoración

```json
{
  "schemaVersion": 1,
  "kind": "waseller.shadow_compare.v1",
  "tenantId": "uuid-tenant",
  "leadId": "uuid-lead",
  "incomingText": "¿Cuánto mide el sillón y en qué colores viene?",
  "businessProfileSlug": "muebles_deco",
  "recentMessages": [
    { "direction": "incoming", "message": "Hola, vi el sillón esquinero en la web" },
    { "direction": "outgoing", "message": "¡Hola! El sillón esquinero sale $280.000. ¿Querés más info?" }
  ],
  "inventoryNarrowingNote": "Variantes del sillón esquinero consultado",
  "stockTable": [
    { "name": "Sillón esquinero Oslo", "attributes": { "color": "gris", "material": "ecocuero", "largo_cm": 280, "ancho_cm": 160, "alto_cm": 85 }, "availableStock": 2, "effectivePrice": 280000 },
    { "name": "Sillón esquinero Oslo", "attributes": { "color": "beige", "material": "ecocuero", "largo_cm": 280, "ancho_cm": 160, "alto_cm": 85 }, "availableStock": 1, "effectivePrice": 280000 }
  ],
  "interpretation": { "intent": "consultar_variante", "nextAction": "reply_only", "confidence": 0.85, "source": "openai" },
  "baselineDecision": { "draftReply": "Viene en gris y beige, mide 280×160×85 cm.", "nextAction": "reply_only", "confidence": 0.65, "reason": "baseline_waseller" }
}
```

### Repuestos y lubricentro

```json
{
  "schemaVersion": 1,
  "kind": "waseller.shadow_compare.v1",
  "tenantId": "uuid-tenant",
  "leadId": "uuid-lead",
  "incomingText": "¿Tienen filtro de aceite para un Gol Trend 2015?",
  "businessProfileSlug": "repuestos_lubricentro",
  "recentMessages": [
    { "direction": "incoming", "message": "Buenas, necesito un filtro de aceite" },
    { "direction": "outgoing", "message": "¡Hola! ¿Para qué auto es? Así te confirmo compatibilidad." }
  ],
  "inventoryNarrowingNote": "Filtros de aceite con stock, compatibles con VW familia Gol según catálogo",
  "stockTable": [
    { "name": "Filtro aceite Mann W712/95", "sku": "FILT-MANN-W71295", "attributes": { "marca": "Mann", "referencia": "W712/95", "compatible": "VW Gol, Polo, Fox 1.6" }, "availableStock": 8, "effectivePrice": 4200 },
    { "name": "Filtro aceite Mahle OC1019", "sku": "FILT-MAHLE-OC1019", "attributes": { "marca": "Mahle", "referencia": "OC1019", "compatible": "VW Gol Trend 1.6 2008-2017" }, "availableStock": 3, "effectivePrice": 5100 }
  ],
  "interpretation": { "intent": "consultar_repuesto", "nextAction": "reply_only", "confidence": 0.88, "source": "openai" },
  "baselineDecision": { "draftReply": "Sí tenemos filtros de aceite para el Gol Trend. El Mann W712/95 sale $4.200 y el Mahle OC1019 $5.100. ¿Cuál preferís?", "nextAction": "ask_clarification", "confidence": 0.7, "reason": "baseline_waseller" }
}
```

---

## 10. Contacto y coordinación

Para agregar un rubro nuevo, reportar un problema con la respuesta del agente o ajustar la guía de ventas de un tenant específico, coordinar con el equipo de waseller-crew indicando:

1. **Rubro / slug** que necesitan crear o modificar.
2. **Ejemplo de conversación** donde la respuesta no fue la esperada (con `incomingText`, `recentMessages` y `stockTable`).
3. **Qué debería haber respondido** el agente en ese caso.

Con esa información el equipo puede ajustar el prompt del rubro sin necesidad de tocar código.
