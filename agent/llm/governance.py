"""Merchant-facing text checks.

THE RULE (docs/07_AGENT_BRIEF.md §2): merchant-facing explanations must not
disclose the customer's financial state. Say "our model scores this window
highest", never "their balance has never recovered before the 3rd".

WHAT THIS FILE IS AND IS NOT. It is a lexical net. It is NOT the guarantee.
The guarantee is `caseview.py`: the narrative layer is never handed a balance,
a salary, a payday or a `p_success`, so it cannot disclose one -- and a lexical
check that could be defeated by a synonym is not what the architecture rests
on. This file is defence in depth, and it catches the case the redaction
boundary cannot: a model that INVENTS a financial claim it was never given.
An invented balance is still a disclosure to the merchant reading it, and it
is worse than a true one because it is also wrong.

The time check exists for a different reason. `ports.Diagnosis` has no
temporal field, so an injected "retry at 11am" has nowhere structural to land
-- but it can still land in `rationale`, which is prose that reaches a human.
A justification that recommends a debit time is an LLM on the timing path via
the merchant's eyeballs, and ADR-005 does not have an exception for that.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# Possessive / inferential framings of customer financial state. Decline-code
# names are deliberately absent: "the bank returned Z9" is the merchant's own
# transaction result, not a disclosure about the account.
_FINANCIAL_STATE = [
    r"\btheir balance\b", r"\bthe balance\b", r"\bcustomer'?s? balance\b",
    r"\baccount balance\b", r"\blow balance\b", r"\bbalance (?:is|was|has|of)\b",
    r"\bsalary\b", r"\bpayday\b", r"\bpay ?che(?:ck|que)\b", r"\bwages?\b",
    r"\btheir income\b", r"\bgets paid\b", r"\bis paid on\b", r"\bpaid on the\b",
    r"\baccount is empty\b", r"\bno money\b", r"\bout of money\b",
    r"\bcannot afford\b", r"\bcan'?t afford\b", r"\bbroke\b",
    r"\bhas only\b", r"\bonly ₹\b", r"\bfunds (?:remaining|available)\b",
]

# The remitter bank. It is IN the CaseView on purpose -- a diagnoser that can
# see "every failure I have is @oksbi" beats a binomial tail that pooled the
# banks together -- but naming it in merchant-facing prose is a different act.
# "Your customer banks with SBI and SBI is having a bad morning" is an
# inference about a person delivered to a third party, and the merchant did not
# need the bank to be told the window is bad. Added 29 Aug 2026 with the field.
_BANK_NAMES = [
    r"@ok\w+", r"@ybl\b", r"@paytm\b", r"@ibl\b", r"@upi\b",
    r"\bsbi\b", r"\bhdfc\b", r"\bicici\b", r"\baxis bank\b",
    r"\bkotak\b", r"\bpaytm\b", r"\bphonepe\b",
    r"\btheir bank\b", r"\bcustomer'?s? bank\b", r"\bthis bank\b",
]

# Anything that recommends WHEN. See the module docstring.
_TIME_EXPRESSIONS = [
    r"\b\d{1,2}\s*(?:am|pm)\b", r"\b\d{1,2}:\d{2}\b",
    r"\bat \d{1,2}\b", r"\bhour \d{1,2}\b",
    r"\bon (?:the )?\d{1,2}(?:st|nd|rd|th)\b",
    r"\btomorrow\b", r"\btonight\b", r"\bimmediately\b", r"\bright now\b",
    r"\bmonday\b", r"\btuesday\b", r"\bwednesday\b", r"\bthursday\b",
    r"\bfriday\b", r"\bsaturday\b", r"\bsunday\b",
]

# Prompt-injection tells. Present so the injection tests have a named check to
# assert against rather than a vibe.
_INJECTION = [
    r"\bignore (?:all )?(?:previous|prior|above)\b",
    r"\bdisregard (?:the )?(?:previous|prior|above|instructions)\b",
    r"\byou are now\b", r"\bnew instructions?\b",
    r"\bsystem prompt\b", r"\boverride\b",
]


@dataclass(frozen=True)
class GovernanceResult:
    ok: bool
    financial_state: tuple[str, ...] = ()
    time_expressions: tuple[str, ...] = ()
    injection_echo: tuple[str, ...] = ()
    bank_disclosure: tuple[str, ...] = ()

    @property
    def reasons(self) -> tuple[str, ...]:
        out = []
        for hit in self.financial_state:
            out.append(f"discloses customer financial state: {hit!r}")
        for hit in self.time_expressions:
            out.append(f"recommends a debit time: {hit!r}")
        for hit in self.injection_echo:
            out.append(f"echoes injected instruction text: {hit!r}")
        for hit in self.bank_disclosure:
            out.append(f"names the customer's bank to the merchant: {hit!r}")
        return tuple(out)


def _hits(patterns, text: str) -> tuple[str, ...]:
    low = text.lower()
    return tuple(m.group(0) for p in patterns
                 for m in re.finditer(p, low, re.IGNORECASE))


def check(text: str) -> GovernanceResult:
    fs = _hits(_FINANCIAL_STATE, text)
    te = _hits(_TIME_EXPRESSIONS, text)
    inj = _hits(_INJECTION, text)
    bk = _hits(_BANK_NAMES, text)
    return GovernanceResult(ok=not (fs or te or inj or bk),
                            financial_state=fs, time_expressions=te,
                            injection_echo=inj, bank_disclosure=bk)


SAFE_FALLBACK = ("Our timing model scores the scheduled window highest for "
                 "this mandate. Details withheld under the customer data "
                 "policy.")


def sanitise(text: str) -> tuple[str, GovernanceResult]:
    """Return text safe to show a merchant, plus what was wrong with it.

    A failing justification is REPLACED, not edited. Editing prose to remove a
    disclosure leaves the disclosure's shape behind and invites a reviewer to
    argue about whether the redaction was thorough. Replacing it does not."""
    res = check(text)
    return (text if res.ok else SAFE_FALLBACK), res
