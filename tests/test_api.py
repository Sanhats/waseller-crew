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


def test_pick_raw_openai_api_key_crew_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    from crew_shadow_crewai.openai_env import pick_raw_openai_api_key_from_environ

    monkeypatch.setenv("OPENAI_API_KEY", "sk-from-openai")
    monkeypatch.setenv("CREW_OPENAI_API_KEY", "sk-from-crew")
    raw, src = pick_raw_openai_api_key_from_environ()
    assert raw == "sk-from-crew"
    assert src == "CREW_OPENAI_API_KEY"


def test_pick_raw_openai_api_key_falls_back_to_openai(monkeypatch: pytest.MonkeyPatch) -> None:
    from crew_shadow_crewai.openai_env import pick_raw_openai_api_key_from_environ

    monkeypatch.delenv("CREW_OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-only-openai")
    raw, src = pick_raw_openai_api_key_from_environ()
    assert raw == "sk-only-openai"
    assert src == "OPENAI_API_KEY"


def test_normalize_openai_api_key() -> None:
    from crew_shadow_crewai.openai_env import normalize_openai_api_key

    k, changed = normalize_openai_api_key('  "sk-test123"\n  ')
    assert k == "sk-test123"
    assert changed is True
    k2, c2 = normalize_openai_api_key("sk-abc")
    assert k2 == "sk-abc"
    assert c2 is False
    k3, c3 = normalize_openai_api_key("sk-te\u200bst")
    assert k3 == "sk-test"
    assert c3 is True


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


def test_v1_shadow_compare_alias_matches_shadow_compare(client: TestClient) -> None:
    fixture = Path(__file__).resolve().parents[1] / "fixtures" / "request.example.json"
    body = json.loads(fixture.read_text(encoding="utf-8"))
    r1 = client.post("/shadow-compare", json=body)
    r2 = client.post("/v1/shadow-compare", json=body)
    assert r1.status_code == 200 and r2.status_code == 200
    assert r1.json() == r2.json()


def test_shadow_compare_invalid_business_profile_slug(client: TestClient) -> None:
    fixture = Path(__file__).resolve().parents[1] / "fixtures" / "request.example.json"
    body = json.loads(fixture.read_text(encoding="utf-8"))
    body["businessProfileSlug"] = "no/valido"
    r = client.post("/shadow-compare", json=body)
    assert r.status_code == 422


def test_stock_table_row_cap() -> None:
    from crew_shadow_crewai.models import ShadowCompareRequest

    rows = [{"sku": str(i)} for i in range(505)]
    m = ShadowCompareRequest(
        schemaVersion=1,
        kind="waseller.shadow_compare.v1",
        tenantId="00000000-0000-4000-8000-000000000001",
        leadId="00000000-0000-4000-8000-000000000002",
        incomingText="hola",
        interpretation={},
        baselineDecision={},
        stockTable=rows,
    )
    assert len(m.stockTable or []) == 500


def test_shadow_compare_accepts_unknown_fields_extra_ignore(client: TestClient) -> None:
    fixture = Path(__file__).resolve().parents[1] / "fixtures" / "request.example.json"
    body = json.loads(fixture.read_text(encoding="utf-8"))
    body["futureWasellerField"] = {"x": 1}
    body["anotherUnknown"] = True
    r = client.post("/shadow-compare", json=body)
    assert r.status_code == 200


def test_shadow_compare_inventory_narrowing_note(client: TestClient) -> None:
    fixture = Path(__file__).resolve().parents[1] / "fixtures" / "request.example.json"
    body = json.loads(fixture.read_text(encoding="utf-8"))
    body["inventoryNarrowingNote"] = "solo variantes activas"
    r = client.post("/shadow-compare", json=body)
    assert r.status_code == 200


def test_shadow_compare_accepts_tenant_commercial_context(client: TestClient) -> None:
    fixture = Path(__file__).resolve().parents[1] / "fixtures" / "request.example.json"
    body = json.loads(fixture.read_text(encoding="utf-8"))
    body["tenantCommercialContext"] = "Aceptamos transferencia y efectivo. Local abierto lun–sáb 9–18."
    r = client.post("/shadow-compare", json=body)
    assert r.status_code == 200


def test_shadow_compare_tenant_commercial_context_max_length(client: TestClient) -> None:
    fixture = Path(__file__).resolve().parents[1] / "fixtures" / "request.example.json"
    body = json.loads(fixture.read_text(encoding="utf-8"))
    body["tenantCommercialContext"] = "x" * 6001
    r = client.post("/shadow-compare", json=body)
    assert r.status_code == 422


def test_enrich_empty_draft_reply_from_baseline() -> None:
    from crew_shadow_crewai.crew_app import _enrich_empty_draft_reply
    from crew_shadow_crewai.models import CandidateDecision, ShadowCompareRequest, ShadowCompareResponse

    body = ShadowCompareRequest(
        schemaVersion=1,
        kind="waseller.shadow_compare.v1",
        tenantId="00000000-0000-4000-8000-000000000001",
        leadId="00000000-0000-4000-8000-000000000002",
        incomingText="hola",
        interpretation={},
        baselineDecision={"draftReply": "Texto baseline"},
    )
    resp = ShadowCompareResponse(
        candidateDecision=CandidateDecision(
            draftReply="   ",
            nextAction="reply_only",
            recommendedAction="reply_only",
        )
    )
    out = _enrich_empty_draft_reply(resp, body)
    assert out.candidateDecision is not None
    assert out.candidateDecision.draftReply == "Texto baseline"


def test_variant_regex_matches_en_que_color_tenes() -> None:
    from crew_shadow_crewai.draft_variant_guard import incoming_asks_variant_clarification

    assert incoming_asks_variant_clarification("en que color tenes?") is True
    assert incoming_asks_variant_clarification("en qué color tenés?") is True
    assert incoming_asks_variant_clarification("que color tendrias?") is True


def test_negation_followup_guard_rewrites_duplicate() -> None:
    from crew_shadow_crewai.draft_variant_guard import apply_followup_draft_guards
    from crew_shadow_crewai.models import (
        CandidateDecision,
        RecentMessageItem,
        ShadowCompareRequest,
        ShadowCompareResponse,
    )

    prev = "Te confirmo Mesa de algarrobo: precio $195.000 y 2 unidad(es) disponibles. ¿Querés reserva?"
    body = ShadowCompareRequest(
        schemaVersion=1,
        kind="waseller.shadow_compare.v1",
        tenantId="00000000-0000-4000-8000-000000000001",
        leadId="00000000-0000-4000-8000-000000000002",
        incomingText="no, no quiero la mesa de algarrobo",
        interpretation={},
        baselineDecision={"draftReply": prev},
        recentMessages=[RecentMessageItem(direction="outgoing", message=prev)],
        stockTable=[{"name": "Mesa algarrobo", "stock": 2}],
    )
    resp = ShadowCompareResponse(candidateDecision=CandidateDecision(draftReply=prev))
    out = apply_followup_draft_guards(body, resp)
    assert out.candidateDecision is not None
    dr = out.candidateDecision.draftReply or ""
    assert dr != prev
    assert "negation_followup_guard" in (out.candidateDecision.reason or "")
    assert "inventario" in dr.lower()


def test_price_followup_guard_rewrites_duplicate() -> None:
    from crew_shadow_crewai.draft_variant_guard import apply_followup_draft_guards
    from crew_shadow_crewai.models import (
        CandidateDecision,
        RecentMessageItem,
        ShadowCompareRequest,
        ShadowCompareResponse,
    )

    prev = (
        "Sí, tengo Mesa de algarrobo en talle L, color marron claro. Sale $195.000. "
        "Tengo 2 unidad(es) disponible(s). ¿Querés que te reserve una?"
    )
    body = ShadowCompareRequest(
        schemaVersion=1,
        kind="waseller.shadow_compare.v1",
        tenantId="00000000-0000-4000-8000-000000000001",
        leadId="00000000-0000-4000-8000-000000000002",
        incomingText="precio?",
        interpretation={},
        baselineDecision={"draftReply": prev},
        recentMessages=[RecentMessageItem(direction="outgoing", message=prev)],
        stockTable=[{"name": "Mesa", "color": "marrón claro", "stock": 2}],
    )
    resp = ShadowCompareResponse(candidateDecision=CandidateDecision(draftReply=prev))
    out = apply_followup_draft_guards(body, resp)
    assert out.candidateDecision is not None
    dr = out.candidateDecision.draftReply or ""
    assert dr != prev
    assert "$195" in dr or "195.000" in dr
    assert "price_followup_guard" in (out.candidateDecision.reason or "")


def test_variant_guard_rewrites_duplicate_color_followup() -> None:
    from crew_shadow_crewai.draft_variant_guard import apply_variant_followup_guard
    from crew_shadow_crewai.models import (
        CandidateDecision,
        RecentMessageItem,
        ShadowCompareRequest,
        ShadowCompareResponse,
    )

    prev = (
        "Sí, tengo Mesa de algarrobo en talle L, color marron claro y modelo mesa algarrobo. "
        "Sale $195.000. Tengo 2 unidad(es) disponible(s). ¿Querés que te reserve una?"
    )
    body = ShadowCompareRequest(
        schemaVersion=1,
        kind="waseller.shadow_compare.v1",
        tenantId="00000000-0000-4000-8000-000000000001",
        leadId="00000000-0000-4000-8000-000000000002",
        incomingText="tenes otro color?",
        interpretation={},
        baselineDecision={"draftReply": prev},
        recentMessages=[
            RecentMessageItem(direction="incoming", message="hola, tienen Mesa de algarrobo?"),
            RecentMessageItem(direction="outgoing", message=prev),
        ],
        stockTable=[{"name": "Mesa algarrobo", "color": "marrón claro", "talle": "L"}],
    )
    resp = ShadowCompareResponse(candidateDecision=CandidateDecision(draftReply=prev))
    out = apply_variant_followup_guard(body, resp)
    assert out.candidateDecision is not None
    dr = out.candidateDecision.draftReply or ""
    assert dr != prev
    assert "color" in dr.lower()
    assert "figura" in dr.lower() or "solo" in dr.lower()
    assert "variant_guard" in (out.candidateDecision.reason or "")


def test_variant_guard_skips_when_two_colors_in_stock() -> None:
    from crew_shadow_crewai.draft_variant_guard import apply_variant_followup_guard
    from crew_shadow_crewai.models import (
        CandidateDecision,
        RecentMessageItem,
        ShadowCompareRequest,
        ShadowCompareResponse,
    )

    prev = "Misma ficha repetida."
    body = ShadowCompareRequest(
        schemaVersion=1,
        kind="waseller.shadow_compare.v1",
        tenantId="00000000-0000-4000-8000-000000000001",
        leadId="00000000-0000-4000-8000-000000000002",
        incomingText="otro color?",
        interpretation={},
        baselineDecision={"draftReply": prev},
        recentMessages=[
            RecentMessageItem(direction="outgoing", message=prev),
        ],
        stockTable=[
            {"name": "A", "color": "rojo"},
            {"name": "A", "color": "azul"},
        ],
    )
    resp = ShadowCompareResponse(candidateDecision=CandidateDecision(draftReply=prev))
    out = apply_variant_followup_guard(body, resp)
    assert out.candidateDecision is not None
    assert out.candidateDecision.draftReply == prev


def test_variant_guard_uses_baseline_when_no_recent_outgoing() -> None:
    from crew_shadow_crewai.draft_variant_guard import apply_variant_followup_guard
    from crew_shadow_crewai.models import CandidateDecision, ShadowCompareRequest, ShadowCompareResponse

    prev = (
        "Sí, tengo Mesa de algarrobo en talle L, color marron claro. Sale $195.000. "
        "Tengo 2 unidad(es) disponible(s)."
    )
    body = ShadowCompareRequest(
        schemaVersion=1,
        kind="waseller.shadow_compare.v1",
        tenantId="00000000-0000-4000-8000-000000000001",
        leadId="00000000-0000-4000-8000-000000000002",
        incomingText="¿Tenés en otro color?",
        interpretation={},
        baselineDecision={"draftReply": prev},
        recentMessages=None,
        stockTable=[{"nombre": "Mesa", "Color": "marrón claro"}],
    )
    resp = ShadowCompareResponse(candidateDecision=CandidateDecision(draftReply=prev))
    out = apply_variant_followup_guard(body, resp)
    assert out.candidateDecision is not None
    assert out.candidateDecision.draftReply != prev


def test_followup_guards_multi_variant_list_on_duplicate() -> None:
    from crew_shadow_crewai.draft_variant_guard import apply_followup_draft_guards
    from crew_shadow_crewai.models import (
        CandidateDecision,
        RecentMessageItem,
        ShadowCompareRequest,
        ShadowCompareResponse,
    )

    prev = "Misma ficha repetida."
    body = ShadowCompareRequest(
        schemaVersion=1,
        kind="waseller.shadow_compare.v1",
        tenantId="00000000-0000-4000-8000-000000000001",
        leadId="00000000-0000-4000-8000-000000000002",
        incomingText="otro color?",
        interpretation={},
        baselineDecision={"draftReply": prev},
        recentMessages=[RecentMessageItem(direction="outgoing", message=prev)],
        stockTable=[
            {"name": "A", "color": "rojo", "stock": 1},
            {"name": "A", "color": "azul", "stock": 2},
        ],
    )
    resp = ShadowCompareResponse(candidateDecision=CandidateDecision(draftReply=prev))
    out = apply_followup_draft_guards(body, resp)
    assert out.candidateDecision is not None
    dr = out.candidateDecision.draftReply or ""
    assert dr != prev
    assert "rojo" in dr.lower()
    assert "azul" in dr.lower()
    assert "multi_variant_list_guard" in (out.candidateDecision.reason or "")


def test_followup_guards_quantity_over_stock() -> None:
    from crew_shadow_crewai.draft_variant_guard import apply_followup_draft_guards
    from crew_shadow_crewai.models import (
        CandidateDecision,
        RecentMessageItem,
        ShadowCompareRequest,
        ShadowCompareResponse,
    )

    prev = "Producto X $1000, tengo 2 disponibles, ¿te lo reservo?"
    body = ShadowCompareRequest(
        schemaVersion=1,
        kind="waseller.shadow_compare.v1",
        tenantId="00000000-0000-4000-8000-000000000001",
        leadId="00000000-0000-4000-8000-000000000002",
        incomingText="quiero 15 unidades",
        interpretation={},
        baselineDecision={"draftReply": prev},
        recentMessages=[RecentMessageItem(direction="outgoing", message=prev)],
        stockTable=[{"name": "X", "stock": 2}],
    )
    resp = ShadowCompareResponse(candidateDecision=CandidateDecision(draftReply=prev))
    out = apply_followup_draft_guards(body, resp)
    assert out.candidateDecision is not None
    dr = out.candidateDecision.draftReply or ""
    assert "15" in dr
    assert "2" in dr
    assert "quantity_stock_guard" in (out.candidateDecision.reason or "")


def test_followup_guards_catalog_scope_duplicate() -> None:
    from crew_shadow_crewai.draft_variant_guard import apply_followup_draft_guards
    from crew_shadow_crewai.models import (
        CandidateDecision,
        RecentMessageItem,
        ShadowCompareRequest,
        ShadowCompareResponse,
    )

    prev = "Solo este producto en oferta, ¿te interesa?"
    body = ShadowCompareRequest(
        schemaVersion=1,
        kind="waseller.shadow_compare.v1",
        tenantId="00000000-0000-4000-8000-000000000001",
        leadId="00000000-0000-4000-8000-000000000002",
        incomingText="tenés catálogo completo?",
        interpretation={},
        baselineDecision={"draftReply": prev},
        recentMessages=[RecentMessageItem(direction="outgoing", message=prev)],
        stockTable=[{"name": "Uno solo", "stock": 1}],
        inventoryNarrowingNote="Solo variantes del producto consultado.",
    )
    resp = ShadowCompareResponse(candidateDecision=CandidateDecision(draftReply=prev))
    out = apply_followup_draft_guards(body, resp)
    assert out.candidateDecision is not None
    dr = out.candidateDecision.draftReply or ""
    assert "stockTable" in dr
    assert "catalog_scope_guard" in (out.candidateDecision.reason or "")


def test_followup_guards_generic_envio_duplicate() -> None:
    from crew_shadow_crewai.draft_variant_guard import apply_followup_draft_guards
    from crew_shadow_crewai.models import (
        CandidateDecision,
        RecentMessageItem,
        ShadowCompareRequest,
        ShadowCompareResponse,
    )

    prev = "Remera azul $5000, ¿te la reservo?"
    body = ShadowCompareRequest(
        schemaVersion=1,
        kind="waseller.shadow_compare.v1",
        tenantId="00000000-0000-4000-8000-000000000001",
        leadId="00000000-0000-4000-8000-000000000002",
        incomingText="y el envío cuánto sale?",
        interpretation={},
        baselineDecision={"draftReply": prev},
        recentMessages=[RecentMessageItem(direction="outgoing", message=prev)],
        stockTable=[{"name": "Remera", "stock": 3}],
    )
    resp = ShadowCompareResponse(candidateDecision=CandidateDecision(draftReply=prev))
    out = apply_followup_draft_guards(body, resp)
    assert out.candidateDecision is not None
    dr = out.candidateDecision.draftReply or ""
    assert dr != prev
    assert "envío" in dr.lower() or "entrega" in dr.lower()
    assert "generic_followup_dedupe_guard" in (out.candidateDecision.reason or "")


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
