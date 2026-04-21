"""OPENAI_API_KEY: normalización compartida (main + CrewAI)."""

from __future__ import annotations

import os


def pick_raw_openai_api_key_from_environ() -> tuple[str | None, str]:
    """
    Elige la clave cruda antes de normalizar.

    CREW_OPENAI_API_KEY tiene prioridad si no está vacía (Railway: variable compartida
    OPENAI_API_KEY a nivel proyecto a veces pisa la del servicio; podés poner la clave
    buena solo en waseller-crew como CREW_OPENAI_API_KEY).
    """
    crew = os.environ.get("CREW_OPENAI_API_KEY")
    if crew is not None and crew.strip():
        return crew, "CREW_OPENAI_API_KEY"
    oa = os.environ.get("OPENAI_API_KEY")
    if oa is not None and oa.strip():
        return oa, "OPENAI_API_KEY"
    return None, "none"


# Pegados desde Slack/Notion/navegador suelen meter estos puntos de código; OpenAI ve otra cadena → 401.
_INVISIBLE_CODEPOINTS: frozenset[int] = frozenset(
    {
        0xFEFF,  # BOM
        0x200B,  # zero-width space
        0x200C,  # zero-width non-joiner
        0x200D,  # zero-width joiner
        0x200E,  # LRM
        0x200F,  # RLM
        0x2060,  # word joiner
        0x180E,  # deprecated Mongolian vowel separator
    }
)


def _strip_invisible(s: str) -> str:
    return "".join(ch for ch in s if ord(ch) not in _INVISIBLE_CODEPOINTS)


def normalize_openai_api_key(raw: str | None) -> tuple[str, bool]:
    """
    Evita 401 por pegados en Railway/UI: espacios, saltos, comillas o prefijo Bearer.
    Las claves sk-… no contienen espacios internos válidos; eliminar whitespace es seguro.
    """
    if not raw:
        return "", False
    before_naive = raw.strip()
    s = before_naive
    changed = False
    if len(s) >= 2 and ((s[0] == '"' and s[-1] == '"') or (s[0] == "'" and s[-1] == "'")):
        s = s[1:-1].strip()
        changed = True
    if s.lower().startswith("bearer "):
        s = s[7:].strip()
        changed = True
    s2 = _strip_invisible(s)
    if s2 != s:
        changed = True
        s = s2
    s = "".join(s.split())
    if s != before_naive:
        changed = True
    return s, changed


def effective_normalized_openai_api_key() -> tuple[str, str]:
    """(clave normalizada, fuente). Vacío si no hay ninguna variable útil."""
    raw, source = pick_raw_openai_api_key_from_environ()
    key, _ = normalize_openai_api_key(raw)
    return key, source
