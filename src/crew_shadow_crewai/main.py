import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI

from crew_shadow_crewai.routes import router

_root = Path(__file__).resolve().parents[2]
load_dotenv(_root / ".env")
load_dotenv(_root / ".env.local", override=True)

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("crew_shadow_crewai")

app = FastAPI(title="Waseller shadow compare", version="0.1.0")
app.include_router(router)


@app.get("/health")
def health() -> dict[str, bool]:
    return {"ok": True}
