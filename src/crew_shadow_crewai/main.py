import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI

from crew_shadow_crewai.observability import structured_log_line
from crew_shadow_crewai.routes import router

_root = Path(__file__).resolve().parents[2]
load_dotenv(_root / ".env")
load_dotenv(_root / ".env.local", override=True)


def _normalize_openai_api_key(raw: str | None) -> tuple[str, bool]:
    """
    Evita 401 por pegados en Railway/UI: espacios, saltos de línea, comillas o prefijo Bearer.
    Las claves sk-… no contienen espacios; eliminar whitespace interno es seguro.
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


_raw_key = os.environ.get("OPENAI_API_KEY")
_norm_key, _key_was_normalized = _normalize_openai_api_key(_raw_key)
if _raw_key is not None:
    os.environ["OPENAI_API_KEY"] = _norm_key

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("crew_shadow_crewai")

_key = os.environ.get("OPENAI_API_KEY") or ""
_stub = os.environ.get("USE_CREW_STUB", "").strip().lower() in ("1", "true", "yes")
log.info(
    structured_log_line(
        "startup_env",
        openai_key_configured=bool(_key),
        openai_key_length=len(_key) if _key else 0,
        openai_key_last4=_key[-4:] if len(_key) >= 4 else None,
        openai_key_normalized=_key_was_normalized,
        use_crew_stub=_stub,
    )
)

app = FastAPI(title="Waseller shadow compare", version="0.1.0")
app.include_router(router)


@app.get("/health")
def health() -> dict[str, bool]:
    return {"ok": True}
