import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from crew_shadow_crewai.constants import (
    CONVERSATION_STAGES,
    INTERPRETATION_SOURCES,
    ConversationStage,
    InterpretationSource,
    NEXT_ACTIONS,
    NextAction,
)
from crew_shadow_crewai.text_encoding import repair_utf8_mojibake

MessageDirection = Literal["incoming", "outgoing"]


class RecentMessageItem(BaseModel):
    """Ítem de recentMessages (contrato HTTP v1.1)."""

    model_config = ConfigDict(extra="forbid")

    direction: MessageDirection
    message: str


class ShadowCompareRequest(BaseModel):
    """Request Waseller → shadow-compare. v1 núcleo + v1.1 opcional (mismos schemaVersion/kind)."""

    model_config = ConfigDict(extra="ignore")

    schemaVersion: int = Field(..., ge=1, le=1)
    kind: str
    tenantId: str
    leadId: str
    incomingText: str
    interpretation: dict
    baselineDecision: dict
    # --- v1.1 (opcional) ---
    phone: str | None = None
    correlationId: str | None = None
    messageId: str | None = None
    conversationId: str | None = None
    recentMessages: list[RecentMessageItem] | None = Field(
        default=None,
        description="Ventana corta; tope 8 ítems (se trunca si viniera más).",
    )
    businessProfileSlug: str | None = Field(
        default=None,
        description="Slug de rubro/perfil; prompts extra opcionales vía CREW_TENANT_PROMPTS_DIR.",
    )
    stockTable: list[dict[str, Any]] | None = Field(
        default=None,
        description="Filas de inventario (mismas columnas que envía Waseller); no inventar fuera de esto.",
    )
    inventoryNarrowingNote: str | None = Field(
        default=None,
        description="Nota opcional de Waseller sobre cómo se acotó el inventario para este request.",
    )
    tenantCommercialContext: str | None = Field(
        default=None,
        max_length=6000,
        description=(
            "Texto libre del tenant (políticas, tono, horarios, formas de pago, envíos, límites). "
            "Se inyecta en el prompt del redactor; no reemplaza stockTable."
        ),
    )
    tenantBrief: str | None = Field(
        default=None,
        max_length=2500,
        description="Resumen corto del negocio o del lead para el prompt (Waseller; opcional).",
    )
    etapa: str | None = Field(
        default=None,
        max_length=500,
        description="Etapa o fase del embudo del lead (Waseller; opcional).",
    )
    activeOffer: dict[str, Any] | None = Field(
        default=None,
        description="Última oferta o deal activo (Waseller; dict flexible, p. ej. producto, precio, CTA).",
    )
    memoryFacts: list[str] | None = Field(
        default=None,
        description="Hechos recordados sobre el lead (Waseller; lista corta de strings).",
    )

    @field_validator("recentMessages", mode="before")
    @classmethod
    def trim_recent_messages(cls, v: object) -> object:
        if v is None:
            return None
        if isinstance(v, list) and len(v) > 8:
            return v[:8]
        return v

    @field_validator("stockTable", mode="before")
    @classmethod
    def cap_stock_rows(cls, v: object) -> object:
        if v is None:
            return None
        if isinstance(v, list) and len(v) > 500:
            return v[:500]
        return v

    @field_validator("tenantCommercialContext", mode="after")
    @classmethod
    def strip_tenant_commercial_context(cls, v: str | None) -> str | None:
        if v is None:
            return None
        s = str(v).strip()
        return s or None

    @field_validator("tenantBrief", mode="after")
    @classmethod
    def strip_tenant_brief(cls, v: str | None) -> str | None:
        if v is None:
            return None
        s = str(v).strip()
        return s or None

    @field_validator("etapa", mode="after")
    @classmethod
    def strip_etapa(cls, v: str | None) -> str | None:
        if v is None:
            return None
        s = str(v).strip()
        return s or None

    @field_validator("memoryFacts", mode="before")
    @classmethod
    def cap_memory_facts(cls, v: object) -> object:
        if v is None:
            return None
        if not isinstance(v, list):
            return v
        out: list[str] = []
        for item in v[:40]:
            if item is None:
                continue
            s = str(item).strip()
            if not s:
                continue
            out.append(s[:400])
        return out or None

    @field_validator("businessProfileSlug", mode="after")
    @classmethod
    def normalize_business_slug(cls, v: str | None) -> str | None:
        if v is None:
            return None
        s = str(v).strip()
        if not s:
            return None
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}", s):
            raise ValueError("businessProfileSlug inválido (solo letras, números, . _ -)")
        return s


class CandidateDecision(BaseModel):
    draftReply: str | None = None
    intent: str | None = None
    nextAction: NextAction | None = None
    recommendedAction: NextAction | None = None
    confidence: float | None = None
    reason: str | None = None

    @field_validator("nextAction", "recommendedAction", mode="before")
    @classmethod
    def coerce_next_action(cls, v: object) -> str | None:
        """Acepta solo literales Waseller; cualquier otro valor se normaliza a None."""
        if v is None:
            return None
        s = str(v).strip()
        if not s:
            return None
        return s if s in NEXT_ACTIONS else None

    @field_validator("draftReply", "reason", "intent", mode="after")
    @classmethod
    def _repair_mojibake(cls, v: str | None) -> str | None:
        """Tras el resto de validaciones, corrige mojibake típico del LLM (Â¿, QuerÃ©s, etc.)."""
        return repair_utf8_mojibake(v)


class CandidateInterpretation(BaseModel):
    """
    Subconjunto alineado con la validación parcial de Waseller sobre candidateInterpretation.
    Campos extra del LLM se ignoran; enums inválidos se anulan.
    """

    model_config = ConfigDict(extra="ignore")

    intent: str | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    nextAction: NextAction | None = None
    source: InterpretationSource | None = None
    conversationStage: ConversationStage | None = None

    @field_validator("nextAction", mode="before")
    @classmethod
    def coerce_next_action(cls, v: object) -> str | None:
        if v is None:
            return None
        s = str(v).strip()
        if not s:
            return None
        return s if s in NEXT_ACTIONS else None

    @field_validator("source", mode="before")
    @classmethod
    def coerce_source(cls, v: object) -> str | None:
        if v is None:
            return None
        s = str(v).strip().lower()
        if not s:
            return None
        return s if s in INTERPRETATION_SOURCES else None

    @field_validator("conversationStage", mode="before")
    @classmethod
    def coerce_conversation_stage(cls, v: object) -> str | None:
        if v is None:
            return None
        s = str(v).strip()
        if not s:
            return None
        return s if s in CONVERSATION_STAGES else None

    @field_validator("intent", mode="after")
    @classmethod
    def _repair_mojibake_intent(cls, v: str | None) -> str | None:
        return repair_utf8_mojibake(v)


class ShadowCompareResponse(BaseModel):
    candidateDecision: CandidateDecision | None = None
    candidateInterpretation: CandidateInterpretation | None = None
