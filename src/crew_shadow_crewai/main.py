import crew_shadow_crewai.bootstrap_env  # noqa: F401 — antes de importar rutas/crewai

import hashlib
import logging
import os
import urllib.error
import urllib.request

from fastapi import FastAPI

from crew_shadow_crewai.observability import structured_log_line
from crew_shadow_crewai.openai_env import (
    normalize_openai_api_key,
    pick_raw_openai_api_key_from_environ,
)
from crew_shadow_crewai.routes import router

_raw_key, _key_env_source = pick_raw_openai_api_key_from_environ()
_norm_key, _key_was_normalized = normalize_openai_api_key(_raw_key)
if _key_env_source != "none":
    os.environ["OPENAI_API_KEY"] = _norm_key

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("crew_shadow_crewai")


def _probe_openai_key_http_status(key: str) -> int:
    """GET https://api.openai.com/v1/models (misma comprobación que curl)."""
    req = urllib.request.Request(
        "https://api.openai.com/v1/models",
        headers={"Authorization": f"Bearer {key}"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return int(resp.status)
    except urllib.error.HTTPError as e:
        return int(e.code)


_key = os.environ.get("OPENAI_API_KEY") or ""
_stub = os.environ.get("USE_CREW_STUB", "").strip().lower() in ("1", "true", "yes")
_model = (os.environ.get("OPENAI_MODEL_NAME") or "gpt-4o-mini").strip()
_key_fp = (
    hashlib.sha256(_key.encode("utf-8")).hexdigest()[:12]
    if _key
    else None
)
log.info(
    structured_log_line(
        "startup_env",
        openai_key_configured=bool(_key),
        openai_key_length=len(_key) if _key else 0,
        openai_key_last4=_key[-4:] if len(_key) >= 4 else None,
        openai_key_fingerprint=_key_fp,
        openai_key_env_source=_key_env_source,
        openai_key_normalized=_key_was_normalized,
        openai_model_name=_model,
        use_crew_stub=_stub,
    )
)

if (
    _key
    and not _stub
    and os.environ.get("OPENAI_STARTUP_PROBE", "").strip().lower() in ("1", "true", "yes")
):
    try:
        code = _probe_openai_key_http_status(_key)
        log.info(
            structured_log_line(
                "openai_api_probe",
                endpoint="GET https://api.openai.com/v1/models",
                http_status=code,
                openai_key_last4=_key[-4:] if len(_key) >= 4 else None,
            )
        )
        if code != 200:
            log.error(
                "openai_api_probe falló: la clave (CREW_OPENAI_API_KEY o OPENAI_API_KEY) no es aceptada por OpenAI. "
                "Generá una nueva en https://platform.openai.com/api-keys y actualizá la variable en Railway."
            )
    except OSError as e:
        log.warning(structured_log_line("openai_api_probe_error", error=str(e)))

app = FastAPI(title="Waseller shadow compare", version="0.1.0")
app.include_router(router)


@app.get("/health")
def health() -> dict[str, bool]:
    return {"ok": True}
