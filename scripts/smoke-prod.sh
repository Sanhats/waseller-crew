#!/usr/bin/env bash
# Smoke contra el crew desplegado (p. ej. Railway). No commitees secretos.
# Uso (desde la raíz del repo):
#   export CREW_BASE_URL="https://tu-app.up.railway.app"
#   export SHADOW_COMPARE_SECRET="..."   # mismo valor que LLM_SHADOW_COMPARE_SECRET si auth en prod
#   ./scripts/smoke-prod.sh
# Opcional: primer arg = fixture (default fixtures/request.v1_1.example.json)

set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

CREW_BASE_URL="${CREW_BASE_URL:?Definí CREW_BASE_URL, ej. https://tu-app.up.railway.app (sin barra final)}"
FIXTURE="${1:-fixtures/request.v1_1.example.json}"
PATH_SUFFIX="${CREW_SHADOW_PATH:-/v1/shadow-compare}"

echo "== GET ${CREW_BASE_URL}/health"
curl -sS "${CREW_BASE_URL}/health"
echo ""

AUTH=()
if [[ -n "${SHADOW_COMPARE_SECRET:-}" ]]; then
  AUTH=( -H "Authorization: Bearer ${SHADOW_COMPARE_SECRET}" )
  echo "== POST ${CREW_BASE_URL}${PATH_SUFFIX} (con Bearer)"
else
  echo "== POST ${CREW_BASE_URL}${PATH_SUFFIX} (sin Bearer — solo si prod no exige auth)"
fi

curl -sS -w "\nHTTP %{http_code}\n" \
  "${CREW_BASE_URL}${PATH_SUFFIX}" \
  -H "Content-Type: application/json" \
  "${AUTH[@]}" \
  -d @"${FIXTURE}"
