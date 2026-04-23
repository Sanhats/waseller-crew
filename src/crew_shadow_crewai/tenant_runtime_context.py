"""
tenantRuntimeContext v1 — estado operativo del tenant (Waseller → shadow-compare).

Contrato canónico en el monorepo Waseller: docs/integrations/waseller-crew/CONTRATO_V1_1.md
Carga en workers: loadCrewTenantRuntimeContextForCrewPayload / assembleShadowCompareOutboundBody.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class TenantRuntimePaymentChannelV1(BaseModel):
    """Canal de pago conectado (sin secretos)."""

    model_config = ConfigDict(extra="ignore")

    provider: str
    status: str


class TenantRuntimeIdentityV1(BaseModel):
    model_config = ConfigDict(extra="ignore")

    tenantId: str
    displayName: str
    plan: str


class TenantRuntimeKnowledgeV1(BaseModel):
    model_config = ConfigDict(extra="ignore")

    businessCategory: str
    businessLabels: list[str] = Field(default_factory=list)
    profileUpdatedAt: str | None = None

    @field_validator("businessLabels", mode="after")
    @classmethod
    def cap_business_labels(cls, v: list[str]) -> list[str]:
        out = [str(x).strip() for x in v if str(x).strip()]
        return out[:24]


class TenantRuntimeLlmV1(BaseModel):
    model_config = ConfigDict(extra="ignore")

    assistEnabled: bool
    confidenceThreshold: float
    guardrailsStrict: bool
    rolloutPercent: int
    modelName: str

    @field_validator("confidenceThreshold", mode="after")
    @classmethod
    def clamp_confidence_threshold(cls, v: float) -> float:
        return min(1.0, max(0.0, float(v)))


class TenantRuntimeOutboundMessagingV1(BaseModel):
    model_config = ConfigDict(extra="ignore")

    senderRateMs: int
    senderPauseEvery: int
    senderPauseMs: int


class TenantRuntimeCatalogV1(BaseModel):
    model_config = ConfigDict(extra="ignore")

    publicSlug: str | None = None
    publicBaseUrl: str | None = None

    @field_validator("publicSlug", mode="after")
    @classmethod
    def normalize_public_slug(cls, v: str | None) -> str | None:
        if v is None:
            return None
        s = str(v).strip()
        if not s:
            return None
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}", s):
            return None
        return s

    @field_validator("publicBaseUrl", mode="after")
    @classmethod
    def normalize_public_base_url(cls, v: str | None) -> str | None:
        if v is None:
            return None
        s = str(v).strip().rstrip("/")
        if not s:
            return None
        low = s.lower()
        if not (low.startswith("https://") or low.startswith("http://")):
            return None
        if s.count("://") != 1:
            return None
        if any(c in s for c in ("\n", "\r", "<", ">", '"', "'", " ", "\t")):
            return None
        return s[:512]


class TenantRuntimeTimestampsV1(BaseModel):
    model_config = ConfigDict(extra="ignore")

    tenantCreatedAt: str
    tenantUpdatedAt: str


class TenantRuntimeChannelV1(BaseModel):
    """
    Opcional. `whatsAppBusinessNumber` es el número **del negocio** (canal), no el del cliente.
    Solo si Waseller activa LLM_SHADOW_COMPARE_INCLUDE_TENANT_WHATSAPP_NUMBER (u equivalente).
    """

    model_config = ConfigDict(extra="ignore")

    whatsAppBusinessNumber: str | None = None


class TenantRuntimeContextV1(BaseModel):
    """
    Estado operativo del tenant (JSON camelCase tal cual Waseller).

    No reemplaza stockTable ni acciones sensibles; es contexto para prompts y tono.
    """

    model_config = ConfigDict(extra="ignore")

    version: Literal[1]
    identity: TenantRuntimeIdentityV1
    knowledge: TenantRuntimeKnowledgeV1
    llm: TenantRuntimeLlmV1
    outboundMessaging: TenantRuntimeOutboundMessagingV1
    catalog: TenantRuntimeCatalogV1
    paymentChannels: list[TenantRuntimePaymentChannelV1] = Field(default_factory=list)
    timestamps: TenantRuntimeTimestampsV1
    channel: TenantRuntimeChannelV1 | None = None
