"""Arranque local: uv run python -m crew_shadow_crewai"""

import os

import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "crew_shadow_crewai.main:app",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "8080")),
        reload=os.environ.get("UVICORN_RELOAD", "") == "1",
    )
