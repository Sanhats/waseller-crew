import json
import logging
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


def test_incoming_suggests_stop_variants() -> None:
    from crew_shadow_crewai.draft_variant_guard import incoming_suggests_stop_or_rejection

    assert incoming_suggests_stop_or_rejection("no gracias!") is True
    assert incoming_suggests_stop_or_rejection("gracias no") is True
    assert incoming_suggests_stop_or_rejection("nop") is True
    assert incoming_suggests_stop_or_rejection("no mejor no") is True


def test_incoming_signals_product_or_topic_pivot() -> None:
    from crew_shadow_crewai.draft_variant_guard import incoming_signals_product_or_topic_pivot

    assert incoming_signals_product_or_topic_pivot("¿Tienen mesa de exterior?") is True
    assert incoming_signals_product_or_topic_pivot("no mejor no, buscaba mesa de exterior en su lugar") is True
    assert incoming_signals_product_or_topic_pivot("no quiero eso") is True
    assert incoming_signals_product_or_topic_pivot("¿Tenés en rojo?") is False


def test_topic_pivot_followup_guard_rewrites_repeated_talle_script() -> None:
    from crew_shadow_crewai.draft_variant_guard import apply_followup_draft_guards
    from crew_shadow_crewai.models import (
        CandidateDecision,
        RecentMessageItem,
        ShadowCompareRequest,
        ShadowCompareResponse,
    )

    prev = (
        "Tenemos Mesa de algarrobo disponible por $150.000. Decime qué talle buscás y te confirmo en el momento."
    )
    body = ShadowCompareRequest(
        schemaVersion=1,
        kind="waseller.shadow_compare.v1",
        tenantId="00000000-0000-4000-8000-000000000001",
        leadId="00000000-0000-4000-8000-000000000002",
        incomingText="¿Tienen mesa de exterior?",
        interpretation={},
        baselineDecision={"draftReply": prev},
        recentMessages=[RecentMessageItem(direction="outgoing", message=prev)],
        stockTable=[{"name": "Mesa de algarrobo", "stock": 3}],
    )
    resp = ShadowCompareResponse(candidateDecision=CandidateDecision(draftReply=prev))
    out = apply_followup_draft_guards(body, resp)
    assert out.candidateDecision is not None
    assert "topic_pivot_followup_guard" in (out.candidateDecision.reason or "")
    dr = (out.candidateDecision.draftReply or "").lower()
    assert "cambiamos de eje" in dr
    assert prev.lower() not in dr


def test_topic_pivot_guard_skips_when_draft_acknowledges() -> None:
    from crew_shadow_crewai.draft_variant_guard import apply_topic_pivot_followup_guard
    from crew_shadow_crewai.models import CandidateDecision, ShadowCompareRequest, ShadowCompareResponse

    body = ShadowCompareRequest(
        schemaVersion=1,
        kind="waseller.shadow_compare.v1",
        tenantId="00000000-0000-4000-8000-000000000001",
        leadId="00000000-0000-4000-8000-000000000002",
        incomingText="¿Tienen mesa de exterior?",
        interpretation={},
        baselineDecision={},
        stockTable=[{"name": "Mesa de algarrobo", "stock": 1}],
    )
    good = (
        "Entiendo que ahora buscás mesa de exterior. En stockTable solo figura la mesa de algarrobo interior; "
        "no veo una fila de exterior acá. ¿Seguimos con esa línea o preferís que te derive?"
    )
    resp = ShadowCompareResponse(candidateDecision=CandidateDecision(draftReply=good))
    out = apply_topic_pivot_followup_guard(body, resp)
    assert out.candidateDecision is not None
    assert out.candidateDecision.draftReply == good


def test_shadow_compare_stub(client: TestClient) -> None:
    fixture = Path(__file__).resolve().parents[1] / "fixtures" / "request.example.json"
    body = json.loads(fixture.read_text(encoding="utf-8"))
    r = client.post("/shadow-compare", json=body)
    assert r.status_code == 200
    data = r.json()
    assert data["candidateDecision"] is not None
    reason = data["candidateDecision"].get("reason") or ""
    assert reason.startswith("stub")
    dr = data["candidateDecision"]["draftReply"] or ""
    assert dr.strip()
    # El stub se enriquece con baseline y luego pasan los guards (p. ej. precio duplicado vs baseline).
    assert "[crew-stub]" in dr or "price_followup_guard" in reason
    assert data["candidateDecision"]["nextAction"] == "reply_only"
    assert data["candidateInterpretation"] is not None
    assert data["candidateInterpretation"]["intent"] == "consultar_precio"
    assert data["candidateInterpretation"]["nextAction"] == "reply_only"
    assert data["candidateInterpretation"]["source"] == "openai"


def test_shadow_compare_stub_negation_rewrites_pitch(client: TestClient) -> None:
    """Stub + guards: negación corta no debe dejar el mismo pitch de reserva del baseline."""
    body = {
        "schemaVersion": 1,
        "kind": "waseller.shadow_compare.v1",
        "tenantId": "00000000-0000-4000-8000-000000000001",
        "leadId": "00000000-0000-4000-8000-000000000002",
        "incomingText": "no gracias!",
        "interpretation": {},
        "baselineDecision": {
            "draftReply": (
                "Te confirmo Mesa de algarrobo: precio $195.000 y 2 unidad(es) disponibles. "
                "¿Querés que te reserve una ahora?"
            ),
            "nextAction": "offer_reservation",
            "recommendedAction": "offer_reservation",
            "confidence": 0.5,
            "reason": "baseline_waseller",
        },
        "stockTable": [{"name": "Mesa algarrobo", "stock": 2}],
        "publicCatalogSlug": "demo-tienda",
        "publicCatalogBaseUrl": "https://stock.ejemplo.app",
    }
    r = client.post("/shadow-compare", json=body)
    assert r.status_code == 200
    data = r.json()
    dr = (data["candidateDecision"] or {}).get("draftReply") or ""
    assert "Te confirmo" not in dr
    assert "querés que te reserve" not in dr.lower()
    assert "catálogo" in dr.lower()
    assert "https://stock.ejemplo.app/tienda/demo-tienda" in dr
    assert "negation_followup_guard" in (data["candidateDecision"].get("reason") or "")


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
    reason = data["candidateDecision"].get("reason") or ""
    assert reason.startswith("stub")
    dr = data["candidateDecision"]["draftReply"] or ""
    assert dr.strip()
    assert "[crew-stub]" in dr or "price_followup_guard" in reason


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


def test_mesa_colores_fixture_parses_and_posts(client: TestClient) -> None:
    """Diálogo mesa → colores con recentMessages + activeOffer (checklist integración)."""
    from crew_shadow_crewai.models import ShadowCompareRequest

    path = Path(__file__).resolve().parents[1] / "fixtures" / "request.mesa_colores.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    m = ShadowCompareRequest.model_validate(raw)
    assert m.incomingText == "¿Qué colores tenés?"
    assert m.activeOffer is not None
    assert m.activeOffer.get("productName") == "Mesa de algarrobo"
    assert m.memoryFacts and len(m.memoryFacts) >= 1
    r = client.post("/shadow-compare", json=raw)
    assert r.status_code == 200
    data = r.json()
    assert data.get("candidateDecision")
    dr = (data["candidateDecision"] or {}).get("draftReply") or ""
    assert len(dr.strip()) > 0


def test_tenant_runtime_context_fixture_parses() -> None:
    from crew_shadow_crewai.models import ShadowCompareRequest
    from crew_shadow_crewai.tenant_runtime_context import TenantRuntimeContextV1

    path = Path(__file__).resolve().parents[1] / "fixtures" / "request.tenant_runtime_context.v1.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    m = ShadowCompareRequest.model_validate(raw)
    assert m.tenantRuntimeContext is not None
    rtc = m.tenantRuntimeContext
    assert isinstance(rtc, TenantRuntimeContextV1)
    assert rtc.version == 1
    assert rtc.identity.displayName == "Muebles Demo SA"
    assert rtc.catalog.publicSlug == "demo-tienda-rtc"
    assert rtc.paymentChannels[0].provider == "mercadopago"
    assert rtc.channel is not None and rtc.channel.whatsAppBusinessNumber == "+5491100002222"


def test_shadow_compare_request_without_tenant_runtime_context() -> None:
    from crew_shadow_crewai.models import ShadowCompareRequest

    m = ShadowCompareRequest(
        schemaVersion=1,
        kind="waseller.shadow_compare.v1",
        tenantId="00000000-0000-4000-8000-000000000001",
        leadId="00000000-0000-4000-8000-000000000002",
        incomingText="hola",
        interpretation={},
        baselineDecision={"draftReply": "x"},
    )
    assert m.tenantRuntimeContext is None


def _minimal_tenant_runtime_context(*, identity_tenant_id: str):
    from crew_shadow_crewai.tenant_runtime_context import (
        TenantRuntimeCatalogV1,
        TenantRuntimeContextV1,
        TenantRuntimeIdentityV1,
        TenantRuntimeKnowledgeV1,
        TenantRuntimeLlmV1,
        TenantRuntimeOutboundMessagingV1,
        TenantRuntimeTimestampsV1,
    )

    return TenantRuntimeContextV1(
        version=1,
        identity=TenantRuntimeIdentityV1(
            tenantId=identity_tenant_id,
            displayName="X",
            plan="starter",
        ),
        knowledge=TenantRuntimeKnowledgeV1(businessCategory="c", businessLabels=[]),
        llm=TenantRuntimeLlmV1(
            assistEnabled=True,
            confidenceThreshold=0.5,
            guardrailsStrict=False,
            rolloutPercent=100,
            modelName="m",
        ),
        outboundMessaging=TenantRuntimeOutboundMessagingV1(
            senderRateMs=100, senderPauseEvery=10, senderPauseMs=1000
        ),
        catalog=TenantRuntimeCatalogV1(publicSlug=None, publicBaseUrl=None),
        paymentChannels=[],
        timestamps=TenantRuntimeTimestampsV1(
            tenantCreatedAt="2025-01-01T00:00:00Z", tenantUpdatedAt="2025-01-01T00:00:00Z"
        ),
    )


def test_tenant_runtime_identity_mismatch_warns(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from crew_shadow_crewai.models import ShadowCompareRequest

    monkeypatch.delenv("SHADOW_COMPARE_STRICT_TENANT_RUNTIME_IDENTITY", raising=False)
    rtc = _minimal_tenant_runtime_context(identity_tenant_id="00000000-0000-4000-8000-000000000099")
    with caplog.at_level(logging.WARNING):
        m = ShadowCompareRequest(
            schemaVersion=1,
            kind="waseller.shadow_compare.v1",
            tenantId="00000000-0000-4000-8000-000000000001",
            leadId="00000000-0000-4000-8000-000000000002",
            incomingText="hola",
            interpretation={},
            baselineDecision={"draftReply": "x"},
            tenantRuntimeContext=rtc,
        )
    assert m.tenantRuntimeContext is rtc
    assert "tenant_runtime_identity_mismatch" in caplog.text


def test_tenant_runtime_identity_mismatch_strict_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    from pydantic import ValidationError

    from crew_shadow_crewai.models import ShadowCompareRequest

    monkeypatch.setenv("SHADOW_COMPARE_STRICT_TENANT_RUNTIME_IDENTITY", "1")
    rtc = _minimal_tenant_runtime_context(identity_tenant_id="00000000-0000-4000-8000-000000000099")
    with pytest.raises(ValidationError) as exc:
        ShadowCompareRequest(
            schemaVersion=1,
            kind="waseller.shadow_compare.v1",
            tenantId="00000000-0000-4000-8000-000000000001",
            leadId="00000000-0000-4000-8000-000000000002",
            incomingText="hola",
            interpretation={},
            baselineDecision={"draftReply": "x"},
            tenantRuntimeContext=rtc,
        )
    assert "coincide" in str(exc.value).lower()


def test_tenant_runtime_identity_case_insensitive_match() -> None:
    from crew_shadow_crewai.models import ShadowCompareRequest

    same = "00000000-0000-4000-8000-000000000001"
    rtc = _minimal_tenant_runtime_context(identity_tenant_id=same.upper())
    m = ShadowCompareRequest(
        schemaVersion=1,
        kind="waseller.shadow_compare.v1",
        tenantId=same,
        leadId="00000000-0000-4000-8000-000000000002",
        incomingText="hola",
        interpretation={},
        baselineDecision={"draftReply": "x"},
        tenantRuntimeContext=rtc,
    )
    assert m.tenantRuntimeContext.identity.tenantId == same.upper()


def test_public_catalog_url_prefers_root_over_runtime_catalog() -> None:
    from crew_shadow_crewai.draft_variant_guard import public_catalog_full_url
    from crew_shadow_crewai.models import ShadowCompareRequest
    from crew_shadow_crewai.tenant_runtime_context import (
        TenantRuntimeCatalogV1,
        TenantRuntimeContextV1,
        TenantRuntimeIdentityV1,
        TenantRuntimeKnowledgeV1,
        TenantRuntimeLlmV1,
        TenantRuntimeOutboundMessagingV1,
        TenantRuntimeTimestampsV1,
    )

    rtc = TenantRuntimeContextV1(
        version=1,
        identity=TenantRuntimeIdentityV1(
            tenantId="00000000-0000-4000-8000-000000000001",
            displayName="X",
            plan="starter",
        ),
        knowledge=TenantRuntimeKnowledgeV1(businessCategory="c", businessLabels=[]),
        llm=TenantRuntimeLlmV1(
            assistEnabled=True,
            confidenceThreshold=0.5,
            guardrailsStrict=False,
            rolloutPercent=100,
            modelName="m",
        ),
        outboundMessaging=TenantRuntimeOutboundMessagingV1(
            senderRateMs=100, senderPauseEvery=10, senderPauseMs=1000
        ),
        catalog=TenantRuntimeCatalogV1(publicSlug="from-rtc", publicBaseUrl="https://rtc.example.com"),
        paymentChannels=[],
        timestamps=TenantRuntimeTimestampsV1(
            tenantCreatedAt="2025-01-01T00:00:00Z", tenantUpdatedAt="2025-01-01T00:00:00Z"
        ),
    )
    body = ShadowCompareRequest(
        schemaVersion=1,
        kind="waseller.shadow_compare.v1",
        tenantId="00000000-0000-4000-8000-000000000001",
        leadId="00000000-0000-4000-8000-000000000002",
        incomingText="hola",
        interpretation={},
        baselineDecision={"draftReply": "x"},
        publicCatalogSlug="from-root",
        publicCatalogBaseUrl="https://root.example.com",
        tenantRuntimeContext=rtc,
    )
    assert public_catalog_full_url(body) == "https://root.example.com/tienda/from-root"


def test_public_catalog_url_falls_back_to_runtime_catalog() -> None:
    from crew_shadow_crewai.draft_variant_guard import public_catalog_full_url
    from crew_shadow_crewai.models import ShadowCompareRequest
    from crew_shadow_crewai.tenant_runtime_context import (
        TenantRuntimeCatalogV1,
        TenantRuntimeContextV1,
        TenantRuntimeIdentityV1,
        TenantRuntimeKnowledgeV1,
        TenantRuntimeLlmV1,
        TenantRuntimeOutboundMessagingV1,
        TenantRuntimeTimestampsV1,
    )

    rtc = TenantRuntimeContextV1(
        version=1,
        identity=TenantRuntimeIdentityV1(
            tenantId="00000000-0000-4000-8000-000000000001",
            displayName="X",
            plan="starter",
        ),
        knowledge=TenantRuntimeKnowledgeV1(businessCategory="c", businessLabels=[]),
        llm=TenantRuntimeLlmV1(
            assistEnabled=True,
            confidenceThreshold=0.5,
            guardrailsStrict=False,
            rolloutPercent=100,
            modelName="m",
        ),
        outboundMessaging=TenantRuntimeOutboundMessagingV1(
            senderRateMs=100, senderPauseEvery=10, senderPauseMs=1000
        ),
        catalog=TenantRuntimeCatalogV1(publicSlug="from-rtc", publicBaseUrl="https://rtc.example.com"),
        paymentChannels=[],
        timestamps=TenantRuntimeTimestampsV1(
            tenantCreatedAt="2025-01-01T00:00:00Z", tenantUpdatedAt="2025-01-01T00:00:00Z"
        ),
    )
    body = ShadowCompareRequest(
        schemaVersion=1,
        kind="waseller.shadow_compare.v1",
        tenantId="00000000-0000-4000-8000-000000000001",
        leadId="00000000-0000-4000-8000-000000000002",
        incomingText="hola",
        interpretation={},
        baselineDecision={"draftReply": "x"},
        tenantRuntimeContext=rtc,
    )
    assert public_catalog_full_url(body) == "https://rtc.example.com/tienda/from-rtc"


def test_tenant_runtime_context_fixture_posts(client: TestClient) -> None:
    path = Path(__file__).resolve().parents[1] / "fixtures" / "request.tenant_runtime_context.v1.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    r = client.post("/shadow-compare", json=raw)
    assert r.status_code == 200
    assert r.json().get("candidateDecision")


def test_topic_pivot_enriched_stock_fixture_parses_and_posts(client: TestClient) -> None:
    """Ejemplo de stockTable ampliado tras giro a exterior (guía §4)."""
    from crew_shadow_crewai.models import ShadowCompareRequest

    path = Path(__file__).resolve().parents[1] / "fixtures" / "request.topic_pivot_enriched_stock.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    m = ShadowCompareRequest.model_validate(raw)
    assert m.incomingText == "¿Tienen mesa de exterior?"
    assert m.stockTable is not None and len(m.stockTable) == 3
    assert m.publicCatalogSlug == "demo-tienda"
    assert m.inventoryNarrowingNote is not None
    r = client.post("/shadow-compare", json=raw)
    assert r.status_code == 200
    data = r.json()
    assert data.get("candidateDecision")
    dr = (data["candidateDecision"] or {}).get("draftReply") or ""
    assert len(dr.strip()) > 0


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


def test_handoff_request_guard_sets_human_and_reply() -> None:
    from crew_shadow_crewai.draft_variant_guard import apply_followup_draft_guards
    from crew_shadow_crewai.models import (
        CandidateDecision,
        ShadowCompareRequest,
        ShadowCompareResponse,
    )

    pitch = "Te confirmo Mesa de algarrobo: precio $195.000 y 2 unidad(es) disponibles. ¿Querés que te reserve una?"
    body = ShadowCompareRequest(
        schemaVersion=1,
        kind="waseller.shadow_compare.v1",
        tenantId="00000000-0000-4000-8000-000000000001",
        leadId="00000000-0000-4000-8000-000000000002",
        incomingText="preferiría que me derive con el asesor para más información",
        interpretation={},
        baselineDecision={},
        stockTable=[{"name": "Mesa algarrobo", "stock": 2}],
    )
    resp = ShadowCompareResponse(
        candidateDecision=CandidateDecision(
            draftReply=pitch,
            nextAction="offer_reservation",
            recommendedAction="offer_reservation",
        )
    )
    out = apply_followup_draft_guards(body, resp)
    assert out.candidateDecision is not None
    assert out.candidateDecision.nextAction == "handoff_human"
    assert out.candidateDecision.recommendedAction == "handoff_human"
    dr = out.candidateDecision.draftReply or ""
    assert dr != pitch
    assert "asesor" in dr.lower()
    assert "catálogo" in dr.lower()
    assert "handoff_request_guard" in (out.candidateDecision.reason or "")


def test_negation_followup_guard_on_pushy_reservation_without_recent_dup() -> None:
    from crew_shadow_crewai.draft_variant_guard import apply_followup_draft_guards
    from crew_shadow_crewai.models import (
        CandidateDecision,
        ShadowCompareRequest,
        ShadowCompareResponse,
    )

    pitch = (
        "Te confirmo Mesa de algarrobo: precio $195.000 y 2 unidad(es) disponibles. "
        "¿Querés que te reserve una ahora?"
    )
    body = ShadowCompareRequest(
        schemaVersion=1,
        kind="waseller.shadow_compare.v1",
        tenantId="00000000-0000-4000-8000-000000000001",
        leadId="00000000-0000-4000-8000-000000000002",
        incomingText="no, gracias",
        interpretation={},
        baselineDecision={},
        stockTable=[{"name": "Mesa algarrobo", "stock": 2}],
    )
    resp = ShadowCompareResponse(
        candidateDecision=CandidateDecision(
            draftReply=pitch,
            nextAction="offer_reservation",
            recommendedAction="offer_reservation",
        )
    )
    out = apply_followup_draft_guards(body, resp)
    assert out.candidateDecision is not None
    dr = out.candidateDecision.draftReply or ""
    assert dr != pitch
    assert "negation_followup_guard" in (out.candidateDecision.reason or "")
    assert out.candidateDecision.nextAction == "reply_only"
    assert "catálogo" in dr.lower()


def test_negation_followup_guard_includes_public_catalog_url() -> None:
    from crew_shadow_crewai.draft_variant_guard import apply_followup_draft_guards
    from crew_shadow_crewai.models import (
        CandidateDecision,
        ShadowCompareRequest,
        ShadowCompareResponse,
    )

    pitch = "¿Querés que te reserve una? Tengo 2 en stock."
    body = ShadowCompareRequest(
        schemaVersion=1,
        kind="waseller.shadow_compare.v1",
        tenantId="00000000-0000-4000-8000-000000000001",
        leadId="00000000-0000-4000-8000-000000000002",
        incomingText="no gracias",
        interpretation={},
        baselineDecision={},
        stockTable=[{"name": "X", "stock": 2}],
        publicCatalogSlug="mi-catalogo",
        publicCatalogBaseUrl="https://app.tienda.com",
    )
    resp = ShadowCompareResponse(
        candidateDecision=CandidateDecision(draftReply=pitch, nextAction="offer_reservation")
    )
    out = apply_followup_draft_guards(body, resp)
    dr = (out.candidateDecision.draftReply or "") if out.candidateDecision else ""
    assert "https://app.tienda.com/tienda/mi-catalogo" in dr


def test_public_catalog_slug_invalid_coerced_to_none() -> None:
    from crew_shadow_crewai.models import ShadowCompareRequest

    body = ShadowCompareRequest(
        schemaVersion=1,
        kind="waseller.shadow_compare.v1",
        tenantId="00000000-0000-4000-8000-000000000001",
        leadId="00000000-0000-4000-8000-000000000002",
        incomingText="hola",
        interpretation={},
        baselineDecision={"draftReply": "x"},
        publicCatalogSlug="no/se/puede",
        publicCatalogBaseUrl="https://ok.com",
    )
    assert body.publicCatalogSlug is None
    assert body.publicCatalogBaseUrl == "https://ok.com"


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
