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
from typing import Any

from crewai import Agent, Crew, Process, Task

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

log = logging.getLogger(__name__)

# Forzar stub sin llamar al LLM (útil en CI o para aprender el cable HTTP primero).
_USE_STUB = os.environ.get("USE_CREW_STUB", "").lower() in ("1", "true", "yes")


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

    redactor = Agent(
        role="Redactor de respuesta shadow Waseller",
        goal=(
            "Proponer una decision candidata(solo telemetria) alineada al baseline, "
            "sin inventar stock/precio que contradigan el baseline."
        ),
        backstory=(
            "Sos el primer paso de un crew de comparacón shadow, tu salida la revisa otro agente."
        ),
        verbose=verbose,
        allow_delegation=False,
    )


    tarea_redactor = Task(
        description=(
            "Contexto Waseller (JSON):\n\n{context}\n\n"
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
    )
    tarea_critico = Task(
        description=(
            "Recibís la salida del redactor: debe ser un JSON con "
            '"candidateDecision" y opcionalmente "candidateInterpretation" (objeto o null).\n'
            "Si trae texto extra o ```json, ignorá todo fuera del objeto JSON.\n"
            "Devolvé SOLO ese objeto final, sin markdown.\n"
            "Reglas: candidateDecision.nextAction y recommendedAction null o uno de "
            f"{NEXT_ACTION_ENUM_DOC}.\n"
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


def run_crew(body: ShadowCompareRequest) -> ShadowCompareResponse:
    if _USE_STUB:
        log.info("USE_CREW_STUB activo: respuesta stub")
        return _stub_response(body)
    if not os.environ.get("OPENAI_API_KEY"):
        log.warning("OPENAI_API_KEY ausente: usando stub")
        return _stub_response(body)
    try:
        return _crew_llm_response(body)
    except Exception:
        log.exception("Fallo CrewAI; degradando a stub")
        return _stub_response(body)
