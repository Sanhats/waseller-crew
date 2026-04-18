import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("USE_CREW_STUB", "1")

from crew_shadow_crewai.main import app  # noqa: E402


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_health(client: TestClient) -> None:
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"ok": True}


def test_shadow_compare_stub(client: TestClient) -> None:
    fixture = Path(__file__).resolve().parents[1] / "fixtures" / "request.example.json"
    body = json.loads(fixture.read_text(encoding="utf-8"))
    r = client.post("/shadow-compare", json=body)
    assert r.status_code == 200
    data = r.json()
    assert data["candidateDecision"] is not None
    assert "[crew-stub]" in data["candidateDecision"]["draftReply"]
    assert data["candidateDecision"]["nextAction"] == "reply_only"
    assert data["candidateInterpretation"] is not None
    assert data["candidateInterpretation"]["intent"] == "consultar_precio"
    assert data["candidateInterpretation"]["nextAction"] == "reply_only"
    assert data["candidateInterpretation"]["source"] == "openai"


def test_shadow_response_from_crew_dict_nested_and_flat() -> None:
    from crew_shadow_crewai.crew_app import _shadow_response_from_crew_dict

    nested = {
        "candidateDecision": {
            "draftReply": "hola",
            "nextAction": "reply_only",
            "recommendedAction": "reply_only",
            "confidence": 0.5,
        },
        "candidateInterpretation": {
            "intent": "x",
            "confidence": 0.9,
            "source": "rules",
            "nextAction": "manual_review",
        },
    }
    r1 = _shadow_response_from_crew_dict(nested)
    assert r1.candidateDecision is not None
    assert r1.candidateDecision.draftReply == "hola"
    assert r1.candidateInterpretation is not None
    assert r1.candidateInterpretation.source == "rules"
    assert r1.candidateInterpretation.nextAction == "manual_review"

    flat = {
        "draftReply": "solo",
        "nextAction": "close_lead",
        "recommendedAction": "close_lead",
    }
    r2 = _shadow_response_from_crew_dict(flat)
    assert r2.candidateInterpretation is None
    assert r2.candidateDecision is not None
    assert r2.candidateDecision.draftReply == "solo"


def test_repair_utf8_mojibake() -> None:
    from crew_shadow_crewai.text_encoding import repair_utf8_mojibake

    assert repair_utf8_mojibake("Â¿Te reservo?") == "¿Te reservo?"
    assert repair_utf8_mojibake("QuerÃ©s que te reserve") == "Querés que te reserve"
    assert repair_utf8_mojibake(None) is None
    assert repair_utf8_mojibake("ASCII ok") == "ASCII ok"
    assert repair_utf8_mojibake("¿Te reservo una?") == "¿Te reservo una?"
    assert repair_utf8_mojibake("Precio: $15000, envío a Córdoba.") == "Precio: $15000, envío a Córdoba."
    mixed = chr(0xC2) + chr(0xBF) + "Quer" + chr(0xC3) + chr(0xA9) + "s\u2019acá"
    fixed = repair_utf8_mojibake(mixed)
    assert "\u00c2" not in fixed
    assert "Quer" in fixed and "s" in fixed


def test_candidate_decision_repairs_draftreply_mojibake() -> None:
    from crew_shadow_crewai.models import CandidateDecision

    bad = (
        "La remera negra talle M sale $15000. "
        + chr(0xC2)
        + chr(0xBF)
        + "Quer"
        + chr(0xC3)
        + chr(0xA9)
        + "s?"
    )
    d = CandidateDecision(
        draftReply=bad,
        intent="consultar_precio",
        nextAction="reply_only",
        recommendedAction="reply_only",
        confidence=0.72,
        reason="ok",
    )
    assert chr(0xC2) not in (d.draftReply or "")
    assert "¿" in (d.draftReply or "")


def test_shadow_compare_v1_1_fixture_stub(client: TestClient) -> None:
    fixture = Path(__file__).resolve().parents[1] / "fixtures" / "request.v1_1.example.json"
    body = json.loads(fixture.read_text(encoding="utf-8"))
    r = client.post("/shadow-compare", json=body)
    assert r.status_code == 200
    data = r.json()
    assert data["candidateDecision"] is not None
    assert "[crew-stub]" in data["candidateDecision"]["draftReply"]


def test_shadow_compare_bearer_required(monkeypatch: pytest.MonkeyPatch, client: TestClient) -> None:
    monkeypatch.setenv("SHADOW_COMPARE_REQUIRE_AUTH", "true")
    monkeypatch.setenv("SHADOW_COMPARE_SECRET", "test-secret-xyz")
    fixture = Path(__file__).resolve().parents[1] / "fixtures" / "request.example.json"
    body = json.loads(fixture.read_text(encoding="utf-8"))
    r = client.post("/shadow-compare", json=body)
    assert r.status_code == 401
    r2 = client.post(
        "/shadow-compare",
        json=body,
        headers={"Authorization": "Bearer wrong"},
    )
    assert r2.status_code == 401
    r3 = client.post(
        "/shadow-compare",
        json=body,
        headers={"Authorization": "Bearer test-secret-xyz"},
    )
    assert r3.status_code == 200


def test_shadow_compare_unsupported_kind(client: TestClient) -> None:
    r = client.post(
        "/shadow-compare",
        json={
            "schemaVersion": 1,
            "kind": "otro.kind",
            "tenantId": "00000000-0000-4000-8000-000000000001",
            "leadId": "00000000-0000-4000-8000-000000000002",
            "incomingText": "hola",
            "interpretation": {},
            "baselineDecision": {},
        },
    )
    assert r.status_code == 400
    assert r.json()["detail"] == "unsupported kind"
