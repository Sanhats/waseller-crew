from typing import Literal

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

    @field_validator("recentMessages", mode="before")
    @classmethod
    def trim_recent_messages(cls, v: object) -> object:
        if v is None:
            return None
        if isinstance(v, list) and len(v) > 8:
            return v[:8]
        return v


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
