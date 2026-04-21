"""
Salvaguarda post-LLM: evita repetir la misma ficha cuando el lead pide otra variante
(color, talle, etc.) y el inventario enviado no muestra otra opción.

El crew ya recibe reglas en prompt; esto corrige el caso frecuente en que el modelo
igual devuelve el mismo párrafo que el turno anterior.
"""

from __future__ import annotations

import logging
import os
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
    # "otro color / otro talle / otro modelo" en cualquier forma
    r"\b(?:otro|otra|otros|otras)\s+(?:color|colores|talle|talles|talla|tallas|tamaño|tamaños|medida|medidas|modelo|modelos)\b"
    # "tenés otro color / tenés en otro talle"
    r"|\bten(?:és|es)\s+(?:en\s+)?(?:otro|otra)\s+(?:color|talle|talla|medida|modelo)\b"
    # "hay otro color / hay en otro talle"
    r"|\bhay\s+(?:en\s+)?(?:otro|otra)\s+(?:color|talle|talla)\b"
    # "algún otro color"
    r"|\b(?:algún|algun|alguna)\s+otro\s+(?:color|talle|modelo)\b"
    # "colores distintos / color más"
    r"|\bcolores?\s+(?:distinto|distinta|diferente|otro|otra|más|mas)\b"
    # "¿sale en otro color/talle?"
    r"|\bsale[ns]?\s+en\s+(?:otro|otra)\s+(?:color|talle|talla|medida|modelo)\b"
    # "hay más colores / hay más talles"
    r"|\bhay\s+(?:más|mas)\s+(?:colores?|talles?|tallas?|medidas?|modelos?)\b"
    # "¿en qué colores viene? / ¿en qué talles lo tienen?"
    r"|\ben\s+qu[eé]\s+(?:colores?|talles?|tallas?|medidas?|modelos?)\b"
    # "¿qué colores tienen? / ¿qué talles hay?"
    r"|\bqu[eé]\s+colores?\s+(?:tienen?|ten[eé]s|hay|manejan?|trabajan?)\b"
    r"|\bqu[eé]\s+(?:talles?|tallas?|medidas?)\s+(?:tienen?|ten[eé]s|hay|manejan?)\b"
    # "quiero / necesito / busco en otro color/talle"
    r"|\b(?:quiero|necesito|busco)\s+(?:en\s+)?(?:otro|otra)\s+(?:color|talle|talla|medida|modelo)\b"
    # "más colores / más talles" como pregunta suelta
    r"|\bm[aá]s\s+(?:colores?|talles?|tallas?|medidas?|modelos?)\b"
    # "talles disponibles / colores disponibles"
    r"|\b(?:talles?|tallas?|colores?)\s+disponibles?\b"
    # "viene en otros colores / sale en otros talles"
    r"|\b(?:viene|vienen|sale|salen)\s+en\s+(?:otros?|otras?)\s+(?:colores?|talles?|tallas?|medidas?|modelos?)\b"
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


def _dedupe_similarity_threshold() -> float:
    """Umbral 0.5–0.99; configurable con CREW_SHADOW_DEDUPE_SIMILARITY (default 0.78)."""
    raw = os.environ.get("CREW_SHADOW_DEDUPE_SIMILARITY", "").strip()
    if not raw:
        return 0.78
    try:
        v = float(raw.replace(",", "."))
    except ValueError:
        return 0.78
    return min(0.99, max(0.5, v))


def drafts_substantially_duplicate(
    new_draft: str, previous_outgoing: str, *, threshold: float | None = None
) -> bool:
    """True si el nuevo borrador es casi el mismo texto que el último mensaje del asistente."""
    if not new_draft.strip() or not previous_outgoing.strip():
        return False
    th = _dedupe_similarity_threshold() if threshold is None else threshold
    return _similarity_ratio(new_draft, previous_outgoing) >= th


def _env_guard_enabled(var_name: str, *, default: bool = True) -> bool:
    raw = os.environ.get(var_name, "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


_CATALOG_ASK_RE = re.compile(
    r"(?:"
    r"\bcat[aá]logos?\b"
    r"|\b(?:list(?:a|ado)\s+completo|todo\s+el\s+cat[aá]logo)\b"
    r"|\bqu[eé]\s+m[aá]s\s+(?:ten[eé]s|tienen|hay|ofrecen)\b"
    r"|\bqu[eé]\s+otros?\s+productos?\b"
    r"|\botros?\s+productos?\b"
    r"|\balgo\s+m[aá]s\b"
    r"|\bten[eé]s\s+m[aá]s\b"
    r"|\bproductos?\s+disponibles?\b"
    r")",
    re.IGNORECASE | re.UNICODE,
)

_MISC_FOLLOWUP_RE = re.compile(
    r"(?:"
    r"\benv[ií]os?\b"
    r"|\bentregas?\b"
    r"|\b(?:retiro|retirar)(?:\s+en\s+(?:local|tienda))?\b"
    r"|\bmedios?\s+de\s+pago\b"
    r"|\bformas?\s+de\s+pago\b"
    r")",
    re.IGNORECASE | re.UNICODE,
)

_STOCK_NUMERIC_KEYS: tuple[str, ...] = (
    "availableStock",
    "stock",
    "cantidad",
    "quantity",
    "disponible",
    "inventory",
    "qty",
    "stockDisponible",
)


def incoming_asks_catalog_or_broader_products(text: str) -> bool:
    return bool(_CATALOG_ASK_RE.search((text or "").strip()))


def total_stock_units(rows: list[dict[str, Any]]) -> int:
    """Suma por fila un único valor numérico de stock (primera clave reconocida)."""
    total = 0
    for row in rows:
        for k in _STOCK_NUMERIC_KEYS:
            if k not in row:
                continue
            val = row.get(k)
            if val is None:
                continue
            try:
                n = int(float(str(val).replace(",", ".").strip()))
                if n >= 0:
                    total += n
                break
            except (TypeError, ValueError):
                break
    return total


def extract_requested_quantity(text: str) -> int | None:
    """
    Cantidad pedida en el mensaje (heurística). None si no hay señal clara.
    """
    t = (text or "").strip().lower()
    if not t:
        return None
    if re.search(r"\bmedia\s+docena\b", t):
        return 6
    m_doc = re.search(r"\b(\d{1,3})\s*docenas?\b", t)
    if m_doc:
        return int(m_doc.group(1)) * 12
    patterns = (
        r"\bquiero\s+(\d{1,5})\b",
        r"\b(?:pedir(?:[ei]a)?|necesito|llevamos?|llevo|mandame|mand[aá]me)\s+(?:unas?\s+)?(\d{1,5})\b",
        r"\b(?:son|somos)\s+(\d{1,5})\b",
        r"\b(\d{1,5})\s*(?:unidades?|uds?\.?|u\.?|piezas?)\b",
        r"\bcantidad\s*:?\s*(\d{1,5})\b",
        r"\b(?:pedido|reserva)\s+(?:de\s+)?(\d{1,5})\b",
    )
    best: int | None = None
    for p in patterns:
        for m in re.finditer(p, t):
            n = int(m.group(1))
            if 1 <= n <= 50_000:
                best = max(best or 0, n)
    return best


def narrow_suggests_partial_inventory(narrow: str | None) -> bool:
    n = _fold(narrow or "")
    if not n:
        return False
    hints = (
        "catalogo",
        "filtr",
        "solo variant",
        "una fila",
        "un producto",
        "resultado",
        "acot",
        "subconj",
        "rag",
        "parcial",
        "listado",
        "mas de",
        "más de",
        "completo tiene",
    )
    return any(h in n for h in hints)


def _limited_catalog_scope(body: ShadowCompareRequest) -> bool:
    rows = body.stockTable or []
    if len(rows) <= 1:
        return True
    return narrow_suggests_partial_inventory(body.inventoryNarrowingNote)


def _duplicate_vs_recent(
    draft: str, body: ShadowCompareRequest
) -> tuple[bool, str, str]:
    """(es_duplicado, last_out, baseline_draft)."""
    last_out = _last_outgoing_text(body.recentMessages)
    baseline_draft = ""
    if isinstance(body.baselineDecision, dict):
        baseline_draft = str(body.baselineDecision.get("draftReply") or "").strip()
    rep_last = bool(last_out) and drafts_substantially_duplicate(draft, last_out)
    rep_base = bool(baseline_draft) and drafts_substantially_duplicate(draft, baseline_draft)
    return (rep_last or rep_base), last_out, baseline_draft


def build_multi_variant_options_reply(rows: list[dict[str, Any]], incoming: str) -> str:
    colors = unique_color_values(rows)
    sizes = unique_size_values(rows)
    parts: list[str] = []
    if _asks_mostly_color(incoming) and len(colors) > 1:
        parts.append("colores: " + ", ".join(colors))
    if _asks_mostly_size(incoming) and len(sizes) > 1:
        parts.append("talles/medidas: " + ", ".join(sizes))
    if not parts:
        if len(colors) > 1:
            parts.append("colores: " + ", ".join(colors))
        if len(sizes) > 1:
            parts.append("talles/medidas: " + ", ".join(sizes))
    if not parts:
        return ""
    joined = "; ".join(parts)
    return (
        f"Respecto de lo que preguntás, en stockTable aparecen estas opciones: {joined}. "
        "¿Cuál te interesa? Te confirmo precio y disponibilidad de la que elijas."
    )


def build_catalog_scope_reply(body: ShadowCompareRequest) -> str:
    narrow = (body.inventoryNarrowingNote or "").strip()
    narrow_sentence = (
        f" Waseller indicó esto sobre el alcance: «{narrow[:500]}»." if narrow else ""
    )
    return (
        "Sobre catálogo u otros productos: en este turno solo tengo la vista de inventario que viene "
        f"en stockTable (no es el catálogo completo de la tienda).{narrow_sentence} "
        "Pasame qué buscás (nombre, rubro, presupuesto aproximado o palabras clave) y en el próximo "
        "paso cruzamos con más líneas si el sistema las envía."
    )


def build_quantity_over_stock_reply(requested: int, available: int) -> str:
    u_req = "unidad" if requested == 1 else "unidades"
    u_av = "unidad" if available == 1 else "unidades"
    return (
        f"Pediste {requested} {u_req}; en stockTable la disponibilidad que veo suma {available} {u_av} "
        "como máximo en este listado. ¿Te reservo hasta lo disponible según estos datos y coordinamos "
        "el resto con un asesor o una posible reposición? Así no queda nada colgado."
    )


def build_misc_followup_dedupe_reply(incoming: str) -> str:
    inc_l = (incoming or "").lower()
    topic = "envío o entrega"
    if re.search(r"pago|pagos", inc_l):
        topic = "medios de pago"
    elif re.search(r"retiro", inc_l):
        topic = "retiro en local"
    return (
        f"Sobre {topic}: con el inventario que tengo acá solo puedo asegurar precio y disponibilidad "
        "de lo que figura en stockTable. Ese tema lo cerramos con el equipo en el siguiente paso. "
        "¿Seguimos con la reserva o la variante que estabas viendo?"
    )


def apply_multi_variant_list_guard(
    body: ShadowCompareRequest,
    resp: ShadowCompareResponse,
) -> ShadowCompareResponse:
    """
    Varias filas con alternativas reales y borrador duplicado: lista determinística desde la tabla.
    """
    if not _env_guard_enabled("CREW_SHADOW_MULTI_VARIANT_LIST_GUARD", default=True):
        return resp
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
    if stock_lacks_alternative_for_incoming(rows, inc):
        return resp
    dup, _, _ = _duplicate_vs_recent(draft, body)
    if not dup:
        return resp
    new_text = build_multi_variant_options_reply(rows, inc)
    if not new_text:
        return resp
    prev_reason = (cd.reason or "").strip()
    new_reason = "multi_variant_list_guard" if not prev_reason else f"{prev_reason}|multi_variant_list_guard"
    log.info(
        structured_log_line(
            "shadow_compare_multi_variant_list_guard_applied",
            tenant_id=body.tenantId,
            lead_id=body.leadId,
            correlation_id=body.correlationId,
        )
    )
    return ShadowCompareResponse(
        candidateDecision=cd.model_copy(
            update={"draftReply": new_text[:2000], "reason": new_reason[:500]}
        ),
        candidateInterpretation=resp.candidateInterpretation,
    )


def apply_quantity_vs_stock_guard(
    body: ShadowCompareRequest,
    resp: ShadowCompareResponse,
) -> ShadowCompareResponse:
    if not _env_guard_enabled("CREW_SHADOW_QUANTITY_GUARD", default=True):
        return resp
    cd = resp.candidateDecision
    if cd is None:
        return resp
    draft = (cd.draftReply or "").strip()
    if not draft:
        return resp
    inc = (body.incomingText or "").strip()
    qty = extract_requested_quantity(inc)
    rows = body.stockTable or []
    total = total_stock_units(rows)
    if qty is None or total <= 0 or qty <= total:
        return resp
    dup, _, _ = _duplicate_vs_recent(draft, body)
    fold_d = _fold(draft)
    mentions_available = str(total) in fold_d or re.search(
        rf"\b{qty}\b.*\b(?:disponible|hay|tengo|quedan)\b", fold_d
    )
    if not dup and mentions_available:
        return resp
    new_text = build_quantity_over_stock_reply(qty, total)
    prev_reason = (cd.reason or "").strip()
    new_reason = "quantity_stock_guard" if not prev_reason else f"{prev_reason}|quantity_stock_guard"
    log.info(
        structured_log_line(
            "shadow_compare_quantity_stock_guard_applied",
            tenant_id=body.tenantId,
            lead_id=body.leadId,
            correlation_id=body.correlationId,
        )
    )
    return ShadowCompareResponse(
        candidateDecision=cd.model_copy(
            update={"draftReply": new_text[:2000], "reason": new_reason[:500]}
        ),
        candidateInterpretation=resp.candidateInterpretation,
    )


def apply_catalog_scope_guard(
    body: ShadowCompareRequest,
    resp: ShadowCompareResponse,
) -> ShadowCompareResponse:
    if not _env_guard_enabled("CREW_SHADOW_CATALOG_SCOPE_GUARD", default=True):
        return resp
    cd = resp.candidateDecision
    if cd is None:
        return resp
    draft = (cd.draftReply or "").strip()
    if not draft:
        return resp
    inc = (body.incomingText or "").strip()
    if not inc or not incoming_asks_catalog_or_broader_products(inc):
        return resp
    if not _limited_catalog_scope(body):
        return resp
    dup, _, _ = _duplicate_vs_recent(draft, body)
    if not dup:
        return resp
    new_text = build_catalog_scope_reply(body)
    prev_reason = (cd.reason or "").strip()
    new_reason = "catalog_scope_guard" if not prev_reason else f"{prev_reason}|catalog_scope_guard"
    log.info(
        structured_log_line(
            "shadow_compare_catalog_scope_guard_applied",
            tenant_id=body.tenantId,
            lead_id=body.leadId,
            correlation_id=body.correlationId,
        )
    )
    return ShadowCompareResponse(
        candidateDecision=cd.model_copy(
            update={"draftReply": new_text[:2000], "reason": new_reason[:500]}
        ),
        candidateInterpretation=resp.candidateInterpretation,
    )


def apply_generic_duplicate_followup_guard(
    body: ShadowCompareRequest,
    resp: ShadowCompareResponse,
) -> ShadowCompareResponse:
    """
    Post-chequeo: seguimiento no cubierto por variantes/catálogo/cantidad (p. ej. envío) + borrador duplicado.
    """
    if not _env_guard_enabled("CREW_SHADOW_GENERIC_DEDUPE_GUARD", default=True):
        return resp
    cd = resp.candidateDecision
    if cd is None:
        return resp
    draft = (cd.draftReply or "").strip()
    if not draft:
        return resp
    inc = (body.incomingText or "").strip()
    if not inc:
        return resp
    if incoming_asks_variant_clarification(inc):
        return resp
    if incoming_asks_catalog_or_broader_products(inc) and _limited_catalog_scope(body):
        return resp
    if extract_requested_quantity(inc) is not None:
        return resp
    if not _MISC_FOLLOWUP_RE.search(inc):
        return resp
    dup, _, _ = _duplicate_vs_recent(draft, body)
    if not dup:
        return resp
    new_text = build_misc_followup_dedupe_reply(inc)
    prev_reason = (cd.reason or "").strip()
    new_reason = (
        "generic_followup_dedupe_guard"
        if not prev_reason
        else f"{prev_reason}|generic_followup_dedupe_guard"
    )
    log.info(
        structured_log_line(
            "shadow_compare_generic_followup_dedupe_guard_applied",
            tenant_id=body.tenantId,
            lead_id=body.leadId,
            correlation_id=body.correlationId,
        )
    )
    return ShadowCompareResponse(
        candidateDecision=cd.model_copy(
            update={"draftReply": new_text[:2000], "reason": new_reason[:500]}
        ),
        candidateInterpretation=resp.candidateInterpretation,
    )


def apply_followup_draft_guards(
    body: ShadowCompareRequest,
    resp: ShadowCompareResponse,
) -> ShadowCompareResponse:
    """Cadena de salvaguardas post-LLM (orden importa)."""
    resp = apply_multi_variant_list_guard(body, resp)
    resp = apply_variant_followup_guard(body, resp)
    resp = apply_quantity_vs_stock_guard(body, resp)
    resp = apply_catalog_scope_guard(body, resp)
    resp = apply_generic_duplicate_followup_guard(body, resp)
    return resp


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


def _stock_urgency_note(row: dict[str, Any]) -> str:
    """Genera una nota de urgencia si el stock disponible es bajo."""
    for key in ("availableStock", "stock", "disponible", "cantidad"):
        val = row.get(key)
        if val is None:
            continue
        try:
            n = int(val)
        except (TypeError, ValueError):
            continue
        if 1 <= n <= 3:
            return f" (quedan {n} {'unidad' if n == 1 else 'unidades'})"
        break
    return ""


def build_variant_only_reply(
    *,
    asks_colorish: bool,
    asks_size: bool,
    asks_model: bool,
    row: dict[str, Any],
) -> str:
    tail = _short_variant_tail(row)
    urgency = _stock_urgency_note(row)
    if asks_colorish:
        return (
            f"Sobre el color: en el inventario que manejo ahora mismo solo figura esta variante{tail}{urgency}. "
            "No aparece otra fila con otro color disponible. "
            "¿Te sirve esta opción? Si es así, te la reservo ahora para que no se te vaya. "
            "Si buscás otro tono, decime y consulto si hay algo sin cargar en el sistema todavía."
        )
    if asks_size:
        return (
            f"Sobre el talle: en los datos que tengo en este momento solo aparece esta opción{tail}{urgency}. "
            "No veo otra medida disponible en el listado. "
            "¿Te queda bien así? Puedo reservártela enseguida. "
            "Si necesitás otro tamaño, consultamos en tienda si hay stock adicional."
        )
    if asks_model:
        return (
            f"Sobre el modelo: en el inventario que tengo cargado solo figura esta variante{tail}{urgency}. "
            "Si te interesa otra línea de producto, contame qué buscás y reviso qué más hay en el listado. "
            "¿Armamos el pedido con este o preferís que busque algo diferente?"
        )
    return (
        f"En el inventario que tengo disponible ahora solo aparece esta opción{tail}{urgency}. "
        "¿Te sirve? Si es así, te la reservo para asegurártela. "
        "Si necesitás otra medida o modelo, decime qué buscás y veo qué más tenemos."
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
