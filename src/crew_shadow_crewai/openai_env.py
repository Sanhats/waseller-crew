"""OPENAI_API_KEY: normalización compartida (main + CrewAI)."""

from __future__ import annotations


def normalize_openai_api_key(raw: str | None) -> tuple[str, bool]:
    """
    Evita 401 por pegados en Railway/UI: espacios, saltos, comillas o prefijo Bearer.
    Las claves sk-… no contienen espacios internos válidos; eliminar whitespace es seguro.
    """
    if not raw:
        return "", False
    before_naive = raw.strip()
    s = before_naive
    if len(s) >= 2 and ((s[0] == '"' and s[-1] == '"') or (s[0] == "'" and s[-1] == "'")):
        s = s[1:-1].strip()
    if s.lower().startswith("bearer "):
        s = s[7:].strip()
    s = "".join(s.split())
    return s, s != before_naive
