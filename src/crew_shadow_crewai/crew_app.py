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


def _load_tenant_prompt_overlay(slug: str | None) -> str:
    """Texto opcional desde tenant_prompts/<slug>.txt o CREW_TENANT_PROMPTS_DIR."""
    if not slug:
        return ""
    raw = os.environ.get("CREW_TENANT_PROMPTS_DIR", "").strip()
    base = Path(raw).expanduser() if raw else _default_tenant_prompts_dir()
    path = base / f"{slug}.txt"
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
            "No agregues productos, precios ni stock que no surjan de esas filas o del texto ya "
            "presente en baseline/interpretation. Si incomingText no matchea ninguna fila, no inventes: "
            "pedí datos o ofrecé alternativas alineadas a filas existentes."
        )
    profile = (
        f" Perfil comercial (businessProfileSlug): {body.businessProfileSlug}."
        if body.businessProfileSlug
        else ""
    )
    overlay = _load_tenant_prompt_overlay(body.businessProfileSlug)
    narrow = (body.inventoryNarrowingNote or "").strip()
    narrow_block = ""
    if narrow:
        narrow_block = (
            f"\n- **Nota de acotación de inventario (Waseller):** {narrow[:4000]}\n"
            "  Usala junto con stockTable; no la contradigas salvo que sea incoherente con las filas.\n"
        )
    return (
        "\n## Rol, tenant e inventario\n"
        f"- Actuás como **asistente de ventas del negocio** identificado por tenantId={body.tenantId} "
        f"en el JSON de contexto.{profile} Tu objetivo es ayudar a cerrar la venta con tono profesional "
        "y claro.\n"
        f"- {stock_hint}\n"
        "- Usá incomingText como mensaje actual del lead y recentMessages (si hay) como contexto "
        "reciente; no ignores contradicciones entre mensajes.\n"
        "- **Seguimiento (obligatorio):** Si incomingText pide **otro color**, **otro talle**, **otro modelo**, "
        "**otra medida**, **más unidades**, **envío**, etc., tu **primer párrafo** debe contestar eso. "
        "Revisá **todas** las filas de stockTable (mismo producto u otros) y listá **solo** variantes que "
        "aparezcan en datos (colores/talles distintos en otras filas). Si **ninguna** otra fila trae otro "
        "color/talle, decí explícitamente que en el inventario enviado **solo figura** esa variante "
        "(nombrala una vez) y ofrecé ayuda (otro producto del listado, reserva, tienda física, etc.). "
        "**Prohibido** responder solo re-enviando la misma ficha de producto del mensaje anterior.\n"
        "- **Anti-repetición (dura):** Compará candidateDecision/baselineDecision.draftReply y los "
        "mensajes del asistente en recentMessages. Si el lead pide variante y tu borrador sería **casi el "
        "mismo texto** (mismo precio, color, talle, stock y cierre) que el último envío del asistente: "
        "**fallaste** — reescribí desde cero: negativa clara o listado de otras filas, sin copiar el "
        "bloque anterior.\n"
        f"{narrow_block}"
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


def _crew_llm_response(body: ShadowCompareRequest) -> ShadowCompareResponse:
    # Contexto completo v1 + v1.1 (opcionales omitidos si son None).
    context: dict[str, Any] = body.model_dump(mode="python", exclude_none=True)
    context_str = json.dumps(context, ensure_ascii=False, indent=2)
    verbose = os.environ.get("CREWAI_VERBOSE", "").lower() in ("1", "true", "yes")

    mission = _sales_and_stock_rules(body)
    llm = _shadow_crew_llm()
    redactor = Agent(
        role="Redactor de respuesta shadow Waseller",
        goal=(
            "Proponer una decision candidata (solo telemetría) alineada al baseline y al tenant, "
            "sin inventar catálogo: stock y precios concretos solo si vienen en stockTable o ya "
            "están de forma inequívoca en baseline/interpretation."
        ),
        backstory=(
            "Sos el primer paso de un crew de comparación shadow; representás al negocio del tenant "
            "y respetás el inventario enviado en el payload. Tu salida la revisa otro agente."
        ),
        verbose=verbose,
        allow_delegation=False,
        llm=llm,
    )

    tarea_redactor = Task(
        description=(
            "Contexto Waseller (JSON):\n\n{context}\n\n"
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
        ),
        expected_output="Un único objeto JSON (texto plano), sin fences ni comentarios.",
        agent=redactor,
    )

    # Agente 2: revisa y corrige solo el JSON (no re-escribe el contexto).
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
    tarea_critico = Task(
        description=(
            "Recibís la salida del redactor: debe ser un JSON con "
            '"candidateDecision" y opcionalmente "candidateInterpretation" (objeto o null).\n'
            "Si trae texto extra o ```json, ignorá todo fuera del objeto JSON.\n"
            "Devolvé SOLO ese objeto final, sin markdown.\n"
            "Reglas: candidateDecision.nextAction y recommendedAction null o uno de "
            f"{NEXT_ACTION_ENUM_DOC}.\n"
            "No contradigas stockTable del contexto: precios/disponibilidad concretos solo si "
            "salen de esas filas o del baseline sin inventar filas nuevas.\n"
            "Si incomingText es aclaración (otro color, otro talle, más stock, etc.), draftReply debe "
            "responder eso de forma **nueva**: si el redactor repitió casi igual el último mensaje del "
            "asistente en recentMessages, **obligatorio** reemplazar draftReply: o listás otras filas de "
            "stockTable con esa variante, o explicás que en datos **solo existe** la variante ya nombrada "
            "y ofrecés siguiente paso (sin volver a pegar precio/stock/cierre idénticos al turno anterior).\n"
            "draftReply no debe quedar vacío si el contexto permite al menos un borrador útil.\n"
            "Si candidateInterpretation existe y trae nextAction, mismo conjunto.\n"
            "Si trae source, solo "
            f"{INTERPRETATION_SOURCE_ENUM_DOC}.\n"
            "Si trae conversationStage, solo "
            f"{CONVERSATION_STAGE_ENUM_DOC}."
        ),
        expected_output="Un único objeto JSON final, sin markdown.",
        agent=critico,
        context=[tarea_redactor],
    )
    crew = Crew(
        agents=[redactor, critico],
        tasks=[tarea_redactor, tarea_critico],
        process=Process.sequential,
        verbose=verbose,
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
        return _stub_response(body)
    if not effective_normalized_openai_api_key()[0]:
        log.warning("CREW_OPENAI_API_KEY / OPENAI_API_KEY ausente: usando stub")
        return _stub_response(body)
    try:
        resp = _crew_llm_response(body)
        return _enrich_empty_draft_reply(resp, body)
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
        return _stub_response(body)
