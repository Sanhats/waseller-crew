"""
Variables de entorno que deben aplicarse antes de importar CrewAI (p. ej. en main).

En procesos sin TTY, el flujo "first-time trace" de CrewAI imprime un panel Rich
("Tracing Preference Saved") en cada kickoff. CREWAI_TESTING desactiva ese camino.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

_root = Path(__file__).resolve().parents[2]
load_dotenv(_root / ".env")
load_dotenv(_root / ".env.local", override=True)
os.environ.setdefault("CREWAI_TESTING", "true")
