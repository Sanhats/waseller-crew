"""Auth opcional Bearer para POST /shadow-compare (contrato v1.1)."""

from __future__ import annotations

import logging
import os

from fastapi import HTTPException, Request

log = logging.getLogger(__name__)


def _truthy_env(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes")


def validate_shadow_compare_bearer(*, authorization: str | None) -> None:
    """
    Si SHADOW_COMPARE_REQUIRE_AUTH está activo, exige Authorization: Bearer
    igual a SHADOW_COMPARE_SECRET (mismo valor que LLM_SHADOW_COMPARE_SECRET en workers).
    """
    if not _truthy_env("SHADOW_COMPARE_REQUIRE_AUTH"):
        return
    secret = (os.environ.get("SHADOW_COMPARE_SECRET") or "").strip()
    if not secret:
        log.error("SHADOW_COMPARE_REQUIRE_AUTH=true pero SHADOW_COMPARE_SECRET vacío")
        raise HTTPException(
            status_code=500,
            detail="auth requerido pero SHADOW_COMPARE_SECRET no configurado",
        )
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Authorization Bearer requerido")
    token = authorization.split(" ", 1)[1].strip()
    if token != secret:
        raise HTTPException(status_code=401, detail="token inválido")


def check_shadow_compare_bearer(request: Request) -> None:
    validate_shadow_compare_bearer(authorization=request.headers.get("Authorization"))
