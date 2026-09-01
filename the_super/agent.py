"""The Super — the root workflow.

START -> classify -> route
  - PAYMENT     -> payment_workflow
  - MAINTENANCE -> maintenance_workflow
  - OTHER       -> flag for a human (never guess)
"""

from google.adk import Agent, Event, Workflow
from google.adk.agents.context import Context

from .schemas import Classification, IncomingMessage, RoutedMessage
from .skills_registry import TENANT_SMS_SKILL
from .tools.gmail import send_sms_now
from .payment import payment_workflow
from .maintenance import maintenance_workflow


classifier = Agent(
    name="classifier",
    model="gemini-flash-latest",
    input_schema=IncomingMessage,
    instruction="""Classify this tenant message.

- PAYMENT — mentions a payment made, PayPal, Zelle, a transfer, or a rent amount
- MAINTENANCE — a repair, damage, a fault, something not working, a leak, no hot
  water, or a complaint about living conditions
- OTHER — everything else (questions, move-out notice, small talk, undecidable)

**Confidence rule:**
Use high only when the message is explicit and unambiguous. Anything vague, or
spanning both categories, or requiring you to guess, is low.

Give a one-sentence reason for the call.

Prefer misclassifying as OTHER over forcing an uncertain message into PAYMENT —
the cost of a wrong entry in the payment ledger far exceeds the cost of making
the landlord glance at one extra message.""",
    output_schema=Classification,
)


def intent_router(node_input: Classification, ctx: Context):
    """Anything low-confidence goes to a human and never enters the automatic flow.

    It also recovers the original message and passes it onward — the
    classification result does not carry the tenant's own words, and both payment
    extraction and maintenance triage need the original text. ctx.user_content is
    the workflow's initial input.
    """
    original = IncomingMessage.model_validate_json(ctx.user_content.parts[0].text)
    routed = RoutedMessage(message=original, classification=node_input)
    if node_input.confidence == "low":
        return Event(route="OTHER", output=routed)
    return Event(route=node_input.intent, output=routed)


holding_reply_agent = Agent(
    name="holding_reply",
    model="gemini-flash-latest",
    input_schema=RoutedMessage,
    tools=[TENANT_SMS_SKILL],
    instruction="""The system judged that it should not handle this message itself —
either the classification is uncertain, or it touches the lease, the deposit, or
legal matters that need the landlord's own judgment.

Write a **holding reply**: confirm receipt, say someone will follow up, and
**take no position at all**.

**First load the tenant-sms skill and read references/holding.md** — it splits
into several variants by situation (classification uncertain / lease or deposit /
legal or complaint / possible safety emergency). Pick the matching one.

⚠️ Never respond to the substance, however simple the question looks.
⚠️ Never promise a timeframe.
⚠️ For legal matters, fewer words is better.

Output only the body of the text message.""",
    output_schema=str,
)


def send_holding_reply(node_input: str, ctx: Context):
    """Send the holding reply and notify the landlord.

    Why this one may be sent automatically: it commits to nothing, which makes it
    the safest outbound message the system can send. Before it existed, the
    outcome for these messages was "notify the landlord, and the tenant hears
    nothing at all" — and that silence escalates the conflict by itself, which
    works against the landlord in a dispute.
    """
    original = IncomingMessage.model_validate_json(ctx.user_content.parts[0].text)
    send_sms_now(original.gmail_thread_id, node_input)
    return Event(
        message=(
            f"📥 A message needs your attention\n"
            f"From: {original.room_id}\n"
            f"Original: \u201c{original.body}\u201d\n"
            f"Auto-replied to the tenant: received, we'll follow up (no position taken)."
        )
    )


root_agent = Workflow(
    name="the_super",
    edges=[
        ("START", classifier, intent_router),
        (intent_router, {
            "PAYMENT": payment_workflow,
            "MAINTENANCE": maintenance_workflow,
            "OTHER": holding_reply_agent,
        }),
        (holding_reply_agent, send_holding_reply),
    ],
)
