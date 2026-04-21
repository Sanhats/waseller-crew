"""
Salvaguarda post-LLM: evita repetir la misma ficha cuando el lead pide otra variante
(color, talle, etc.) y el inventario enviado no muestra otra opción.

El crew ya recibe reglas en prompt; esto corrige el caso frecuente en que el modelo
igual devuelve el mismo párrafo que el turno anterior.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from difflib import SequenceMatcher
from typing import Any

from crew_shadow_crewai.models import (
    RecentMessageItem,
    ShadowCompareRequest,
    ShadowCompareResponse,
)
from crew_shadow_crewai.observability import structured_log_line

log = logging.getLogger(__name__)

# Preguntas típicas de seguimiento (español rioplatense / neutro).
_VARIANT_ASK_RE = re.compile(
    r"(?:"
    r"\b(?:otro|otra|otros|otras)\s+(?:color|colores|talle|talles|tamaño|tamaños|medida|medidas|modelo)\b"
    r"|\bten(?:és|es)\s+(?:en\s+)?(?:otro|otra)\s+(?:color|talle|medida|modelo)\b"
    r"|\bhay\s+(?:en\s+)?(?:otro|otra)\s+(?:color|talle)\b"
    r"|\b(?:algún|algun|alguna)\s+otro\s+color\b"
    r"|\bcolores?\s+(?:distinto|distinta|diferente|otro|otra|más)\b"
    r")",
    re.IGNORECASE | re.UNICODE,
)


def _fold(s: str) -> str:
    """Normaliza para comparar borradores (sin acentos fuertes, minúsculas, espacios)."""
    t = unicodedata.normalize("NFKD", s)
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    t = t.lower()
    t = re.sub(r"\s+", " ", t)
    return t.strip()


def incoming_asks_variant_clarification(incoming_text: str) -> bool:
    return bool(_VARIANT_ASK_RE.search((incoming_text or "").strip()))


def _last_outgoing_text(recent: list[RecentMessageItem] | None) -> str:
    if not recent:
        return ""
    for item in reversed(recent):
        if item.direction == "outgoing" and (item.message or "").strip():
            return item.message.strip()
    return ""


def _similarity_ratio(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return float(SequenceMatcher(None, _fold(a), _fold(b)).ratio())


def drafts_substantially_duplicate(new_draft: str, previous_outgoing: str, *, threshold: float = 0.78) -> bool:
    """True si el nuevo borrador es casi el mismo texto que el último mensaje del asistente."""
    if not new_draft.strip() or not previous_outgoing.strip():
        return False
    return _similarity_ratio(new_draft, previous_outgoing) >= threshold


def _color_keys_in_row(row: dict[str, Any]) -> list[str]:
    return [k for k in row if "color" in str(k).lower()]


def unique_color_values(rows: list[dict[str, Any]]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for row in rows:
        for k in _color_keys_in_row(row):
            v = row.get(k)
            if v is None:
                continue
            s = str(v).strip()
            if not s or s in seen:
                continue
            seen.add(s)
            out.append(s)
    return out


def _size_keys_in_row(row: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for k in row:
        lk = str(k).lower()
        if any(x in lk for x in ("talle", "size", "tamano", "medida")):
            out.append(str(k))
    return out


def unique_size_values(rows: list[dict[str, Any]]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for row in rows:
        for k in _size_keys_in_row(row):
            v = row.get(k)
            if v is None:
                continue
            s = str(v).strip()
            if not s or s in seen:
                continue
            seen.add(s)
            out.append(s)
    return out


def _model_keys_in_row(row: dict[str, Any]) -> list[str]:
    return [k for k in row if "model" in str(k).lower()]


def unique_model_values(rows: list[dict[str, Any]]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for row in rows:
        for k in _model_keys_in_row(row):
            v = row.get(k)
            if v is None:
                continue
            s = str(v).strip()
            if not s or s in seen:
                continue
            seen.add(s)
            out.append(s)
    return out


def _pick_label_row(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {}
    return rows[0]


def _short_variant_tail(row: dict[str, Any]) -> str:
    """Fragmento corto y seguro desde filas reales (no inventa claves)."""
    parts: list[str] = []
    for key in ("color", "Color", "talle", "Talle", "size", "modelo", "Modelo", "name", "nombre"):
        if key in row and row[key] is not None:
            s = str(row[key]).strip()
            if s and s not in parts:
                parts.append(s)
            if len(parts) >= 2:
                break
    if not parts:
        return ""
    return " (" + ", ".join(parts[:2]) + ")"


def stock_lacks_alternative_for_incoming(rows: list[dict[str, Any]], incoming: str) -> bool:
    """
    True si el payload no permite contestar “otra variante” con otra fila distinta
    según lo que preguntó el lead (color / talle / modelo).
    """
    if not rows:
        return False
    inc = (incoming or "").lower()
    if len(rows) == 1:
        return True
    if "color" in inc or "tono" in inc or "tinte" in inc:
        return len(unique_color_values(rows)) <= 1
    if "talle" in inc or "medida" in inc or "tamaño" in inc or "tamano" in inc:
        return len(unique_size_values(rows)) <= 1
    if "modelo" in inc:
        return len(unique_model_values(rows)) <= 1
    # Pregunta genérica de variante: solo forzamos si el listado es una sola fila.
    return len(rows) == 1


def build_variant_only_reply(
    *,
    asks_colorish: bool,
    asks_size: bool,
    asks_model: bool,
    row: dict[str, Any],
) -> str:
    tail = _short_variant_tail(row)
    if asks_colorish:
        return (
            "Sobre el color: en el inventario que me pasaron solo figura esta variante"
            f"{tail}. No aparece otra fila con otro color. "
            "Si te sirve así, ¿querés que te reserve una? "
            "Si buscás otro tono, en tienda pueden confirmarte si hay algo que aún no está cargado acá."
        )
    if asks_size:
        return (
            "Sobre el talle/medida: en los datos que tengo acá solo aparece esta opción"
            f"{tail}. No veo otra fila con otra medida. "
            "Si te sirve, ¿te reservo? Si necesitás otro tamaño, en tienda pueden confirmarte stock adicional."
        )
    if asks_model:
        return (
            "Sobre el modelo: en el inventario enviado solo figura esta variante"
            f"{tail}. Si querés otra línea de producto, decime qué buscás y reviso el listado."
        )
    return (
        "Sobre esa consulta: en los datos del inventario que tengo acá solo aparece esta opción"
        f"{tail}. Si te sirve, ¿te reservo? "
        "Si necesitás otra medida o modelo, decime y veo qué más hay en el listado."
    )


def _asks_mostly_color(incoming: str) -> bool:
    s = (incoming or "").lower()
    return "color" in s or "tono" in s or "tinte" in s


def _asks_mostly_size(incoming: str) -> bool:
    s = (incoming or "").lower()
    return any(x in s for x in ("talle", "medida", "tamaño", "tamano"))


def _asks_mostly_model(incoming: str) -> bool:
    return "modelo" in (incoming or "").lower()


def apply_variant_followup_guard(
    body: ShadowCompareRequest,
    resp: ShadowCompareResponse,
) -> ShadowCompareResponse:
    """
    Si el lead pide otra variante, el borrador repite el último outgoing y el stock
    no muestra otro color (o es una sola fila), reemplaza por una respuesta explícita.
    """
    cd = resp.candidateDecision
    if cd is None:
        return resp
    draft = (cd.draftReply or "").strip()
    if not draft:
        return resp
    inc = (body.incomingText or "").strip()
    if not inc or not incoming_asks_variant_clarification(inc):
        return resp

    rows = body.stockTable or []
    if not stock_lacks_alternative_for_incoming(rows, inc):
        return resp

    last_out = _last_outgoing_text(body.recentMessages)
    baseline_draft = ""
    if isinstance(body.baselineDecision, dict):
        baseline_draft = str(body.baselineDecision.get("draftReply") or "").strip()

    repeated_vs_last = bool(last_out) and drafts_substantially_duplicate(draft, last_out)
    repeated_vs_baseline = bool(baseline_draft) and drafts_substantially_duplicate(draft, baseline_draft)
    if not repeated_vs_last and not repeated_vs_baseline:
        return resp

    asks_color = _asks_mostly_color(inc)
    asks_size = _asks_mostly_size(inc)
    asks_model = _asks_mostly_model(inc)
    row = _pick_label_row(rows)
    new_text = build_variant_only_reply(
        asks_colorish=asks_color,
        asks_size=asks_size,
        asks_model=asks_model,
        row=row,
    )
    prev_reason = (cd.reason or "").strip()
    new_reason = "variant_guard" if not prev_reason else f"{prev_reason}|variant_guard"
    log.info(
        structured_log_line(
            "shadow_compare_variant_guard_applied",
            tenant_id=body.tenantId,
            lead_id=body.leadId,
            correlation_id=body.correlationId,
        )
    )
    return ShadowCompareResponse(
        candidateDecision=cd.model_copy(
            update={
                "draftReply": new_text[:2000],
                "reason": new_reason[:500],
            }
        ),
        candidateInterpretation=resp.candidateInterpretation,
    )
