"""Reparación de mojibake UTF-8/Latin-1 (salida LLM / JSON)."""

from __future__ import annotations

import ftfy


def looks_like_utf8_mojibake_as_latin1(s: str) -> bool:
    """
    Detecta UTF-8 de 2 bytes leído como dos caracteres Latin-1
    (p. ej. U+00C2 U+00BF en lugar de U+00BF para '¿').
    """
    o = [ord(c) for c in s]
    for i in range(len(o) - 1):
        if 0xC2 <= o[i] <= 0xDF and 0x80 <= o[i + 1] <= 0xBF:
            return True
    return False


def _latin1_utf8_layers(s: str, rounds: int = 5) -> str:
    t = s
    for _ in range(rounds):
        try:
            n = t.encode("latin-1").decode("utf-8")
        except (UnicodeDecodeError, UnicodeEncodeError):
            return t
        if n == t:
            return t
        t = n
    return t


def repair_utf8_mojibake(s: str | None) -> str | None:
    """
    Corrige mojibake solo si hay patrón; si no, devuelve el string igual (no romper UTF-8 válido).
    """
    if not s:
        return s
    if not looks_like_utf8_mojibake_as_latin1(s):
        return s
    t = _latin1_utf8_layers(s)
    if looks_like_utf8_mojibake_as_latin1(t):
        t = _latin1_utf8_layers(ftfy.fix_text(t))
    return t
