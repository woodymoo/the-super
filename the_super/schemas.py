"""Type contracts for data passed between nodes.

The ADK 2.0 graph engine passes data via node return values, and these pydantic
models are the interface definition.
"""

from typing import Literal

from pydantic import BaseModel


class IncomingMessage(BaseModel):
    """One incoming message, already channel-parsed and resolved to a tenant."""
    source: Literal["sms", "email"]      # sms = forwarded via Voice, email = sent by the tenant directly
    gmail_thread_id: str
    gmail_message_id: str
    sender: str                          # phone number or email address
    # ⚠️ Optional fields must have defaults. ADK strips None when serializing a
    # node output, and re-validation then fails with 'Field required' — in pydantic
    # `X | None` without a default is still required.
    room_id: str | None = None           # None when no tenant matches
    tenant_email: str | None = None
    body: str
    received_at: str                     # ISO 8601
    has_attachments: bool = False


class Classification(BaseModel):
    """The classification result."""
    intent: Literal["PAYMENT", "MAINTENANCE", "OTHER"]
    confidence: Literal["high", "low"]
    reason: str


class RoutedMessage(BaseModel):
    """The classification result plus the original message.

    The classifier node outputs only intent/confidence/reason and not the
    tenant's own words — but the downstream payment extraction and maintenance
    triage both need the original text. ADK 2.0 passes data by return value, so
    the two have to travel onward together.

    ⚠️ This object is assembled in code by intent_router (the original text is
       recovered from ctx.user_content) rather than having the model restate it —
       a model restating it quietly alters key fields like room_id.
    """
    message: IncomingMessage
    classification: Classification


class Tenant(BaseModel):
    room_id: str
    name: str
    phone: str
    email: str
    rent_amount: float
