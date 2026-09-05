"""Merchant-facing text checks.

THE RULE (docs/architecture.md): merchant-facing explanations must not
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
    # ADDED 29 AUG 2026, AND FOUND BY THE JUDGE, NOT BY US. GLM-5.3 flagged two
    # rationales this net had passed: "recent activity on the account indicates
    # MONEY REACHED IT recently" and "a recent successful mandate on this
    # account confirms FUNDS REACH IT". Both are paraphrases of
    # `peer_mandate_success_recent`, which the CaseView is allowed to carry --
    # but restating a boolean about another transaction as a claim that this
    # customer HAS MONEY is exactly the disclosure the rule forbids. The
    # independent checker was right and the fix goes here, never into the
    # checker.
    r"\bmoney (?:reach|reaches|reached|arriv|arrives|arrived)\w*\b",
    r"\bfunds (?:reach|reaches|reached|arriv|arrives|arrived)\w*\b",
    r"\bcash (?:reach|reaches|reached)\w*\b",
    r"\bis funded\b", r"\bwas funded\b", r"\bhas funds\b",
    r"\bgood for it\b", r"\bcan pay\b",
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


# NAMES THAT HAPPEN TO CARRY A DIGIT. Two kinds, and neither is a quantity:
#
#   * DECLINE CODES. "The last decline was Z9" names the provider's own
#     response code, which the operator is already looking at.
#   * "STAGE 0". The constraint layer's name. Found by the template's own
#     gate on the first run: every explanation that says what Stage 0 did was
#     being rejected for saying "0", including the deterministic template that
#     is the REPLACEMENT for a rejected one. A checker that fails its own
#     fallback is a checker that gets deleted.
#
# The exemption is a fixed list of names, not a pattern for "numbers in
# context", because the second is unbounded and the first is auditable.
_EXEMPT_TOKEN = re.compile(
    r"\b(?:Z8|Z9|ZX|YE|VD|VI|VF|IE|U30|OK|TECH|Stage\s+0)\b", re.IGNORECASE)
_NUMERIC_TOKEN = re.compile(r"\S*\d\S*")


def no_invented_numbers(text: str) -> tuple[str, ...]:
    """Numeric tokens in `text` that are not decline codes.

    THIS IS FOR OPERATOR EXPLANATIONS AND IS NOT PART OF `check`. The two have
    different audiences and different contracts: a merchant-facing reminder
    says "a payment of Rs 550 could not be collected" and must, so folding a
    numeric rule into `check` would fail `compose.py`'s own template.

    WHY AN EXPLANATION MAY NOT WRITE A NUMBER. Every figure an operator needs
    is already on the screen beside the prose, rendered from the decision
    itself -- the hour, the attempt count, the cap, the amount, the five gate
    verdicts. So a number in the narrative is never the only copy of a fact;
    it is a SECOND copy, written by a model, that can disagree with the first.
    An operator reading "the debit is set for hour 300" beside a field saying
    288 has been handed a decision to make that nobody meant to give them.

    The prompt states the rule, which is what makes this fire rarely. This is
    the backstop, in the shape the rest of this file uses: structure first, a
    lexical net behind it.
    """
    return tuple(m.group(0) for m in _NUMERIC_TOKEN.finditer(
        _EXEMPT_TOKEN.sub("", text)))


@dataclass(frozen=True)
class GovernanceResult:
    ok: bool
    financial_state: tuple[str, ...] = ()
    time_expressions: tuple[str, ...] = ()
    injection_echo: tuple[str, ...] = ()
    bank_disclosure: tuple[str, ...] = ()
    invented_numbers: tuple[str, ...] = ()

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
        for hit in self.invented_numbers:
            out.append(f"states a figure the interface already shows: {hit!r}")
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


def check_explanation(text: str) -> GovernanceResult:
    """`check`, plus the numeric rule. THE CONTRACT FOR OPERATOR PROSE.

    `check` is reused verbatim rather than forked. Three of its four nets mean
    the same thing to an operator as to a merchant -- a model that INVENTS a
    balance has disclosed one to whoever reads it, an echoed injection is an
    echoed injection, and a bank name is still an inference about a person.

    THE TIME NET IS KEPT TOO, AND THAT IS NOT A CONTRADICTION OF THE DESIGN.
    `ExplainView` carries the schedule so the explainer can DESCRIBE the window
    the scheduler chose. It does not follow that the prose may name an hour:
    the hour is on the screen already, and a sentence like "retry at 11:00" is
    a RECOMMENDATION whatever the layer that wrote it intended. Describing is
    "the debit sits outside the peak window, a full day after the notice";
    recommending is a clock face. The net separates them, and the numeric rule
    catches the digits the word-shaped patterns miss.
    """
    base = check(text)
    nums = no_invented_numbers(text)
    return GovernanceResult(
        ok=base.ok and not nums,
        financial_state=base.financial_state,
        time_expressions=base.time_expressions,
        injection_echo=base.injection_echo,
        bank_disclosure=base.bank_disclosure,
        invented_numbers=nums)
