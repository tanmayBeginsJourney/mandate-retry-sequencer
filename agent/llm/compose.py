"""Write the customer or merchant copy for a non-money workflow.

The diagnoser chooses WHAT to do. This module writes WHAT TO SAY. The two
are separate so a customer-facing sentence cannot land in a merchant field
by accident, and so a template still exists when the model is unavailable.
"""
from __future__ import annotations

from dataclasses import dataclass

from agent.llm.governance import sanitise
from agent.llm.prompts import (COMPOSE_PROMPT_ID, COMPOSE_SCHEMA,
                               render_compose)
from agent.ports import CaseView, Diagnosis, InterventionKind


@dataclass(frozen=True)
class Outreach:
    audience: str
    body: str
    source: str
    prompt_id: str = ""


def template_body(view: CaseView, diag: Diagnosis, purpose: str = "") -> str:
    amt = f"Rs {view.amount:.0f}"
    purpose = purpose or (
        "escalate" if diag.intervention is InterventionKind.ESCALATE
        else "reminder")
    if purpose == "backup_link":
        return (f"Pay {amt} for this billing period using this link. "
                "The automatic debit is paused, so you will not be charged "
                "twice. Paying keeps the subscription active.")
    if purpose == "escalate":
        return (f"Mandate referred to the merchant queue after "
                f"{diag.root_cause.value.replace('_', ' ').lower()}. "
                f"{amt}. {diag.rationale}".strip())[:280]
    return (f"A subscription payment of {amt} could not be collected. "
            "Please add funds so the next automatic debit can succeed.")


def compose_outreach(view: CaseView, diag: Diagnosis, *,
                     client=None, purpose: str = "") -> Outreach:
    """Never raises. Model failure falls back to the template."""
    purpose = purpose or (
        "escalate" if diag.intervention is InterventionKind.ESCALATE
        else "reminder")
    audience = "merchant" if purpose == "escalate" else "customer"
    templ = template_body(view, diag, purpose)
    if client is None:
        body, _ = sanitise(templ)
        return Outreach(audience, body, "template", "template-v1")

    system, user = render_compose(view, diag, purpose=purpose)
    r = client.complete(system=system, user=user,
                        prompt_id=COMPOSE_PROMPT_ID,
                        case_hash=f"{view.case_hash}|{purpose}",
                        schema=COMPOSE_SCHEMA)
    if not r.ok or not isinstance(r.parsed, dict):
        body, _ = sanitise(templ)
        return Outreach(audience, body, "template",
                        f"{COMPOSE_PROMPT_ID}+fallback")
    raw = str(r.parsed.get("body", "")).strip() or templ
    body, gov = sanitise(raw)
    src = "llm" if gov.ok and raw else "template"
    return Outreach(audience, body, src, COMPOSE_PROMPT_ID)
