"""Literales que Waseller valida en shadow-compare (ver README del repo)."""

from typing import Literal

# ConversationNextActionV1 (Waseller)
NEXT_ACTIONS: frozenset[str] = frozenset(
    {
        "reply_only",
        "ask_clarification",
        "confirm_variant",
        "offer_reservation",
        "reserve_stock",
        "share_payment_link",
        "suggest_alternative",
        "handoff_human",
        "close_lead",
        "manual_review",
    }
)

NextAction = Literal[
    "reply_only",
    "ask_clarification",
    "confirm_variant",
    "offer_reservation",
    "reserve_stock",
    "share_payment_link",
    "suggest_alternative",
    "handoff_human",
    "close_lead",
    "manual_review",
]

NEXT_ACTION_ENUM_DOC = ", ".join(sorted(NEXT_ACTIONS))

# Interpretación: source (Waseller — si se envía, solo estos)
INTERPRETATION_SOURCES: frozenset[str] = frozenset({"rules", "openai"})

InterpretationSource = Literal["rules", "openai"]

INTERPRETATION_SOURCE_ENUM_DOC = ", ".join(sorted(INTERPRETATION_SOURCES))

# ConversationStageV1 (Waseller)
CONVERSATION_STAGES: frozenset[str] = frozenset(
    {
        "waiting_product",
        "waiting_variant",
        "variant_offered",
        "waiting_reservation_confirmation",
        "reserved_waiting_payment_method",
        "payment_link_sent",
        "waiting_payment_confirmation",
        "sale_confirmed",
    }
)

ConversationStage = Literal[
    "waiting_product",
    "waiting_variant",
    "variant_offered",
    "waiting_reservation_confirmation",
    "reserved_waiting_payment_method",
    "payment_link_sent",
    "waiting_payment_confirmation",
    "sale_confirmed",
]

CONVERSATION_STAGE_ENUM_DOC = ", ".join(sorted(CONVERSATION_STAGES))
