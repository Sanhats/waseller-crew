"""
Lógica CrewAI + fallback stub.

Resumen CrewAI (lo que vas a ver en el código de abajo):
- **Agent**: un “rol” con goal, backstory y opciones (verbose, tools). Es quien “piensa” con el LLM.
- **Task**: una instrucción concreta para un agente; puede usar placeholders `{nombre}` que llenás
  con el dict `inputs` de `crew.kickoff(inputs={...})`.
- **Crew**: agrupa agentes y tareas y define **Process** (casi siempre `sequential`: tarea 1, luego 2…).
- **kickoff()**: ejecuta el crew y devuelve un resultado; en la práctica suele ser texto (a veces con
  markdown). Por eso parseamos JSON del string final.
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any

from crewai import Agent, Crew, LLM, Process, Task

from crew_shadow_crewai.constants import (
    CONVERSATION_STAGE_ENUM_DOC,
    INTERPRETATION_SOURCE_ENUM_DOC,
    NEXT_ACTION_ENUM_DOC,
)
from crew_shadow_crewai.draft_variant_guard import apply_followup_draft_guards, public_catalog_full_url
from crew_shadow_crewai.models import (
    CandidateDecision,
    CandidateInterpretation,
    ShadowCompareRequest,
    ShadowCompareResponse,
)
from crew_shadow_crewai.observability import structured_log_line
from crew_shadow_crewai.openai_env import effective_normalized_openai_api_key

log = logging.getLogger(__name__)


def _default_tenant_prompts_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "tenant_prompts"


def _prompt_base_dir() -> Path:
    raw = os.environ.get("CREW_TENANT_PROMPTS_DIR", "").strip()
    return Path(raw).expanduser() if raw else _default_tenant_prompts_dir()


def _load_global_prompt_overlay() -> str:
    """Guía de ventas global (_global.txt). Se aplica a todos los tenants siempre."""
    path = _prompt_base_dir() / "_global.txt"
    if not path.is_file():
        return ""
    try:
        text = path.read_text(encoding="utf-8").strip()
        if not text:
            return ""
        return f"\n\n=== Guía de ventas global ===\n{text}\n=== Fin guía global ===\n"
    except OSError:
        log.warning("No se pudo leer overlay global: %s", path)
        return ""


def _load_tenant_prompt_overlay(slug: str | None) -> str:
    """Overlay específico por rubro desde tenant_prompts/<slug>.txt. Layerea sobre el global."""
    if not slug:
        return ""
    path = _prompt_base_dir() / f"{slug}.txt"
    if not path.is_file():
        return ""
    try:
        text = path.read_text(encoding="utf-8").strip()
        if not text:
            return ""
        return (
            f"\n\n=== Instrucciones del perfil comercial ({slug}) ===\n{text}\n"
            "=== Fin perfil ===\n"
        )
    except OSError:
        log.warning("No se pudo leer overlay de tenant: %s", path)
        return ""


def _sales_and_stock_rules(body: ShadowCompareRequest) -> str:
    """Reglas fijas: vendedor del tenant + uso honesto de stockTable + ventana de mensajes."""
    rows = body.stockTable
    if rows is None:
        stock_hint = (
            "stockTable no viene en el payload: no afirmes existencias, precios ni SKU concretos "
            "salvo que ya estén redactados de forma explícita en baselineDecision.draftReply "
            "o en interpretation (y aun así no contradigas políticas obvias)."
        )
    elif len(rows) == 0:
        stock_hint = (
            "stockTable viene vacío: tratá el inventario como desconocido; no inventes filas ni "
            "cantidades. Preferí ask_clarification o suggest_alternative."
        )
    else:
        stock_hint = (
            f"stockTable trae {len(rows)} fila(s). Interpretá cada fila usando **solo** las claves y "
            "valores que aparecen en el JSON (es el mismo esquema que Waseller usa en su tabla). "
            "No agregues productos, precios ni stock que no surgan de esas filas o del texto ya "
            "presente en baseline/interpretation. Si incomingText no matchea ninguna fila, no inventes: "
            "pedí datos o ofrecé alternativas alineadas a filas existentes."
        )
    profile = (
        f" Perfil comercial (businessProfileSlug): {body.businessProfileSlug}."
        if body.businessProfileSlug
        else ""
    )
    overlay = _load_global_prompt_overlay() + _load_tenant_prompt_overlay(body.businessProfileSlug)
    narrow = (body.inventoryNarrowingNote or "").strip()
    narrow_block = ""
    if narrow:
        narrow_block = (
            f"\n- **PRIORIDAD — Nota de acotación de inventario (Waseller):** {narrow[:4000]}\n"
            "  Leela **antes** de interpretar stockTable: delimita alcance (un producto, una fila, RAG, "
            "filtros, catálogo parcial, etc.). Tu respuesta debe ser coherente con ese alcance; si el lead "
            "pide “todo el catálogo” y la nota aclara que el payload es acotado, **no inventes** listados "
            "ni otros productos: explicá el alcance y pedí criterio de búsqueda o palabras clave.\n"
        )
    else:
        narrow_block = (
            "\n- **Acotación de inventario:** Si no hay nota explícita, igual asumí que stockTable puede "
            "ser un subconjunto del catálogo real; no afirmes que tenés “todos los productos” si solo "
            "ves pocas filas.\n"
        )
    return (
        "\n## Rol, tenant e inventario\n"
        f"- Actuás como **asistente de ventas del negocio** identificado por tenantId={body.tenantId} "
        f"en el JSON de contexto.{profile} Tu objetivo principal es **cerrar la venta o avanzar un paso "
        "concreto hacia ella**: cotizar, confirmar variante, ofrecer reserva o generar el link de pago.\n"
        f"{narrow_block}"
        f"- {stock_hint}\n"
        "- **stockTable como fuente de variantes:** Si hay **varias filas** y el lead pregunta por "
        "**color** o **talle/medida**, enumerá las opciones **solo** con valores que aparezcan en las "
        "columnas correspondientes de la tabla (sin inventar tonos o talles). Si hay **una sola fila**, "
        "decí con claridad que en **este** inventario no aparece otra variante distinta; no inventes "
        "stock alternativo fuera de las filas.\n"
        "- **Cantidad vs stock mostrado:** Si en incomingText el lead pide **más unidades** que la "
        "suma o el máximo razonable que se desprende de las columnas de stock en stockTable, "
        "reconocé la diferencia: ofrecé **reservar lo disponible** según la tabla y, según el rubro "
        "(repuesto, indumentaria, mueblería, etc.), mencioná **asesor humano** o **reposición** sin "
        "prometer plazos ni cantidades que no estén en datos.\n"
        "- Usá incomingText como mensaje actual del lead y recentMessages (si hay) como contexto "
        "reciente; no ignores contradicciones entre mensajes.\n"
        "- **Interpretación Waseller + lectura integral del lead:** El objeto **interpretation** del JSON "
        "(intención, entidades, nextAction sugerido, campos faltantes, etc.) suele venir de **OpenAI o reglas** "
        "en Waseller: usalo como **señal semántica del turno**, junto con baselineDecision y el perfil de rubro. "
        "El lead puede responder de muchas formas (agradecimiento, rechazo, cambio de tema, ironía, pedido "
        "mezclado, aclaración vaga): leé **todo** el contexto y contestá a lo esencial de **este** mensaje. "
        "Si interpretation y el texto discrepan, priorizá el texto del lead y el hilo. "
        "**Hechos duros** (precio, cantidades, existencias, SKU): solo si salen de **stockTable** o del "
        "baseline de forma inequívoca; interpretation **no** autoriza inventar filas ni stock.\n"
        "- **tenantBrief, etapa, activeOffer, memoryFacts:** Si el JSON los trae, usalos para **embudo, "
        "última oferta y hechos del lead** junto con recentMessages; no contradigan stockTable. "
        "Si activeOffer y la tabla discrepan en precio/stock, **gana stockTable**.\n"
        "- **Seguimiento (obligatorio):** Si incomingText pide **otro color**, **otro talle**, **otro modelo**, "
        "**otra medida**, **más unidades**, **otro producto**, **catálogo**, **envío**, etc., tu **primer "
        "párrafo** debe contestar eso. "
        "Revisá **todas** las filas de stockTable (mismo producto u otros) y listá **solo** variantes que "
        "aparezcan en datos (colores/talles distintos en otras filas). Si **ninguna** otra fila trae otro "
        "color/talle, decí explícitamente que en el inventario enviado **solo figura** esa variante "
        "(nombrala una vez) y ofrecé ayuda (otro producto del listado, reserva, tienda física, etc.). "
        "**Prohibido** responder solo re-enviando la misma ficha de producto del mensaje anterior.\n"
        "- **Catálogo / “qué más tenés” con payload acotado:** Si stockTable tiene **pocas filas o una** "
        "y el lead pide catálogo u otros productos, **no inventes** listados: explicá que tu vista es la "
        "de la tabla enviada, citá inventoryNarrowingNote si aplica, y pedí **criterio de búsqueda** "
        "(nombre, rubro, presupuesto) para poder ayudar en el próximo turno.\n"
        "- **Anti-repetición (dura):** Compará candidateDecision/baselineDecision.draftReply y los "
        "mensajes del asistente en recentMessages. Si el lead **cambia de tema** respecto al cierre del "
        "turno anterior (color, talle, cantidad, otro producto, catálogo, envío, etc.) y tu borrador sería "
        "**casi el mismo texto** (misma ficha, mismo precio, mismo cierre) que el último **outgoing** del "
        "asistente: **fallaste** — reescribí desde cero contestando lo nuevo; no repitas el cierre del "
        "baseline si solo reenvía la misma oferta. Si el lead pidió variante y tu borrador repite la ficha: "
        "listado de otras filas o negativa clara, sin copiar el bloque anterior.\n"
        "- **Urgencia y escasez (natural):** Si una fila de stockTable tiene `availableStock` o `stock` "
        "entre 1 y 3, podés mencionarlo de forma natural ('quedan pocas unidades', 'son las últimas que "
        "tengo en ese talle') para motivar la decisión. No exageres ni inventes cantidades fuera del dato.\n"
        "- **Cierre activo:** Cuando el producto está disponible y el lead muestra interés, **terminá** "
        "con una pregunta de cierre o CTA: '¿Te lo reservo?', '¿Armamos el pedido?', "
        "'¿Querés que lo aparte?'. Usá nextAction `offer_reservation` o `reserve_stock` según el caso. "
        "No termines el mensaje sin un paso concreto propuesto **salvo** que aplique la excepción siguiente.\n"
        "- **Excepción — derivación o negativa al cierre:** Si el lead pide **asesor**, **persona humana** "
        "o **derivación**, o **rechaza** la reserva o la pregunta de cierre ('no', 'no gracias', 'no gracias!', "
        "'gracias no', 'no quiero', etc.): **no** repitas la misma ficha de producto ni el mismo CTA de reserva. "
        "En derivación usá "
        "`handoff_human` y `recommendedAction` acorde; en rechazo claro usá `reply_only` o `ask_clarification`. "
        "Cerrá con **invitación a seguir explorando el catálogo**: pedí rubro, nombre o palabras clave; si en "
        "stockTable hay **otras filas** distintas, podés mencionar que en este envío hay más líneas para que "
        "elija; **no inventes** productos fuera de la tabla. Si el JSON trae `publicCatalogSlug` y "
        "`publicCatalogBaseUrl`, el catálogo público en la web es `{publicCatalogBaseUrl}/tienda/{publicCatalogSlug}` "
        "(misma regla que la app); si solo viene el slug, referí la ruta /tienda/{slug} sin inventar el dominio.\n"
        "- **Cross-sell:** Si el producto exacto no está en stockTable pero hay alternativas similares "
        "(mismo rubro, precio cercano, otro color/talle disponible), ofrecelas con nombre y precio real. "
        "Usá `suggest_alternative` y listá máximo 2 opciones concretas de stockTable.\n"
        "- nextAction / recommendedAction deben seguir el vocabulario Waseller ya indicado abajo.\n"
        f"{overlay}"
    )


def _shadow_crew_llm() -> LLM:
    """
    API key explícita (además de os.environ) para evitar desalineación con el provider OpenAI de CrewAI.
    """
    api_key, _src = effective_normalized_openai_api_key()
    if api_key:
        os.environ["OPENAI_API_KEY"] = api_key
    model_raw = (os.environ.get("OPENAI_MODEL_NAME") or "gpt-4o-mini").strip()
    model = model_raw if "/" in model_raw else f"openai/{model_raw}"
    base = (os.environ.get("OPENAI_BASE_URL") or os.environ.get("OPENAI_API_BASE") or "").strip()
    params: dict[str, Any] = {
        "model": model,
        "api_key": api_key,
        "temperature": 0.2,
    }
    if base:
        params["base_url"] = base
    return LLM(**params)


def _use_crew_stub() -> bool:
    """Leer en cada request: en Railway/Ops a veces se cambia env y se reinicia tarde el proceso."""
    return os.environ.get("USE_CREW_STUB", "").strip().lower() in ("1", "true", "yes")


def _stub_interpretation(body: ShadowCompareRequest) -> CandidateInterpretation | None:
    raw = body.interpretation
    if not isinstance(raw, dict) or not raw:
        return None
    try:
        return CandidateInterpretation.model_validate(raw)
    except Exception:
        log.debug("stub: interpretation no mapeable a CandidateInterpretation", exc_info=True)
        return None


def _stub_response(body: ShadowCompareRequest) -> ShadowCompareResponse:
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
        ),
        candidateInterpretation=_stub_interpretation(body),
    )


def _json_from_crew_output(text: str) -> dict[str, Any]:
    """Extrae el primer objeto JSON del texto del agente (quita fences ```json si vienen)."""
    s = text.strip()
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*", "", s, flags=re.IGNORECASE)
        s = re.sub(r"\s*```\s*$", "", s)
    start, end = s.find("{"), s.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("No se encontró un objeto JSON en la salida del crew")
    return json.loads(s[start : end + 1])


def _enrich_empty_draft_reply(resp: ShadowCompareResponse, body: ShadowCompareRequest) -> ShadowCompareResponse:
    """
    Modo primary-friendly: Waseller espera candidateDecision.draftReply no vacío cuando hay baseline.
    Si el LLM dejó draft vacío, completamos desde baseline (telemetría sigue siendo útil en diff).
    """
    cd = resp.candidateDecision
    if cd is None:
        return resp
    if (cd.draftReply or "").strip():
        return resp
    bl = body.baselineDecision if isinstance(body.baselineDecision, dict) else {}
    fallback = str(bl.get("draftReply") or "").strip()
    if not fallback:
        log.warning(
            structured_log_line(
                "shadow_compare_empty_draft_no_fallback",
                tenant_id=body.tenantId,
                lead_id=body.leadId,
            )
        )
        return resp
    log.warning(
        structured_log_line(
            "shadow_compare_empty_draft_filled_from_baseline",
            tenant_id=body.tenantId,
            lead_id=body.leadId,
        )
    )
    return ShadowCompareResponse(
        candidateDecision=cd.model_copy(update={"draftReply": fallback[:2000]}),
        candidateInterpretation=resp.candidateInterpretation,
    )


def _finalize_shadow_response(body: ShadowCompareRequest, resp: ShadowCompareResponse) -> ShadowCompareResponse:
    """Misma cadena de guards post-borrador en stub, LLM ok o fallback por error."""
    resp = apply_followup_draft_guards(body, resp)
    return _enrich_empty_draft_reply(resp, body)


def _shadow_response_from_crew_dict(data: dict[str, Any]) -> ShadowCompareResponse:
    """
    Acepta:
    - JSON anidado: { "candidateDecision": {...}, "candidateInterpretation": {...}|null }
    - JSON plano (legado): solo campos de decisión en la raíz.
    """
    has_nested = ("candidateDecision" in data) or ("candidateInterpretation" in data)
    if has_nested:
        raw_dec = data.get("candidateDecision")
        raw_int = data.get("candidateInterpretation")
        cand_dec: CandidateDecision | None = None
        if isinstance(raw_dec, dict) and raw_dec:
            cand_dec = CandidateDecision.model_validate(raw_dec)
        cand_int: CandidateInterpretation | None = None
        if isinstance(raw_int, dict) and raw_int:
            cand_int = CandidateInterpretation.model_validate(raw_int)
        return ShadowCompareResponse(
            candidateDecision=cand_dec,
            candidateInterpretation=cand_int,
        )
    cand_dec = CandidateDecision.model_validate(data)
    return ShadowCompareResponse(
        candidateDecision=cand_dec,
        candidateInterpretation=None,
    )


def _waseller_negotiation_context_block(body: ShadowCompareRequest) -> str:
    """Bloque explícito: etapa, resumen tenant, oferta activa, hechos en memoria (Waseller)."""
    parts: list[str] = []
    et = (body.etapa or "").strip()
    if et:
        parts.append(f"**Etapa (Waseller):** {et}")
    tb = (body.tenantBrief or "").strip()
    if tb:
        parts.append(f"**Resumen tenant / brief:**\n{tb[:2200]}")
    if body.activeOffer and isinstance(body.activeOffer, dict) and body.activeOffer:
        ao = json.dumps(body.activeOffer, ensure_ascii=False, indent=2)[:2800]
        parts.append(f"**activeOffer (última oferta / deal; JSON):**\n{ao}")
    if body.memoryFacts:
        lines = "\n".join(f"- {x}" for x in (body.memoryFacts or [])[:32])
        parts.append(f"**memoryFacts:**\n{lines}")
    if not parts:
        return ""
    return (
        "\n## Contexto de negociación Waseller\n"
        "Combiná este bloque con **recentMessages** e **interpretation**. Reflejá la **última pregunta del "
        "cliente** y la **última oferta** del asistente sin repetir un cierre genérico si el lead cambió de eje "
        "(color, envío, otro producto, etc.). Si algo aquí contradice **stockTable**, prevalece la tabla.\n\n"
        + "\n\n".join(parts)
        + "\n\n"
    )


def _interpretation_priority_banner(body: ShadowCompareRequest) -> str:
    """Instrucciones explícitas cuando Waseller envía interpretation (OpenAI / reglas)."""
    interp = body.interpretation if isinstance(body.interpretation, dict) else {}
    if not interp:
        return ""
    return (
        "\n## Interpretación Waseller (`interpretation` en el JSON)\n"
        "Waseller puede rellenar este objeto con **intención, entidades, nextAction sugerido, confianza, "
        "campos faltantes**, etc. (p. ej. vía OpenAI). Tratálo como **capa de comprensión del turno**: "
        "combiná lo que diga ahí con **incomingText** y **recentMessages**. "
        "Si hay conflicto entre interpretation y el texto libre del lead, **gana el texto y el hilo**. "
        "Para **precios, stock, SKU y productos concretos**, la verdad operativa sigue siendo **stockTable** "
        "(y baseline cuando no contradiga la tabla). interpretation **no** reemplaza inventario: no inventes "
        "filas ni cantidades solo porque un campo sugiere algo que no está en datos.\n\n"
    )


def _public_catalog_prompt_note(body: ShadowCompareRequest) -> str:
    """Instrucción explícita para usar catálogo público (slug + origen) sin inventar URLs."""
    slug = body.publicCatalogSlug
    base = body.publicCatalogBaseUrl
    if not slug and not base:
        return ""
    url = public_catalog_full_url(body)
    lines: list[str] = [
        "\n## Catálogo público de la tienda (Waseller)\n",
        "En el JSON pueden venir **`publicCatalogSlug`** (columna `public.tenants.public_catalog_slug` / "
        "Prisma `Tenant.publicCatalogSlug`) y opcionalmente **`publicCatalogBaseUrl`** (origen HTTPS del "
        "storefront **sin** barra final, equivalente a `window.location.origin` en la app).\n",
    ]
    if url:
        lines.append(f"**URL armada** (copiar tal cual al lead, sin modificar): `{url}`.\n")
    elif slug:
        lines.append(
            f"**Solo slug:** la ruta pública es **/tienda/{slug}**; el enlace completo es `{{origen}}/tienda/{slug}` "
            "— no inventes el origen si no viene `publicCatalogBaseUrl`.\n"
        )
    else:
        lines.append(
            "**Solo origen:** sin `publicCatalogSlug` no armes la URL del catálogo; no inventes el segmento.\n"
        )
    lines.append(
        "Si el lead **rechaza** lo ofrecido o pide **derivación**, cerrá con tono conclusivo e invitá a seguir "
        "el **catálogo público** (lo que van cargando) usando el enlace o la regla anterior **solo** si están "
        "en el JSON.\n\n"
    )
    return "".join(lines)


def _tenant_commercial_context_redactor_note(body: ShadowCompareRequest) -> str:
    """
    Instrucción explícita para que el redactor priorice tenantCommercialContext / equivalentes en JSON.
    El texto completo ya va en el payload; acá solo remarcamos prioridad.
    """
    top = (body.tenantCommercialContext or "").strip()
    nested = ""
    interp = body.interpretation if isinstance(body.interpretation, dict) else {}
    for key in ("tenantCommercialContext", "tenantVoiceNote", "commercialContext"):
        v = interp.get(key)
        if isinstance(v, str) and v.strip():
            nested = v.strip()
            break
    if not top and not nested:
        return ""
    return (
        "\n## Contexto comercial extra del tenant (prioridad alta)\n"
        "En el JSON hay `tenantCommercialContext` y/o notas en `interpretation` "
        "(tenantCommercialContext / tenantVoiceNote / commercialContext): usalas como **tono, políticas, "
        "horarios, medios de pago, envíos y límites** del negocio. No contradigan stockTable ni precios "
        "concretos salvo que allí también aparezcan.\n\n"
    )


def _use_conversation_director() -> bool:
    """Tercer agente (plan conversacional). Desactivar con CREW_SHADOW_CONVERSATION_DIRECTOR=0."""
    raw = os.environ.get("CREW_SHADOW_CONVERSATION_DIRECTOR", "").strip().lower()
    if not raw:
        return True
    return raw in ("1", "true", "yes", "on")


_CONVERSATIONAL_FLOW_FOR_REDACTOR = """
## Flujo conversacional (natural)
Clasificá el turno con **incomingText + recentMessages + interpretation** (y conversationStage si viene en interpretation):
- **Saludo / primer contacto:** Breve, humano; si ya preguntan por producto, pasá enseguida a dato útil + un CTA.
- **Seguimiento:** Primero lo que preguntaron (variante, cantidad, aclaración); no repitas toda la ficha si no hace falta.
- **Objeción** (precio, desconfianza, "lo pienso"): reconocé la duda; valor según datos o alternativas en stockTable; cierre suave.
- **Cierre / intención de compra:** Menos charla, más paso concreto (reserva, confirmar variante, link de pago); nextAction acorde.
- **Rechazo o “no”** (a la reserva o a la pregunta que le hicimos): sin insistir ni repetir el mismo cierre; tono **conclusivo** e invitación a seguir el **catálogo público**; si el JSON trae `publicCatalogSlug` y `publicCatalogBaseUrl`, podés pasar el enlace `{publicCatalogBaseUrl}/tienda/{publicCatalogSlug}` (sin inventar dominio ni slug).
- **Derivación / asesor humano:** Reconocé el pedido, `handoff_human` si corresponde, sin volver a empujar la misma reserva; podés combinar con invitación al catálogo público cuando vengan slug/origen en el JSON (sin inventar fuera de stockTable).
- **Cambio de tema / catálogo / “qué más tenés”:** Aclará alcance del inventario enviado y pedí criterio si hace falta; no inventes catálogo.
- **Mensaje ambiguo o multitema:** Una frase de aclaración o priorizá lo más urgente; pedí un solo dato si falta para avanzar.
- **Cortesía o charla lateral breve:** Respondé en una línea y volvé al paso de venta sin alargar.
Si aplica más de uno, priorizá lo explícito en incomingText; usá interpretation para desambiguar cuando el texto sea vago.
"""


def _director_task_description() -> str:
    return (
        "Analizá el contexto Waseller (JSON) del usuario (incomingText, recentMessages, interpretation, "
        "baselineDecision, stockTable, tenantCommercialContext si existe).\n\n"
        "Tu salida es SOLO un objeto JSON (sin markdown) con esta forma exacta:\n"
        "{\n"
        '  "conversationMoment": "greeting" | "follow_up" | "objection" | "closing" | "mixed",\n'
        '  "leadTemperature": "cold" | "warm" | "hot",\n'
        '  "tacticsForRedactor": "2 a 4 frases en español: cómo abrir, qué priorizar y cómo cerrar ESTE turno. '
        'Sin precios, sin SKU, sin inventar stock: solo guía de tono y prioridad."\n'
        "}\n\n"
        "Criterios:\n"
        "- greeting: saludo o arranque sin pedido concreto de producto.\n"
        "- follow_up: refina variante, cantidad, o responde al mensaje anterior del asistente.\n"
        "- objection: duda de precio, plazo, confianza, comparación, indecisión fuerte.\n"
        "- closing: intención clara de compra o reserva.\n"
        "- mixed: mezcla evidente; tacticsForRedactor debe ordenar qué va primero.\n"
        "- Si incomingText pide **asesor/persona**/derivación o es **negativa** clara al cierre previo "
        "(no / no gracias / no quiero reservar): tacticsForRedactor debe priorizar **no insistir** con la "
        "misma ficha ni el mismo CTA de reserva; invitación breve a seguir el catálogo con criterios de búsqueda, "
        "sin inventar inventario.\n"
        "- Si `interpretation` trae intent o entidades distintos del tono superficial del mensaje, "
        "reflejalo en tacticsForRedactor (Waseller ya hizo una lectura; integrala con el hilo).\n"
        "- Mensaje ambiguo o multitema: usá mixed y ordená en tactics: primero empatía o aclaración, "
        "después dato concreto de stockTable si aplica.\n"
        "Respetá tenantCommercialContext del JSON si existe (tono y reglas del negocio).\n\n"
        "Contexto Waseller (JSON):\n\n{context}\n"
    )


def _crew_llm_response(body: ShadowCompareRequest) -> ShadowCompareResponse:
    # Contexto completo v1 + v1.1 (opcionales omitidos si son None).
    context: dict[str, Any] = body.model_dump(mode="python", exclude_none=True)
    context_str = json.dumps(context, ensure_ascii=False, indent=2)
    verbose = os.environ.get("CREWAI_VERBOSE", "").lower() in ("1", "true", "yes")

    mission = _sales_and_stock_rules(body)
    interp_banner = _interpretation_priority_banner(body)
    negotiation_block = _waseller_negotiation_context_block(body)
    tenant_note = _tenant_commercial_context_redactor_note(body)
    catalog_note = _public_catalog_prompt_note(body)
    flow_rules = _CONVERSATIONAL_FLOW_FOR_REDACTOR
    llm = _shadow_crew_llm()
    use_director = _use_conversation_director()

    redactor_goal = (
        "Proponer una decision candidata (solo telemetría) alineada al baseline y al tenant, "
        "usando **todo** el JSON (interpretation, recentMessages, rubro, stockTable, baseline). "
        "Sin inventar catálogo: stock y precios concretos solo si vienen en stockTable o ya "
        "están de forma inequívoca en baseline. Adaptá tono y estructura al flujo conversacional "
        "y a **cualquier tipo de respuesta del lead** (duda, rechazo, cambio de tema, pedido mixto, etc.)."
    )
    redactor_backstory = (
        "Sos el paso de redacción del crew shadow; representás al negocio del tenant y respetás el "
        "inventario del payload. Si hay director conversacional, su plan va antes que tu borrador; "
        "tu salida la revisa el crítico."
        if use_director
        else (
            "Sos el primer paso de un crew de comparación shadow; representás al negocio del tenant "
            "y respetás el inventario enviado en el payload. Tu salida la revisa otro agente."
        )
    )

    redactor = Agent(
        role="Redactor de respuesta shadow Waseller",
        goal=redactor_goal,
        backstory=redactor_backstory,
        verbose=verbose,
        allow_delegation=False,
        llm=llm,
    )

    redactor_intro = (
        "## Plan conversacional (salida del paso anterior)\n"
        "Ya corrió el **Director conversacional**. Su JSON trae `conversationMoment`, `leadTemperature` y "
        "`tacticsForRedactor`. Incorporalo en la **estructura y tono** de draftReply (no copies literal las "
        "tácticas: convertilas en mensaje al lead). Si es follow_up u objection, abrí contestando eso; si "
        "es closing, priorizá el paso concreto.\n\n"
        if use_director
        else ""
    )

    redactor_description = (
        "Contexto Waseller (JSON):\n\n{context}\n\n"
        f"{interp_banner}"
        f"{negotiation_block}"
        f"{tenant_note}"
        f"{catalog_note}"
        f"{redactor_intro}"
        f"{flow_rules}\n"
        f"{mission}\n"
        "Devolvé SOLO un objeto JSON (sin markdown) con esta forma exacta:\n"
        "{\n"
        '  "candidateDecision": {\n'
        '    "draftReply": "...",\n'
        '    "intent": "...",\n'
        '    "nextAction": "...",\n'
        '    "recommendedAction": "...",\n'
        '    "confidence": 0.0,\n'
        '    "reason": "..."\n'
        "  },\n"
        '  "candidateInterpretation": null\n'
        "  | {\n"
        '      "intent": "...",\n'
        '      "confidence": 0.0,\n'
        '      "nextAction": "...",\n'
        '      "source": "openai" | "rules",\n'
        '      "conversationStage": "..."\n'
        "    }\n"
        "}\n"
        "Todas las claves internas de candidateDecision son opcionales salvo las que puedas inferir.\n"
        "candidateDecision.draftReply: string **no vacío** siempre que puedas proponer texto al lead; "
        "no devuelvas \"\" ni null si hay baselineDecision.draftReply o datos en stockTable para armar "
        "una respuesta coherente (Waseller modo primary). Si el lead pide **otro color/talle/modelo**, "
        "draftReply debe ser **distinto en sustancia** al último mensaje del asistente en recentMessages "
        "(no repitas la misma ficha).\n"
        "candidateInterpretation: resumí la lectura del mensaje respecto al contexto; "
        "si no aporta valor, usá null.\n"
        f"nextAction / recommendedAction (en candidateDecision): uno de {NEXT_ACTION_ENUM_DOC}.\n"
        f"Si incluís candidateInterpretation.nextAction: mismo conjunto.\n"
        f"Si incluís source: solo {INTERPRETATION_SOURCE_ENUM_DOC}.\n"
        f"Si incluís conversationStage: solo uno de {CONVERSATION_STAGE_ENUM_DOC}."
    )

    critico = Agent(
        role="Crítico de contrato JSON Waseller",
        goal=(
            "Validar y, si hace falta, corregir el JSON del redactor para que sea un único objeto "
            "parseable y coherente con el contexto."
        ),
        backstory=(
            "No inventás datos nuevos: solo ajustás formato, enums y omisiones obvias. "
            "Si el JSON es inválido, reconstruís el mínimo válido posible."
        ),
        verbose=verbose,
        allow_delegation=False,
        llm=llm,
    )
    critic_extra = (
        "Si el crew usó director conversacional, draftReply debe sonar coherente con un turno "
        "orientado (saludo vs cierre vs objeción) sin contradecir stockTable.\n"
        if use_director
        else ""
    )
    critic_description = (
        "Recibís la salida del redactor: debe ser un JSON con "
        '"candidateDecision" y opcionalmente "candidateInterpretation" (objeto o null).\n'
        "Si trae texto extra o ```json, ignorá todo fuera del objeto JSON.\n"
        "Devolvé SOLO ese objeto final, sin markdown.\n"
        f"{critic_extra}"
        "Reglas: candidateDecision.nextAction y recommendedAction null o uno de "
        f"{NEXT_ACTION_ENUM_DOC}.\n"
        "No contradigas stockTable del contexto: precios/disponibilidad concretos solo si "
        "salen de esas filas o del baseline sin inventar filas nuevas.\n"
        "Si incomingText es aclaración (otro color, otro talle, más stock, etc.), draftReply debe "
        "responder eso de forma **nueva**: si el redactor repitió casi igual el último mensaje del "
        "asistente en recentMessages, **obligatorio** reemplazar draftReply: o listás otras filas de "
        "stockTable con esa variante, o explicás que en datos **solo existe** la variante ya nombrada "
        "y ofrecés siguiente paso (sin volver a pegar precio/stock/cierre idénticos al turno anterior).\n"
        "Si incomingText pide **asesor/derivación** o es **negativa** clara a reserva/cierre: draftReply **no** "
        "debe repetir la misma confirmación de producto ni el mismo '¿te reservo?'; priorizá handoff o cierre "
        "breve que invite a seguir el catálogo según las reglas del rol (sin inventar fuera de stockTable).\n"
        "draftReply no debe quedar vacío si el contexto permite al menos un borrador útil.\n"
        "Si el JSON trae `interpretation` con intención o entidades, el borrador debe ser **coherente** "
        "con esa lectura siempre que no contradiga stockTable ni invente datos.\n"
        "Si candidateInterpretation existe y trae nextAction, mismo conjunto.\n"
        "Si trae source, solo "
        f"{INTERPRETATION_SOURCE_ENUM_DOC}.\n"
        "Si trae conversationStage, solo "
        f"{CONVERSATION_STAGE_ENUM_DOC}."
    )

    if use_director:
        director = Agent(
            role="Director conversacional shadow Waseller",
            goal=(
                "Leer el hilo y clasificar el momento de la conversación; orientar al redactor sin "
                "inventar inventario ni precios."
            ),
            backstory=(
                "Sos el primer paso del crew: definís si el turno es saludo, seguimiento, objeción o cierre "
                "y qué debe priorizar el mensaje al lead. No redactás la respuesta final al cliente."
            ),
            verbose=verbose,
            allow_delegation=False,
            llm=llm,
        )
        tarea_director = Task(
            description=_director_task_description(),
            expected_output="Un único objeto JSON, sin markdown ni fences.",
            agent=director,
        )
        tarea_redactor = Task(
            description=redactor_description,
            expected_output="Un único objeto JSON (texto plano), sin fences ni comentarios.",
            agent=redactor,
            context=[tarea_director],
        )
        tarea_critico = Task(
            description=critic_description,
            expected_output="Un único objeto JSON final, sin markdown.",
            agent=critico,
            context=[tarea_redactor],
        )
        crew = Crew(
            agents=[director, redactor, critico],
            tasks=[tarea_director, tarea_redactor, tarea_critico],
            process=Process.sequential,
            verbose=verbose,
            tracing=False,
        )
    else:
        tarea_redactor = Task(
            description=redactor_description,
            expected_output="Un único objeto JSON (texto plano), sin fences ni comentarios.",
            agent=redactor,
        )
        tarea_critico = Task(
            description=critic_description,
            expected_output="Un único objeto JSON final, sin markdown.",
            agent=critico,
            context=[tarea_redactor],
        )
        crew = Crew(
            agents=[redactor, critico],
            tasks=[tarea_redactor, tarea_critico],
            process=Process.sequential,
            verbose=verbose,
            tracing=False,
        )

    raw = crew.kickoff(inputs={"context": context_str})
    text_out = raw if isinstance(raw, str) else str(raw)
    data = _json_from_crew_output(text_out)
    return _shadow_response_from_crew_dict(data)


def _openai_failure_hint(exc: BaseException) -> str | None:
    """Texto corto para logs cuando OpenAI devuelve 401 con causas conocidas."""
    s = str(exc)
    if "ip_not_authorized" in s or "Your IP is not authorized" in s:
        return (
            "openai_ip_not_authorized: la clave u organización tiene lista de IPs permitidas; "
            "la IP de salida de Railway no está incluida. En "
            "https://platform.openai.com/settings/organization/api-keys "
            "desactivá la restricción por IP o usá egress con IP fija compatible con tu allowlist."
        )
    if "invalid_api_key" in s:
        return "openai_invalid_api_key: revisá CREW_OPENAI_API_KEY / OPENAI_API_KEY."
    return None


def run_crew(body: ShadowCompareRequest) -> ShadowCompareResponse:
    if _use_crew_stub():
        log.info("USE_CREW_STUB activo: respuesta stub")
        return _finalize_shadow_response(body, _stub_response(body))
    if not effective_normalized_openai_api_key()[0]:
        log.warning("CREW_OPENAI_API_KEY / OPENAI_API_KEY ausente: usando stub")
        return _finalize_shadow_response(body, _stub_response(body))
    try:
        resp = _crew_llm_response(body)
        return _finalize_shadow_response(body, resp)
    except Exception as e:
        hint = _openai_failure_hint(e)
        log.error(
            structured_log_line(
                "crew_failure",
                tenant_id=body.tenantId,
                lead_id=body.leadId,
                error_type=type(e).__name__,
                openai_hint=hint,
            ),
            exc_info=True,
        )
        return _finalize_shadow_response(body, _stub_response(body))
