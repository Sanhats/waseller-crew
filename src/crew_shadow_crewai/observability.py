"""Líneas de log en JSON (una por evento) para Railway / agregadores."""

from __future__ import annotations

import json
from typing import Any


def structured_log_line(event: str, **fields: Any) -> str:
    """Serializa un evento y campos opcionales en una sola línea JSON."""
    payload: dict[str, Any] = {"event": event}
    for key, val in fields.items():
        if val is not None:
            payload[key] = val
    return json.dumps(payload, ensure_ascii=False)
