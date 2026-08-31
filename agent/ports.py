"""The shared vocabulary. Every layer speaks these types; none of them owns one.

THIS MODULE IMPORTS NOTHING FROM `agent`. That is deliberate and is checked by
`agent/tests/test_layer_isolation.py`. If ports.py could import a layer, the
layers could reach each other through it, and the whole isolation argument
would be decoration.

Two things in here are load-bearing for the architecture, not conveniences:

1. `Diagnosis` HAS NO TEMPORAL FIELD. No day, no hour, no target time, no
   delay. The LLM layer's only output type physically cannot express when to
   debit somebody. That is ADR-005 -- "an LLM must never be on the path that
   decides whether to debit a specific customer at a specific moment" --
   enforced by construction rather than by reviewer discipline. A prompt
   injection that says "retry at 11am" has nowhere to put the 11am.

2. Money is carried two ways on purpose. `Rupees` (float) is what the frozen
   belief filter is fed, so our probabilities stay bit-identical to sim/.
   `Paise` (int) is what the audit log stores, so sums are exact and SQL can
   add them up without float drift. Convert at the boundary, never sum a
   Rupees for a report.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Literal, Protocol, Sequence

Rupees = float          # fed to w3.BeliefPD. Never summed for a report.
Paise = int             # stored in the audit log. Exact.


def to_paise(r: Rupees) -> Paise:
    """Round-half-up to the paisa. One conversion point, so drift has one home."""
    return int(round(r * 100))


# --------------------------------------------------------------- decline codes
# These mirror w3.OK / w3.Z9 / w3.TECH. They are re-declared as plain strings
# rather than imported so that `agent.ports` stays dependency-free; the values
# are asserted equal to w3's in test_layer_isolation.py.
OK = "OK"
Z9 = "Z9"           # insufficient funds. Needs a FRESH notification to retry.
TECH = "TECH"       # technical decline. May auto-represent under the old one.


# ------------------------------------------------------- the richer taxonomy
# Added 29 August 2026. `sim/w3.py` is frozen and its vocabulary is the three
# symbols above, which is all the belief filter needs -- it reasons about WHEN
# money is there and nothing else. It is not enough to name the families NPCI
# publishes, and that gap is precisely what a narrative layer is for:
#
#   a frozen account   -> STOP FOREVER. No retry ever helps.
#   a broken mandate   -> no retry helps either; the merchant must re-authorise.
#   a limit hit        -> the money IS there. A SMALLER debit works.
#   insufficient funds -> wait for money. This is the only one w3 models.
#   a technical decline-> the rail glitched. Try again; it costs an attempt.
#
# `w3.index_score` has no slot for "this account will never succeed again", so
# a frozen account looks to it like a very unlucky customer and it will spend
# attempts until the cap kills the mandate. A structural blind spot, not an
# unlearned parameter -- the same shape as the rail-outage argument.
#
# THE MEMBER CODES ARE [VERIFIED] against NPCI "UPI Error and Response Codes"
# v2.9 section 3.1, read via `agent/eval/golden_cases.yaml`'s research block.
# HOW OFTEN each family occurs is [GUESS]: no source found gives AutoPay
# -specific decline frequencies. The mix is SWEPT, never picked. See
# `DeclineMix` in `agent/execution/sim_executor.py` -- there is no
# `declines.py`; it was planned under that name and landed in the executor.
#
# This table lives in ports.py because `agent/llm` must be able to read it and
# rule I2 forbids `agent/llm` importing `agent.execution`. ports.py imports
# nothing, so it is the only lawful home for shared vocabulary.
FAMILY_OK = "OK"
FAMILY_FUNDS = "FUNDS"                    # Z9
FAMILY_TECH = "TECH"                      # TECH
FAMILY_ACCOUNT_SHUT = "ACCOUNT_SHUT"      # ZX, YE
FAMILY_MANDATE_BROKEN = "MANDATE_BROKEN"  # VD, VI, VF
FAMILY_LIMIT = "LIMIT"                    # Z8, IE
FAMILY_AMBIGUOUS = "AMBIGUOUS"            # U30 -- names nothing

# --- TWO FAMILIES ADDED 29 AUGUST 2026, FROM RAZORPAY'S OWN ERROR VOCABULARY.
# Both come from `payments_error_reasons.xlsx`, downloaded from razorpay.com --
# 114 rows, 110 distinct reasons, [VERIFIED] primary source, listed in
# docs/01_FACTS.md. Neither was invented to fill a gap we imagined; both were
# found by mapping their list onto ours and finding cells with nowhere to go.
#
# NEITHER IS SIMULATED AND NEITHER HAS A RATE. `sim/w3.py` is frozen and models
# neither state, and no source anywhere gives a frequency for either, so
# inventing one would be rule 5. They are vocabulary and routing only: nothing
# in this repo measures what they cost. Said plainly because the temptation to
# put a number beside a good story is exactly how this project got errors 5,
# 7 and 8.

#: The money IS in the account and ANOTHER MANDATE HAS ALREADY CLAIMED IT.
#: Razorpay reason `funds_blocked_by_mandate`.
#:
#: This is cross-merchant contention, in the production error vocabulary of the
#: company we are pitching to. It is not FUNDS -- the balance is adequate. It is
#: not LIMIT -- no limit was breached and a smaller debit does not obviously
#: help, because we do not know the size of the other lien. It is the one
#: decline code that is ABOUT the thing this whole architecture is built on:
#: a customer's mandates compete, and a merchant who can only see their own
#: cannot tell this apart from an empty account.
#:
#: What a single-merchant retry engine does with it: nothing, because it reads
#: as a failure and it retries. What we can do with it: it is direct evidence
#: that the customer HAD money at a known time, which is the opposite of what
#: `BeliefPD.observe(amount, False)` would record -- that call hard-zeroes every
#: balance bin at or above the amount (`w3.py:432`). Feeding this to the filter
#: as a plain failure teaches it something FALSE.
FAMILY_LIEN = "LIEN"                      # funds_blocked_by_mandate

#: WE DO NOT KNOW WHETHER THE DEBIT HAPPENED. Razorpay reasons
#: `deemed_transaction` and `duplicate_rrn_found`.
#:
#: Every other family answers "why did it fail". This one refuses to answer
#: "did it fail". A deemed transaction may have moved the customer's money and
#: lost the response; retrying may debit them twice.
#:
#: DOUBLE-CHARGING IS THE WORST OUTCOME THIS SYSTEM CAN PRODUCE -- worse than
#: never collecting, because it costs a refund, a complaint and probably the
#: mandate. That is not a new opinion: it is the verbatim finding of error 19,
#: where `RuleBasedDiagnoser` proposed a second debit on a collected cycle.
#:
#: AND IT IS THE CLEANEST ARGUMENT IN THIS REPO FOR A DIAGNOSIS LAYER.
#: `w3.index_score(p_now, p_later, amount)` reads two probabilities and a
#: discount. There is no arrangement of those three numbers that means "do not
#: act, because the question you are asking is unanswerable". An index over a
#: boolean cannot represent an unknown; a diagnosis can.
FAMILY_INDETERMINATE = "INDETERMINATE"    # deemed_transaction, duplicate_rrn_found

#: Drawn uniformly within a family: nothing found ranks the members against
#: each other, and inventing a within-family split would stack a second [GUESS]
#: on the first for no gain.
#: THE TWO NEW FAMILIES ARE KEYED ON RAZORPAY'S STRINGS, NOT ON NPCI CODES,
#: and that is not an oversight. NPCI's published list as read in
#: `agent/eval/golden_cases.yaml` names no code for either state. Inventing an
#: NPCI-looking symbol for a family NPCI does not name would be a rumour
#: wearing a source's clothes (rule 4), so the code IS the provenance: these
#: two families are spelled the way the only document that names them spells
#: them.
FAMILY_CODES: dict[str, tuple[str, ...]] = {
    FAMILY_OK: ("OK",),
    FAMILY_FUNDS: ("Z9",),
    FAMILY_TECH: ("TECH",),
    FAMILY_ACCOUNT_SHUT: ("ZX", "YE"),
    FAMILY_MANDATE_BROKEN: ("VD", "VI", "VF"),
    FAMILY_LIMIT: ("Z8", "IE"),
    FAMILY_AMBIGUOUS: ("U30",),
    FAMILY_LIEN: ("funds_blocked_by_mandate",),
    FAMILY_INDETERMINATE: ("deemed_transaction", "duplicate_rrn_found"),
}

CODE_FAMILY: dict[str, str] = {c: fam for fam, cs in FAMILY_CODES.items()
                               for c in cs}

#: The debit failed and NO retry can ever help. The only correct response is to
#: stop and hand the mandate back to the merchant.
TERMINAL_CODES = frozenset(FAMILY_CODES[FAMILY_ACCOUNT_SHUT]
                           + FAMILY_CODES[FAMILY_MANDATE_BROKEN])

#: The money exists; this particular request was refused for being too large or
#: too frequent. A smaller debit is the right answer -- and PARTIAL is still a
#: recommendation only, because its legality under one mandate is unestablished.
LIMIT_CODES = frozenset(FAMILY_CODES[FAMILY_LIMIT])

#: May be re-presented under the SAME pre-debit notification. Only a technical
#: decline may; every business decline needs a fresh one. docs/01_FACTS.md.
REPRESENTABLE_CODES = frozenset({TECH})

#: NO RETRY MAY EVER BE ISSUED AGAINST ONE OF THESE, and the reason is the
#: opposite of the reason for `TERMINAL_CODES`. A terminal code means a retry
#: cannot WORK. These mean a retry is not SAFE: the first debit may already
#: have gone through, so a second one charges the customer twice.
#:
#: `RETRY` is refused on these by the diagnosis layer, not by Stage 0. That
#: split is deliberate and worth stating: Stage 0's five rules are the five
#: external regulatory constraints in docs/01_FACTS.md, and this is not one of
#: them -- it is OUR judgement about OUR exposure. Smuggling a sixth rule into
#: a layer whose whole claim is "these five are NPCI's" would make the
#: compliance claim harder to check, not easier.
INDETERMINATE_CODES = frozenset(FAMILY_CODES[FAMILY_INDETERMINATE])

#: The money was there and someone else's mandate had already claimed it. NOT
#: a balance observation: see FAMILY_LIEN above for why feeding this to
#: `BeliefPD.observe(amount, False)` teaches the filter something false.
LIEN_CODES = frozenset(FAMILY_CODES[FAMILY_LIEN])


def family_of(code: str) -> str:
    """Family for a response code. Unknown codes are AMBIGUOUS, never guessed
    into a family -- a code we cannot name is exactly the U30 situation."""
    return CODE_FAMILY.get(code, FAMILY_AMBIGUOUS)


# ------------------------------------------------------------------- banks
# Added 29 August 2026. Lives here, not in `agent/execution/`, because gate I2
# forbids anything outside `constraints/stage0.py` importing `agent.execution`
# and the sweep needs these. `bank_of` is a pure function of a customer index
# and `BANK_HANDLES` is a table of strings: neither is execution, both are
# vocabulary, and ports.py imports nothing.
#
# `N_BANKS` and the UNIFORM assignment are [GUESS]. Real Indian UPI share is
# heavily skewed and nothing found gives per-bank AutoPay MANDATE share, so a
# skew we invented would be a constant with no source (rule 5). Uniform makes a
# single-bank outage cover about an eighth of customers; a realistic skew would
# make the largest bank's incident bigger and the smallest bank's smaller, so
# every single-bank number is the middle of a range nobody has measured.
N_BANKS = 8

#: Handles a merchant would already recognise: in real UPI the payer's VPA
#: carries the bank on its face (`@oksbi`, `@ybl`), so the remitter bank is
#: something the merchant can already see on their own transaction report. That
#: is why it is allowed across the redaction boundary -- `agent/llm/caseview.py`
#: has the argument, and `agent/llm/governance.py` still forbids NAMING it in
#: merchant-facing prose.
BANK_HANDLES = ("@oksbi", "@ybl", "@okhdfcbank", "@okicici", "@okaxis",
                "@paytm", "@ibl", "@upi")


def bank_of(customer_id: int, n_banks: int = N_BANKS) -> str:
    """Which bank holds this customer's account.

    Derived from a stable hash of the customer index rather than from any RNG,
    so it is identical across every run, seed and process and consumes nothing
    from the money path's stream. A bank assignment that moved with the seed
    would make a bank-scoped outage unreproducible."""
    import hashlib
    h = hashlib.blake2b(str(customer_id).encode(), digest_size=8).digest()
    return BANK_HANDLES[int.from_bytes(h, "big") % min(n_banks,
                                                       len(BANK_HANDLES))]


# =========================================================================
# RAZORPAY'S ERROR VOCABULARY, MAPPED ONTO OURS. Added 29 August 2026.
# =========================================================================
# WHY THIS LIVES IN ports.py AND NOT IN agent/execution/. It was written there
# first and gate I2 was right to reject it. Two reasons, and the second is the
# decisive one:
#
#   * I2 forbids anything outside `constraints/stage0.py` and the composition
#     root from importing `agent.execution`, and it makes no exception for a
#     module INSIDE that package importing a sibling.
#   * Rule I1 forbids `agent/llm` from importing `agent.execution` at all. A
#     diagnoser that wants to explain a decline to a merchant needs this table,
#     and in `agent/execution/` it could never reach it. ports.py is the only
#     lawful home for shared vocabulary -- the same argument that moved
#     `BANK_HANDLES` here on 29 August, recorded above.
#
# WHY A TRANSLATION LAYER EXISTS AT ALL, and it is not the reason you would
# guess. The taxonomy above is keyed on NPCI codes -- `Z9`, `ZX`, `VD`, `Z8`,
# `U30` -- read from NPCI's "UPI Error and Response Codes" v2.9. **Razorpay's
# API does not return those codes.** It returns its own normalised
# `error_reason` from a published list of 110 distinct values, plus an
# `error_code` of BAD_REQUEST_ERROR / GATEWAY_ERROR / SERVER_ERROR, an
# `error_source` and an `error_step`. Their own material describes an error
# mapping module that translates NPCI codes into merchant-legible terms, so the
# normalisation is deliberate on their side and the raw acquirer code is not
# part of the documented surface.
#
# So the boundary is `razorpay_reason -> our family`, never `npci_code ->
# family`. That was found by reading their list rather than by assuming ours
# was the lingua franca.
#
# SOURCE: `payments_error_reasons.xlsx`, downloaded from razorpay.com on
# 29 August 2026, 110 distinct reasons, committed verbatim as
# `agent/execution/razorpay_reasons.txt`. [VERIFIED], primary source, recorded
# in docs/01_FACTS.md.
#
# THIS ASSIGNS NO FREQUENCY TO ANYTHING. Which reasons are common in AutoPay
# traffic is unknown, no public source gives it, and docs/02_RESULTS.md sweeps
# every rate rather than picking one. A mapping is vocabulary; a rate would be
# an invented constant (rule 5).

# ---------------------------------------------------------------------------
# THE MAP. Every reason we could not place is in `UNMAPPED` below with a
# written reason -- an unmapped code falls to AMBIGUOUS, which is exactly the
# U30 situation and is the safe default, but a SILENT default would hide the
# fact that their vocabulary is richer than ours. That silence is the shape of
# error 9 in docs/03_ERRORS.md: a thing named after a concept, never re-checked
# against the object it stands for.
# ---------------------------------------------------------------------------

REASON_FAMILY: dict[str, str] = {}


def _fam(family: str, *reasons: str) -> None:
    for r in reasons:
        REASON_FAMILY[r] = family


# --- the money is not there. The only family the frozen belief filter models.
_fam(FAMILY_FUNDS,
     "insufficient_funds",
     "payment_declined",        # "funds could not be debited from the account"
     "debit_declined")

# --- the rail or a bank glitched. May be re-presented under the same notice.
_fam(FAMILY_TECH,
     "bank_technical_error", "gateway_technical_error", "issuer_technical_error",
     "upi_app_technical_error", "psp_app_not_available", "psp_app_ not_available",
     "psp_not_available", "server_error", "invalid_response_from_gateway",
     "vpa_resolution_failed", "credit_failed", "capture_failed",
     "payment_declined_due_to_high_traffic", "bank_not_available",
     # A bank end-of-day cutoff is transient and self-resolving like a glitch,
     # but it is NOT random -- see UNMAPPED, it deserves its own family and
     # does not have one yet.
     "bank_cutoff_in_progress")

# --- the account cannot be debited. Absorbing. No retry ever helps.
_fam(FAMILY_ACCOUNT_SHUT,
     "bank_account_invalid", "bank_account_validation_failed",
     "beneficiary_account_does_not_exist", "beneficiary_account_dormant",
     "debit_instrument_blocked", "debit_instrument_inactive",
     "transaction_on_vpa_restricted", "invalid_vpa",
     "user_not_eligible", "user_not_registered_for_netbanking",
     "pin_not_set", "pin_attempts_exceeded")

# --- the MANDATE is broken. No retry helps; the merchant must re-authorise.
_fam(FAMILY_MANDATE_BROKEN,
     "mandate_creation_declined", "mandate_creation_expired",
     "mandate_creation_failed", "mandate_creation_timeout",
     "reqauth_mandate_not_acknowledged", "authorisation_declined_by_psp",
     "recurring_payment_not_enabled", "upi_autopay_not_supported_on_psp",
     "bank_not_enabled",
     "payment_method_not_enabled", "invalid_device", "invalid_user_details",
     "credit_limit_expired", "credit_limit_inactive",
     "credit_limit_not_approved", "credit_not_permitted",
     "card_expired", "card_not_enrolled", "card_number_invalid",
     "card_type_invalid", "card_network_not_enabled", "card_declined",
     "incorrect_card_details", "incorrect_card_expiry_date",
     "incorrect_cardholder_name", "incorrect_cvv", "authentication_failed",
     "incorrect_otp", "otp_expired", "otp_attempts_exceeded",
     "incorrect_atm_pin", "incorrect_pin",
     "international_transaction_not_allowed",
     "emi_plan_unavailable", "emi_greater_than_max_amount",
     "collect_on_mcc_blocked", "upi_collect_not_enabled",
     "upi_intent_not_enabled", "merchant_not_activated",
     "live_mode_not_enabled", "compliance_violation",
     "payment_risk_check_failed")

# --- the money IS there; this REQUEST was too large or too frequent.
#     A smaller debit works for the amount limits. IT DOES NOT WORK for the
#     count limits -- see UNMAPPED.
_fam(FAMILY_LIMIT,
     "transaction_limit_exceeded", "mcc_amount_limit_exceeded",
     "credit_limit_exceeded", "transaction_daily_limit_exceeded",
     "amount_less_than_minimum_amount", "refund_limit_crossed",
     "transaction_daily_count_exceeded", "transaction_frequency_limit_exceeded")

# --- the money is there and ANOTHER MANDATE HAS IT. Added 29 Aug 2026.
_fam(FAMILY_LIEN, "funds_blocked_by_mandate")

# --- we do not know WHETHER it happened. Added 29 Aug 2026. NEVER RETRY.
_fam(FAMILY_INDETERMINATE,
     "deemed_transaction", "duplicate_rrn_found", "duplicate_request",
     "payment_pending", "payment_pending_approval",
     "payment_timed_out", "request_timed_out")

# --- names nothing useful, or is our own integration bug rather than a decline.
_fam(FAMILY_AMBIGUOUS,
     "payment_failed", "payment_cancelled", "payment_session_expired",
     "payment_collect_request_expired", "collect_request_pending",
     "mismatch_in_transaction_details", "payment_amount_tampered",
     "input_validation_failed", "invalid_request", "invalid_amount",
     "invalid_currency", "invalid_email", "invalid_mobile_number",
     "mobile_number_invalid", "invalid_order_id", "order_already_paid",
     "order_amount_mismatch", "order_payment_method_mismatch",
     "record_not_found", "duplicate_refund_id", "verification_failed",
     "psp_app_not_supported", "psp_not_registered")


# ---------------------------------------------------------------------------
# THE REPORT. Reasons in Razorpay's list that our families cover only by
# flattening a distinction that changes the CORRECT ACTION. Two of them became
# new families; the rest are recorded rather than quietly absorbed.
# ---------------------------------------------------------------------------

#: Keys in `REASON_FAMILY` that are NOT in Razorpay's published list, each with
#: a written reason. `test_razorpay_mapping.py` R1b fails on any key not listed
#: here, which is what caught `deemed_transaction_unknown` -- a code invented
#: while writing this map and cited as if it came from the document.
#:
#: This list is DEBT, not permission, in the same sense as
#: `sim/known_failures.txt`. Adding a line to silence R1b is the same offence
#: as loosening a threshold.
KNOWN_EXTRA_KEYS: dict[str, str] = {
    "psp_app_not_available":
        "Razorpay's published spreadsheet spells this `psp_app_ not_available`"
        " -- with a space after the underscore. That is almost certainly a"
        " typo in their document rather than the string their API emits, but"
        " we cannot tell which without a key, so BOTH spellings are mapped to"
        " TECH and this one is declared here. Remove it the moment a live"
        " response settles the question.",
}


#: (reason, what our taxonomy loses, what we did about it)
UNMAPPED_DISTINCTIONS: tuple[tuple[str, str, str], ...] = (
    ("funds_blocked_by_mandate",
     "Money present, claimed by another mandate. Not FUNDS (balance is fine) "
     "and not LIMIT (nothing was breached). Feeding it to the belief filter as "
     "a plain failure hard-zeroes every balance bin at or above the amount "
     "(w3.py:432) and teaches it something FALSE.",
     "NEW FAMILY: LIEN. Not simulated, no rate."),

    ("deemed_transaction / duplicate_rrn_found",
     "We do not know WHETHER the debit happened, not why it failed. A retry "
     "may debit the customer twice, which is the worst outcome this system "
     "can produce (error 19).",
     "NEW FAMILY: INDETERMINATE. RETRY is refused on it. Not simulated."),

    ("transaction_daily_count_exceeded / transaction_frequency_limit_exceeded",
     "A COUNT limit, not an AMOUNT limit. Our LIMIT family means 'a smaller "
     "debit works'. For these it does not -- the only fix is to wait for the "
     "counter to roll over.",
     "Mapped to LIMIT and FLAGGED. Splitting LIMIT into amount/count is a "
     "real repair and it needs a world that models either, which the frozen "
     "one does not."),

    ("bank_cutoff_in_progress",
     "A bank's end-of-day cutoff: transient, self-resolving and SCHEDULED. "
     "TECH means 'the rail glitched, retry costs an attempt'. This one is the "
     "only decline in their list that is a TIMING fact, which is the one kind "
     "of fact our timing layer could actually act on.",
     "Mapped to TECH and FLAGGED. It is the most interesting thing in their "
     "list that we cannot yet use."),

    ("payment_pending / payment_pending_approval",
     "UPI has a genuinely PENDING state that resolves later. Rounding it to "
     "'failed' is the reading that licenses a retry.",
     "Mapped to INDETERMINATE and surfaced as AttemptOutcome.pending=True."),

    ("compliance_violation / payment_risk_check_failed",
     "A risk or AML block. Terminal, but the remediation is neither "
     "'re-authorise the mandate' nor 'the account is shut'.",
     "Mapped to MANDATE_BROKEN, which gets the STOP right and the "
     "explanation wrong."),

    ("mandate_creation_* (4 reasons)",
     "Registration-time failures. Our taxonomy assumes the mandate already "
     "exists, because the simulation starts after authorisation.",
     "Mapped to MANDATE_BROKEN. Out of scope rather than mis-modelled."),
)


def family_for_reason(reason: str | None) -> str:
    """Our family for a Razorpay `error_reason`.

    An unknown reason is AMBIGUOUS, never guessed into a family. A code we
    cannot name IS the U30 situation, and inventing a home for it would be
    exactly the confident wrongness this project keeps writing up.
    """
    if not reason:
        return FAMILY_AMBIGUOUS
    return REASON_FAMILY.get(reason, FAMILY_AMBIGUOUS)


def code_for_reason(reason: str | None) -> str:
    """Our canonical code for a Razorpay `error_reason`.

    The first member of the family, except that LIEN and INDETERMINATE are
    spelled with Razorpay's own strings -- see FAMILY_CODES in ports.py for
    why a family NPCI does not name does not get an NPCI-looking symbol.
    """
    fam = family_for_reason(reason)
    if fam in (FAMILY_LIEN, FAMILY_INDETERMINATE):
        # Preserve WHICH member, since the two INDETERMINATE members mean
        # measurably different things to a human reading the trail.
        if reason in FAMILY_CODES[fam]:
            return reason
    return FAMILY_CODES[fam][0]


def is_pending(reason: str | None) -> bool:
    """Did the rail decline to tell us what happened?

    This is the ONLY predicate that reads `AttemptOutcome.pending` into
    existence, and it is deliberately narrow: a timeout and a deemed
    transaction, not every failure we dislike. Widening it would turn every
    ordinary decline into an unknown and stop the belief filter learning
    anything, which is a much larger harm than the one it prevents.
    """
    return family_for_reason(reason) == FAMILY_INDETERMINATE


def summarise_coverage() -> dict:
    """What our seven-plus-two families do and do not cover. Printed by
    `agent/tests/test_razorpay_mapping.py` so the gap is a measured artifact
    rather than a paragraph."""
    by_family: dict[str, int] = {}
    for fam in REASON_FAMILY.values():
        by_family[fam] = by_family.get(fam, 0) + 1
    return dict(reasons_mapped=len(REASON_FAMILY),
                by_family=dict(sorted(by_family.items())),
                distinctions_lost=len(UNMAPPED_DISTINCTIONS))


# ---------------------------------------------------------------------- time
@dataclass(frozen=True, order=True)
class Clock:
    """Absolute hour index, exactly as sim/harness.py counts time."""
    t: int

    @property
    def day(self) -> int:
        return self.t // 24

    @property
    def hour(self) -> int:
        return self.t % 24


# ------------------------------------------------------------------ identity
@dataclass(frozen=True, order=True)
class MandateRef:
    customer_id: int
    mandate_index: int
    merchant_id: int

    @property
    def uid(self) -> str:
        """Stable string key. Used as the audit log's mandate_uid."""
        return f"c{self.customer_id}m{self.mandate_index}"


# ------------------------------------------------------------------- actions
class InterventionKind(Enum):
    """What the agent can decide to do about a mandate.

    WAIT WAS CUT ON 29 AUGUST 2026. It was unreachable from every branch of
    `RuleBasedDiagnoser`, had exactly one supporting golden case (GC-22), and
    the action ablation measured it at approximately zero. Removing it was
    preferred to adding a branch to reach it.

    ONE MEASURED CAVEAT, recorded because it complicates the decision rather
    than supporting it: WAIT was unreachable for the RULE ENGINE, but in the
    first live eval it was `glm-5.3-flash`'s MOST-USED answer -- 11 of 40
    registered cases. So this removed an action one diagnoser never reached and
    the other reached constantly, and the eval was re-run to measure what that
    did. Waiting still happens; it is just no longer an INTERVENTION the
    narrative layer can name. The timing layer's own "the future looks better
    than now" verdict is `policy.timing.Reason.WAIT` and is untouched -- that is
    the frozen index doing its job, not a diagnosis.

    PARTIAL is deliberately absent. Whether a partial debit is permitted under
    one UPI AutoPay mandate is not established in docs/01_FACTS.md, and a
    merchant-acceptance rate for it would be an invented constant (rule 5).
    It survives as a RECOMMENDATION only -- see `Recommendation` below -- which
    credits zero money and never reaches the gate.
    """
    RETRY = "RETRY"           # money action: attempt a debit
    NUDGE = "NUDGE"           # non-money: ask the customer to fund the account
    ESCALATE = "ESCALATE"     # non-money: hand to a human / merchant queue
    STOP = "STOP"             # no further money action this cycle


MONEY_ACTIONS = frozenset({InterventionKind.RETRY})


class RootCause(Enum):
    INSUFFICIENT_FUNDS = "INSUFFICIENT_FUNDS"
    TIMING_MISMATCH = "TIMING_MISMATCH"     # money exists, we asked on the wrong day
    TECHNICAL = "TECHNICAL"
    MANDATE_AT_RISK = "MANDATE_AT_RISK"     # one attempt from death
    # --- added 29 Aug 2026 with the richer decline taxonomy above. Purely
    # additive: every value the golden cases already use is unchanged.
    ACCOUNT_UNAVAILABLE = "ACCOUNT_UNAVAILABLE"   # frozen/dormant. STOP FOREVER.
    MANDATE_INVALID = "MANDATE_INVALID"           # revoked/expired/paused. Re-authorise.
    LIMIT_EXCEEDED = "LIMIT_EXCEEDED"             # money is there. Debit smaller.
    RAIL_OUTAGE = "RAIL_OUTAGE"                   # the rail, not this customer.
    # --- added 29 Aug 2026 with FAMILY_LIEN and FAMILY_INDETERMINATE.
    FUNDS_LIENED = "FUNDS_LIENED"                 # money is there; another mandate has it.
    OUTCOME_UNKNOWN = "OUTCOME_UNKNOWN"           # we cannot tell if it succeeded.
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class PendingNotification:
    """A pre-debit notification that has been issued and not yet consumed.

    `notify_t` is None for a re-presentation under a previous notification,
    which is legal ONLY after a technical decline.
    """
    notify_t: int | None
    target_t: int
    under_previous_notice: bool


@dataclass(frozen=True)
class MoneyAction:
    """A request to move money. The ONLY thing Stage 0 adjudicates."""
    action_id: str
    ref: MandateRef
    amount: Rupees
    cycle: int
    target_t: int
    notify_t: int | None
    decided_at_t: int
    kind: InterventionKind = InterventionKind.RETRY
    # what the policy layer thought, carried for the audit trail only
    p_now: float = 0.0
    p_later: float = 0.0
    index_score: float = 0.0
    diagnosis_id: str = ""


@dataclass(frozen=True)
class AttemptOutcome:
    """The world's answer to one debit request.

    `pending` WAS ADDED 29 AUGUST 2026 AND IT IS A MODELLING FIX, NOT A
    FEATURE. Real UPI has three answers, not two: it succeeded, it failed, or
    the response was lost and NOBODY KNOWS WHICH. Razorpay's own published
    reason list carries `deemed_transaction` and `duplicate_rrn_found` for
    exactly that state ([VERIFIED], docs/01_FACTS.md). A `bool` cannot say it,
    so until today this codebase could only round an unknown down to "failed" --
    and "failed" is the one reading that licenses a retry, which is the reading
    that double-debits a customer who was already charged.

    IT DEFAULTS TO FALSE AND `SimExecutor` NEVER SETS IT. Every frozen number,
    the 24/24 parity gate and the whole gated suite are therefore untouched;
    `test_pending_outcome.py` asserts that rather than assuming it. Only
    `RazorpayExecutor` can produce `pending=True`, because only a real rail can
    lose a response.

    `success` STAYS FALSE WHEN `pending` IS TRUE, deliberately. No money may be
    credited for an outcome nobody knows, and any caller that has not been
    taught about `pending` keeps its old, conservative reading. The DANGEROUS
    reading is the retry decision, and that is the one place the field is
    consulted: see `INDETERMINATE_CODES` above.
    """
    t: int
    code: str               # ALWAYS our vocabulary. See FAMILY_CODES.
    success: bool
    pending: bool = False   # the rail did not tell us. NEVER retry on this.
    raw_code: str = ""      # what the rail actually said, verbatim. Audit only.


@dataclass(frozen=True)
class WorkflowResult:
    """Outcome of a non-money workflow (reminder, backup checkout, escalate).

    `executed` is true only if the side effect actually happened: an email
    send, a Payment Link create, a queue row. A cap skip is executed=False.
    `credited` means this cycle's amount was collected (backup link paid).
    `status` is the Payment Link state when the channel is a checkout:
    issued / paid / expired / cancelled / "".
    """
    executed: bool
    credited: bool = False
    channel: str = ""
    vendor_id: str = ""
    detail: str = ""
    status: str = ""
    short_url: str = ""


@dataclass(frozen=True)
class Refusal:
    rule: str               # cap | peak | lead | pending | represent
    detail: str


@dataclass(frozen=True)
class Allowed:
    outcome: AttemptOutcome


@dataclass(frozen=True)
class Refused:
    refusal: Refusal


Decision = Allowed | Refused


# ------------------------------------------------------------- policy output
@dataclass(frozen=True)
class ScheduleProposal:
    """The timing layer's answer. Only the timing layer may construct one."""
    target_day: int
    target_t: int
    notify_t: int
    p_now: float
    p_later: float
    index_score: float


@dataclass(frozen=True)
class TimingSummary:
    """Belief summary for the POLICY layer. Carries rupee-denominated state."""
    expected_balance: Rupees
    payday_entropy: float
    top_hypothesis_weight: float


@dataclass(frozen=True)
class PaydayUncertainty:
    """Belief summary for the LLM layer. Carries NO rupee-denominated state.

    This is the redaction seam. `TimingSummary` has an expected balance in it;
    this does not, and this is the only one `agent/llm` is allowed to see. A
    narrative layer cannot leak a balance it was never handed.
    """
    payday_entropy: float
    top_hypothesis_weight: float

    @property
    def band(self) -> Literal["narrow", "medium", "wide"]:
        """Coarse label. The LLM sees this, not the float."""
        if self.top_hypothesis_weight >= 0.60:
            return "narrow"
        if self.top_hypothesis_weight >= 0.25:
            return "medium"
        return "wide"


# ---------------------------------------------------------------- LLM layer
@dataclass(frozen=True)
class CaseView:
    """The ONLY thing `agent/llm` ever sees about a mandate.

    Everything here is either a count, a coarse band, or the mandate's own
    contracted amount -- which the merchant already knows, because it is their
    own subscription price. There is no balance, no salary, no p_success, no
    payday, and no posterior.
    """
    case_hash: str
    attempts_used: int
    attempts_cap: int
    day_in_cycle: int
    days_left_in_cycle: int
    amount: Rupees                  # the merchant's own price. Not customer state.
    decline_history: tuple[str, ...]        # e.g. ("Z9", "Z9", "TECH")
    n_recent_z9: int
    peer_mandate_success_recent: bool       # did another merchant just succeed?
    uncertainty_band: str                   # narrow | medium | wide
    merchant_note: str = ""                 # UNTRUSTED free text from merchant metadata
    bank: str = ""                          # remitter bank handle. See caseview.py.

    @property
    def decline_families(self) -> tuple[str, ...]:
        """`decline_history` mapped through `family_of`. Convenience only --
        the codes themselves are what a merchant sees on their report."""
        return tuple(family_of(c) for c in self.decline_history)

    @property
    def has_terminal_code(self) -> bool:
        """Did any attempt come back with a code no retry can ever fix?"""
        return any(c in TERMINAL_CODES for c in self.decline_history)


@dataclass(frozen=True)
class Diagnosis:
    """LLM (or fallback) output.

    NOTE WHAT IS ABSENT: no day, no hour, no target_t, no delay, no
    "retry_at". The type cannot express a time. See the module docstring.
    """
    diagnosis_id: str
    root_cause: RootCause
    intervention: InterventionKind
    confidence: float
    rationale: str                          # merchant-facing. Governance-checked.
    source: Literal["llm", "fallback"]
    prompt_id: str = ""
    recommendations: tuple[str, ...] = ()   # e.g. "PARTIAL" -- credits zero money


# ------------------------------------------------------------------ stopping
class StopRule(Enum):
    COLLECTED = "COLLECTED"
    CAP_REACHED = "CAP_REACHED"
    CYCLE_CLOSED = "CYCLE_CLOSED"
    NO_LEGAL_SLOT = "NO_LEGAL_SLOT"
    MANDATE_DEAD = "MANDATE_DEAD"
    ESCALATED = "ESCALATED"
    AGENT_STOP = "AGENT_STOP"       # the diagnosis layer chose STOP
    LAST_ATTEMPT_HELD = "LAST_ATTEMPT_HELD"  # 4th debit replaced by unpaid backup link
    # Batch-wide legal maximum: n_mandates × 4 × cycles in the horizon.
    # Circuit breaker, not a consumable. Fires only if a per-mandate cap bug
    # would exceed that total. Expected count 0 on a clean run.
    BATCH_LEGAL_CEILING = "BATCH_LEGAL_CEILING"


# ------------------------------------------------------------------- ports
class Executor(Protocol):
    """The world. Only `agent.constraints.stage0.Stage0Gate` may hold one."""

    def attempt(self, ref: MandateRef, amount: Rupees, t: int,
                action_id: str = "") -> AttemptOutcome:
        """`action_id` is the id Stage 0 already audited this action under.

        It is here so a backend that needs an idempotency key can derive one
        from the SAME identity the audit trail uses, rather than from wall
        clock or a fresh uuid -- the whole point of an idempotency key being
        that a retried request after a crash produces the same key. Backends
        that do not need it, such as `SimExecutor`, accept and ignore it.

        Defaulted, so an implementation written against the three-parameter
        version still satisfies the protocol. Added 30 August 2026: Stage 0 had
        the id on the line above the dispatch and was not passing it.
        """
        ...


class Diagnoser(Protocol):
    """MUST NOT RAISE. Ever. An LLM failure is an event, not an exception."""

    def diagnose(self, view: CaseView) -> Diagnosis:
        ...
