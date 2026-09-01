"""Maintenance branch (ADK 2.0 graph workflow).

Flow: triage severity -> judge whether the description is clear -> route
  - Unclear -> draft the photo request text (may be auto-sent; zero risk)
  - Clear   -> load history -> draft the dispatch brief for the landlord (requires human approval)
"""

from typing import Literal

from pydantic import BaseModel
from google.adk import Agent, Event, Workflow
from google.adk.agents.context import Context

from .skills_registry import TENANT_SMS_SKILL

from .schemas import IncomingMessage, RoutedMessage
from .tools.gmail import send_sms_now
from .tools.store import get_ticket_history, write_ticket


class Triage(BaseModel):
    """The initial assessment of a maintenance request."""
    room_id: str
    description: str                                  # the tenant's own words
    severity: Literal["urgent", "normal", "low"]
    clarity: Literal["clear", "needs_media"]
    missing_info: str                                 # what information is missing (empty when clarity=clear)
    suggested_photos: str                             # what to photograph (empty when clarity=clear)


class TicketDraft(BaseModel):
    ticket_id: str
    room_id: str
    severity: str
    summary: str
    # Must have a default — in pydantic `str | None` without one is still
    # required, so if the model omits the field you get a ValidationError and the
    # whole branch crashes.
    repeat_of: str | None = None                      # the id of the matching prior ticket, if any
    contractor_brief: str


# ---------------------------------------------------------------- AI nodes

triage_agent = Agent(
    name="maintenance_triage",
    model="gemini-flash-latest",
    input_schema=RoutedMessage,
    instruction="""Triage the severity of a tenant's maintenance request and judge
whether the description is clear enough.

The tenant's own words are in message.body and the room is in message.room_id —
copy room_id verbatim; do not infer it.

**Severity rules:**
- urgent — anything involving water (a leak or a burst pipe), electricity, gas,
  loss of heat, or any safety hazard
- normal — affects normal use but isn't urgent (an appliance fault, a damaged
  door or window)
- low — doesn't affect use (an odd noise, a cosmetic issue)

**Whether the description is clear:**
Clear = it states **which fixture or location** + **what is happening** +
**roughly when it started**. Missing any one of the three makes it needs_media.

Examples:
- "the pipe under the kitchen sink is dripping, started last night" -> clear
- "the kitchen is broken" -> needs_media; the symptom and location are missing
- "the AC isn't cooling" -> needs_media; the start time and specific behavior are missing

**When needs_media:**
- Write in missing_info exactly what is missing
- Make suggested_photos specific about what to photograph; never just "please
  send photos". For example "a close-up of the pipe joint under the sink, plus a
  short video with the faucet running"

When the description is clear, leave missing_info and suggested_photos as empty
strings.""",
    output_schema=Triage,
)


request_media_agent = Agent(
    name="request_media",
    model="gemini-flash-latest",
    input_schema=Triage,
    tools=[TENANT_SMS_SKILL],
    instruction="""Draft a text asking the tenant for photos or video.

**First load the tenant-sms skill and read references/maintenance.md** — it holds
the table of which spots to ask about per fixture type, plus how to handle an
emergency. The wording standard there governs.

The suggested_photos in the input is a preliminary suggestion; the table in the
skill is more specific and takes precedence.

Output only the body of the text message, with no surrounding commentary.""",
    output_schema=str,
)


brief_agent = Agent(
    name="contractor_brief",
    model="gemini-flash-latest",
    instruction="""Produce the contractor's job brief from the maintenance request
and that room's prior tickets.

Include: the room, the problem description, the urgency, and whether this is a
repeat problem (if the history holds something similar, say plainly when it last
happened and how it was handled).

**Write in English** — the brief is forwarded to the contractor.

The brief is what the landlord reads and then forwards to the contractor, so do
not write it in the voice used with tenants.""",
    output_schema=TicketDraft,
)


# ------------------------------------------------------------ Pure-code nodes

def clarity_router(node_input: Triage):
    """Unclear -> ask for photos first; clear -> go straight to dispatch preparation."""
    if node_input.clarity == "needs_media":
        return Event(route="REQUEST_MEDIA", output=node_input)
    return Event(route="PREPARE_DISPATCH", output=node_input)


def load_history(node_input: Triage) -> dict:
    """Load this room's prior tickets and hand them to brief_agent with the current request."""
    history = get_ticket_history(node_input.room_id)
    return {
        "current": node_input.model_dump(),
        "history": history,
    }


def open_awaiting_media_ticket(node_input: str, ctx: Context):
    """**Actually send** the photo request text and open an awaiting_media ticket.

    Asking for photos is the one category CLAUDE.md constraint 1 allows to be
    auto-sent to a tenant — it is zero risk, and the worst case is asking again.

    The original message is recovered from ctx.user_content: this node's
    node_input is only the message text, but sending needs gmail_thread_id and
    opening the ticket needs room_id.
    """
    original = IncomingMessage.model_validate_json(ctx.user_content.parts[0].text)
    sent_id = send_sms_now(original.gmail_thread_id, node_input)
    ticket_id = write_ticket(
        status="awaiting_media",
        room_id=original.room_id,          # previously omitted, which left tickets with room_id=None
        draft_sms=node_input,
        sent_message_id=sent_id,
    )
    return Event(
        message=(f"🔧 {original.room_id} ticket {ticket_id} opened (awaiting photos)\n"
                 f"The photo request text was sent automatically.")
    )


def stage_for_approval(node_input: TicketDraft):
    """The dispatch brief is ready — it waits for the landlord's approval and is never auto-sent to the contractor."""
    write_ticket(status="ready_to_dispatch", draft=node_input.model_dump())
    return Event(
        message=(
            f"🔧 {node_input.room_id} ticket {node_input.ticket_id} awaiting dispatch\n"
            f"Severity: {node_input.severity}\n"
            f"{node_input.summary}\n"
            + (f"\u26a0\ufe0f Possible repeat problem (see {node_input.repeat_of})\n"
               if node_input.repeat_of else "")
            + "The brief is ready; forward it to the contractor once you confirm."
        )
    )


# ---------------------------------------------------------------- The graph

maintenance_workflow = Workflow(
    name="maintenance_workflow",
    edges=[
        ("START", triage_agent, clarity_router),
        # ⚠️ A route dict value must be a **single** node. A tuple is read by ADK
        # as a parallel fan-out
        # (_graph.py: `{"route_x": (node_a, node_b)}  # fan-out: both triggered`),
        # not a sequential chain — both nodes fire independently and neither sees
        # the other's output. Chains go in their own edge item.
        (clarity_router, {
            "REQUEST_MEDIA": request_media_agent,
            "PREPARE_DISPATCH": load_history,
        }),
        (request_media_agent, open_awaiting_media_ticket),
        (load_history, brief_agent, stage_for_approval),
    ],
)
