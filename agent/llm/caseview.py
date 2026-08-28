"""THE REDACTION BOUNDARY. One function, one place.

Everything `agent/llm` knows about a mandate comes through `build_case_view`.
If a field is not constructed here, no diagnoser -- deterministic or
model-backed -- can leak it, because it never had it.

WHAT DOES NOT CROSS. The expected balance, the raw payday posterior, the
customer's salary, the true payday, and every `p_success`. The governance rule
in docs/07_AGENT_BRIEF.md §2 says merchant-facing explanations must not
disclose the customer's financial state ("our model scores this window
highest", never "their balance has never recovered before the 3rd"). Enforcing
that by reviewing prose is a losing game; enforcing it by never handing the
prose-writer the number is not.

WHAT DOES CROSS, and why each is defensible to show a merchant:
  amount           -- the merchant's own subscription price. They set it.
  decline_history  -- their own transactions' response codes.
  attempts_used    -- their own retry count against a public regulatory cap.
  day_in_cycle     -- their own billing calendar.
  uncertainty_band -- a coarse label for OUR model's confidence, not the
                      customer's finances. "wide" says we are unsure when to
                      try; it does not say what is in the account.
  peer_mandate_success_recent -- a BOOLEAN, and the sharpest thing here. It
                      says another mandate on this account just succeeded. It
                      names no merchant and no amount. This is the moat made
                      visible to the narrative layer without disclosing a
                      balance, and it is the one field to re-examine if the
                      legal question in docs/01_FACTS.md ("may an aggregator
                      use Merchant A's outcomes for Merchant B") resolves the
                      wrong way. That question is tagged [GUESS] and unread.

  bank             -- the REMITTER BANK's UPI handle, e.g. "@oksbi". Added
                      29 Aug 2026. This is not customer financial state: in
                      real UPI the payer's VPA carries the bank on its face, so
                      a merchant already sees it on their own transaction
                      report. What it buys is the one thing `RailMonitor`
                      structurally cannot say -- the monitor pools technical
                      declines across every bank, so a single-bank incident is
                      locally obvious and statistically invisible. A diagnoser
                      that can see "every failure I have is @oksbi" has
                      information the binomial tail has averaged away.
                      ⚠️ It is also the field most likely to invite an
                      inference about a person, so `governance.py` treats a
                      bank NAME in merchant-facing prose as a disclosure and
                      the rationale may not carry one.

`merchant_note` IS UNTRUSTED INPUT. It is free text supplied by a merchant and
is carried here so the injection tests have something to attack. Nothing may
treat it as an instruction.
"""
from __future__ import annotations

import hashlib
import json
from typing import Sequence

from agent.ports import CaseView, PaydayUncertainty, Rupees


def _hash(payload: dict) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]


def build_case_view(*, amount: Rupees, attempts_used: int, attempts_cap: int,
                    day: int, cycle_open: int, cycle_close: int,
                    decline_history: Sequence[str],
                    peer_success_recent: bool,
                    uncertainty: PaydayUncertainty,
                    merchant_note: str = "", bank: str = "") -> CaseView:
    hist = tuple(decline_history[-6:])
    payload = {
        "attempts_used": attempts_used,
        "attempts_cap": attempts_cap,
        "day_in_cycle": day - cycle_open,
        "days_left_in_cycle": cycle_close - day,
        # amount is bucketed into the hash so near-identical cases share a
        # cached LLM response instead of paying for the same answer twice
        "amount_bucket": int(amount // 250),
        "decline_history": list(hist),
        "peer": bool(peer_success_recent),
        "band": uncertainty.band,
        # In the hash, so two cases that differ only by bank do NOT share a
        # cached LLM response. A bank-shaped outage is the case the cache would
        # otherwise collapse into its bank-agnostic twin.
        "bank": bank,
    }
    return CaseView(
        case_hash=_hash(payload),
        attempts_used=attempts_used,
        attempts_cap=attempts_cap,
        day_in_cycle=day - cycle_open,
        days_left_in_cycle=cycle_close - day,
        amount=amount,
        decline_history=hist,
        n_recent_z9=sum(1 for c in hist if c == "Z9"),
        peer_mandate_success_recent=bool(peer_success_recent),
        uncertainty_band=uncertainty.band,
        merchant_note=merchant_note,
        bank=bank,
    )
