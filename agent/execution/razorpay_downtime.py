"""Razorpay's Payment Downtime feed, and an honest comparison with ours.

THE FIRST THING TO SAY IS THAT WE WERE WRONG ABOUT IT.

This project's outage argument was drafted as "their feed is system-wide, ours
is bank-shaped". **That is false.** The Payment Downtime API exists, it is
available with test keys, and its `instrument` object is scoped:

    {"id":"down_F8LCfthx90fMOo", "entity":"payment.downtime", "method":"upi",
     "begin":1593412063, "end":null, "status":"started", "scheduled":false,
     "severity":"high",
     "instrument":{"vpa_handle":"oksbi","psp":"google_pay","flow":"collect"}}

`vpa_handle` is `oksbi` -- the same handle vocabulary as `ports.BANK_HANDLES`.
Razorpay publishes bank-shaped downtime already, `GET /v1/payments/downtimes`
and `GET /v1/payments/downtimes/:id`, with webhooks `payment.downtime.started`,
`.updated` and `.resolved`. Read from their docs on 29 August 2026,
`[VERIFIED]`, recorded in `docs/results.md`.

So the moat argument does NOT get to be "they cannot see bank-level incidents".
They can, and they say so.

---------------------------------------------------------------------------
WHAT IS ACTUALLY LEFT, STATED NARROWLY ENOUGH TO BE CHECKED
---------------------------------------------------------------------------

Four differences survive that reading, and none of them is "we detect and they
do not":

1. **Different population.** Their feed reports degradation across Razorpay's
   own traffic, which is dominated by checkout flows -- their `instrument.flow`
   enumerates `collect`, `intent` and `in_app`. Nothing in their documentation
   says AutoPay *mandate execution* is what is being measured, and mandate
   debits are a small, differently-timed slice: 99.22% of ours land in a single
   hour of the day (`docs/results.md`). A feed healthy for checkout at
   14:00 says little about mandates at 08:00.

2. **A label, not a rate.** `severity` is `high` / `medium` / `low`, defined by
   what is affected rather than by how much of it fails. The timing layer needs
   a probability, and no mapping from that label to one exists that is not
   invented. Ours produces an exact binomial tail on our own attempts.

3. **A PSP is marked down only when ALL its handles are down** -- their words.
   That is a deliberately conservative trigger, appropriate for a status feed
   a human reads and wrong for an actuator: a partial PSP degradation is
   exactly the case where a mandate scheduler wants to move a debit by a day.

4. **Latency is measurable on ours and unstated on theirs.** Their docs say
   webhooks arrive "within a few seconds of the event", but the event is their
   detection, not the onset. We have a measured detection latency distribution
   against an oracle at the true change points, and a measured false-alarm rate
   of 0 in 48 runs.

**The correct posture is complement, not replacement.** Their feed is the
better first alarm and is free. Ours is scoped to the traffic we actually care
about and produces a number the scheduler can act on. If both are available,
the right design uses theirs as a prior and ours as the likelihood -- and that
design is NOT built, NOT measured, and is written here as a next step rather
than as a claim.

---------------------------------------------------------------------------
WHAT IS TESTED
---------------------------------------------------------------------------
Parsing, normalisation, the handle mapping and `agrees_with` are gated offline
against recorded payload shapes. **Whether test mode returns populated
downtimes at all is UNVERIFIED** -- their docs invite you to try the endpoint
with test keys but do not say whether test data is seeded, and no key has been
used by this project.
"""
from __future__ import annotations

from dataclasses import dataclass

from agent.ports import BANK_HANDLES

#: Their three severity labels. Deliberately NOT mapped to a probability: see
#: point 2 above. A dict from `high` to 0.8 would be an invented constant
#: sitting directly under a scheduling decision, which is rule 5 and is how
#: this project got errors 5 and 8.
SEVERITIES = ("low", "medium", "high")

#: `ALL` is what their docs say appears in `vpa_handle` when the whole of UPI
#: is affected, rather than one handle.
ALL_HANDLES = "ALL"

DOWNTIME_EVENTS = ("payment.downtime.started",
                   "payment.downtime.updated",
                   "payment.downtime.resolved")


@dataclass(frozen=True)
class Downtime:
    """One entry from their feed, normalised into our vocabulary.

    `handles` is the set of `ports.BANK_HANDLES` this entry covers, so it can
    be compared with `OutageSchedule(banks=...)` and with what `RailMonitor`
    saw. `None` means every handle.
    """
    id: str
    method: str
    begin: int
    end: int | None
    status: str
    scheduled: bool
    severity: str
    handles: tuple[str, ...] | None
    psp: str | None
    raw: dict

    @property
    def resolved(self) -> bool:
        return self.status == "resolved" or self.end is not None

    def covers_handle(self, handle: str) -> bool:
        return self.handles is None or handle in self.handles


def _handle_to_ours(h: str | None) -> tuple[str, ...] | None:
    """`oksbi` -> `("@oksbi",)`. `ALL` -> None. Unknown -> None, LOUDLY.

    An unrecognised handle returns None -- "assume it covers everything" --
    which is the CONSERVATIVE direction for a pause decision and the wrong
    direction for a claim about coverage. That asymmetry is why `parse` also
    records the unrecognised string in `raw`: a handle we cannot place must be
    visible, not silently widened.
    """
    if not h or h == ALL_HANDLES:
        return None
    ours = h if h.startswith("@") else "@" + h
    return (ours,) if ours in BANK_HANDLES else None


def parse(entity: dict) -> Downtime:
    """One `payment.downtime` object, from the API or from a webhook payload."""
    inst = entity.get("instrument") or {}
    return Downtime(
        id=str(entity.get("id", "")),
        method=str(entity.get("method", "")),
        begin=int(entity.get("begin") or 0),
        end=(int(entity["end"]) if entity.get("end") is not None else None),
        status=str(entity.get("status", "")),
        scheduled=bool(entity.get("scheduled", False)),
        severity=str(entity.get("severity", "")),
        handles=_handle_to_ours(inst.get("vpa_handle")),
        psp=inst.get("psp"),
        raw=entity,
    )


def parse_webhook(payload: dict) -> tuple[str, Downtime | None]:
    """`(event_name, Downtime)` from a webhook body.

    Returns `(event, None)` rather than raising on a shape we do not recognise.
    A webhook handler that raises on an unexpected body drops the delivery, and
    a dropped `resolved` leaves us paused on an outage that ended -- the failure
    mode is silent and expensive, so unknown shapes are surfaced as data.
    """
    event = str(payload.get("event", ""))
    ent = (((payload.get("payload") or {}).get("payment.downtime") or {})
           .get("entity"))
    if not isinstance(ent, dict):
        return event, None
    return event, parse(ent)


def active_at(downtimes, t_epoch: int, handle: str | None = None) -> list:
    """Every entry live at a wall-clock instant, optionally for one handle.

    Takes EPOCH SECONDS, while everything else in this repo counts integer
    hours from the start of a run. There is no conversion here on purpose:
    inventing an epoch for a simulated run would let simulated and real time
    be compared as if they were the same clock, which they are not.
    """
    out = []
    for d in downtimes:
        if d.begin > t_epoch:
            continue
        if d.end is not None and d.end <= t_epoch:
            continue
        if handle is not None and not d.covers_handle(handle):
            continue
        out.append(d)
    return out


def agrees_with(vendor_says_down: bool, ours_says_down: bool) -> str:
    """Cross-check, four ways, with no scoring.

    DELIBERATELY RETURNS A LABEL AND NOT A NUMBER. Turning agreement into an
    accuracy would require treating one feed as ground truth, and neither is:
    theirs measures a different traffic mix (point 1 above), ours measures a
    sample of about 22 attempts per day. A confusion matrix between two
    imperfect detectors of two different things is a number that looks like
    evidence and is not.

    The label a comparison run actually wants to look at is `WE_SEE_ONLY`: our
    stream degraded while their system-level feed stayed quiet. That is the
    case the moat argument rests on, and it is the case 2026's bank-shaped
    incidents would produce -- but it is also what a false alarm produces, and
    from one run you cannot tell which. `docs/results.md` records one such
    firing, outside every injected window, rather than assuming it was real.
    """
    if vendor_says_down and ours_says_down:
        return "BOTH"
    if vendor_says_down:
        return "VENDOR_ONLY"
    if ours_says_down:
        return "WE_SEE_ONLY"
    return "NEITHER"


class DowntimeFeed:
    """Poll or receive `payment.downtime` and keep the live set.

    NOT WIRED INTO THE AGENT LOOP, and that is a decision rather than an
    omission. `RailMonitor` already produces a verdict the loop consults, and
    the measured value of pausing on that verdict is INDISTINGUISHABLE FROM
    ZERO at every severity swept: +0.000 / +0.000 / +0.017 / +0.051, each
    inside its own 2 SE (`docs/results.md`, `logs/w27_abl_outage_repaired.txt`).
    An earlier measurement on the pre-repair belief read -0.529 and significant;
    that figure is retired. Adding a second, unmeasured input to a response that
    does not pay for itself would be shipping a feature because it is available.
    It is built so the comparison
    can be run; the comparison has to come first.
    """

    def __init__(self, transport=None, base: str = "https://api.razorpay.com/v1"):
        self._t = transport
        self.base = base
        self.live: dict[str, Downtime] = {}
        self.seen: list[tuple[str, Downtime]] = []

    def ingest_webhook(self, payload: dict) -> Downtime | None:
        event, d = parse_webhook(payload)
        if d is None:
            return None
        self.seen.append((event, d))
        if event == "payment.downtime.resolved" or d.resolved:
            self.live.pop(d.id, None)
        else:
            self.live[d.id] = d
        return d

    def poll(self) -> list[Downtime]:
        """`GET /payments/downtimes`. UNVERIFIED against a live response."""
        if self._t is None:
            raise RuntimeError("DowntimeFeed needs a transport to poll")
        status, payload = self._t.get(f"{self.base}/payments/downtimes")
        if status != 200:
            return list(self.live.values())
        items = payload.get("items") or []
        self.live = {}
        for ent in items:
            d = parse(ent)
            if not d.resolved:
                self.live[d.id] = d
        return list(self.live.values())

    def upi_down_for(self, handle: str | None = None) -> bool:
        return any(d.method == "upi" and d.covers_handle(handle or "")
                   for d in self.live.values()) if handle else any(
            d.method == "upi" for d in self.live.values())
