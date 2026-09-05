"""Build a demonstration database for the operator console, offline only.

WHY THE DATABASE IS NOT COMMITTED. It used to be, and a committed database is a
frozen clock: `epoch_origin` is on disk and every `target_t` is measured from
it, so the file drifts one simulated day behind itself for every real day it
sits in the repository. The one that was checked in had been saved at hour 752
and started at hour 35, and reaching its first retry took 113 clicks. It also
carried a customer's email and phone number inside stored webhook payloads.
`live/data/` is gitignored; run this instead.

WHAT THIS SCRIPT MAY NOT DO. It writes no attempt, transition or webhook row.
Every row in the database it produces was written by the same code that would
write it in production: registration, the customer authorising the mandate,
`decide`, and the mock rail's webhooks going through signature verification and
the real state machine. A state that cannot be reached that way is not seeded.

THE ONE THING IT SCRIPTS is the rail's answers. `MockPlan.debits` is consumed
in call order, so the mandates are driven one at a time and each takes the next
entry in the list. Without that the mock is a coin flip and the seeded states
would differ every run.

Run:

    py -3.12 scripts/seed_demo_console.py
    py -3.12 -m live.server
"""
from __future__ import annotations

import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# THE REFUSAL COMES BEFORE THE IMPORTS THAT COULD REACH A RAIL. `live.config`
# is what decides which provider is built, so the check that this process can
# never be pointed at Razorpay has to run before anything reads it.
if (os.environ.get("RECOVERY_MODE") or "offline").strip().lower() != "offline":
    sys.stderr.write(
        "seed_demo_console.py refuses to run with RECOVERY_MODE="
        f"{os.environ.get('RECOVERY_MODE')!r}. It registers mandates, "
        "authorises them and runs decision ticks; against a live rail that is "
        "real money. Unset RECOVERY_MODE or set it to 'offline'.\n")
    raise SystemExit(2)

#: `live/config.py:DEFAULT_MAX_DEBIT_PAISE` is 500 -- a Rs 5 ceiling on one
#: live debit, which is a real safety limit and keeps its default. It is what
#: forced the previous demonstration database to two identical Rs 1 mandates.
#: Raised HERE, in this process only, because nothing this script touches can
#: reach a rail that moves money.
os.environ.setdefault("RECOVERY_MAX_DEBIT_PAISE", "300000")

import agent  # noqa: E402,F401  -- puts sim/ on the path
from agent.execution.razorpay_mock import MockPlan, MockRazorpayApi  # noqa: E402
from live.config import load  # noqa: E402
from live.domain import ATTEMPT_PRESENTED, AttemptState  # noqa: E402
from live.service import LiveService  # noqa: E402

#: Three mandates that differ in amount, in cycle position and in state, so the
#: console's list is legible at a glance rather than three copies of one row.
#: `est_salary` and `est_payday` are the operator's stated estimate, which is
#: all a real integration has -- see `live/domain.py:Mandate`.
#:
#: THE THIRD STATE IS THE END OF THE ESCALATION LADDER. Three presentations
#: spent on funds declines, two funding reminders sent, and a Payment Link
#: standing where the fourth mandate debit would have gone -- so the mandate
#: survives into the next cycle instead of dying at the NPCI cap.
#:
#: It was unreachable until the ladder was wired into `live/service.py`: a
#: single decline made the diagnoser answer NUDGE, the service read that as a
#: cancelled debit, and the mandate never reached a second attempt. See
#: `live/tests/test_ladder.py` for the measurement.
PEOPLE = [
    dict(name="Rohit Desai", email="rohit.desai@example.in",
         contact="+919812000002", charge_paise=64900, est_salary=38000.0,
         est_payday=2,
         want="collected on the first attempt of the cycle"),
    dict(name="Meera Iyer", email="meera.iyer@example.in",
         contact="+919812000001", charge_paise=189900, est_salary=52000.0,
         est_payday=2,
         want="declined once on funds, funding reminder sent, holding"),
    dict(name="Kavya Menon", email="kavya.menon@example.in",
         contact="+919812000003", charge_paise=249900, est_salary=61000.0,
         est_payday=2,
         want="three presentations spent, a Payment Link holding the fourth"),
]

#: The rail's answers, in the order the drives below consume them: one capture
#: for Rohit, one decline for Meera, three declines for Kavya. The trailing
#: entries are there so a change in the scheduler produces a wrong seed rather
#: than a random one.
#:
#: `insufficient_funds` is Razorpay's own `error_reason` and maps to the code
#: the console shows as Z9. It is the whole premise: the account was empty at
#: the moment of the charge.
DEBITS = ["captured",
          "failed:insufficient_funds",
          "failed:insufficient_funds",
          "failed:insufficient_funds",
          "failed:insufficient_funds",
          "failed:insufficient_funds",
          "failed:insufficient_funds"]

#: How far the demonstration clock moves between ticks, and the ceiling on how
#: long one mandate may be driven before the script gives up. The clock is
#: shared, so the drives run in sequence and the whole seed lands inside the
#: first billing cycle.
STEP_H = 4
MAX_DRIVE_H = 24 * 40


def presented(svc: LiveService, mandate_id: str) -> int:
    """Presentations spent: attempts that reached the provider."""
    return len([a for a in svc.store.attempts_for(mandate_id)
                if a.state in ATTEMPT_PRESENTED])


def collected(svc: LiveService, mandate_id: str) -> bool:
    return any(a.succeeded for a in svc.store.attempts_for(mandate_id))


def drive(svc: LiveService, mandate_id: str, *, until, label: str) -> None:
    """Tick the real decision path, advancing the demonstration clock.

    Nothing here writes to the store. `decide` is the same method the console's
    button calls and the same one a timer would call in production.
    """
    spent = 0
    while spent <= MAX_DRIVE_H:
        svc.decide(mandate_id)
        svc.deliver_mock_webhooks()
        if until():
            return
        svc.advance_clock(STEP_H)
        spent += STEP_H
    raise SystemExit(f"seeding {label} ran out of clock after "
                     f"{MAX_DRIVE_H // 24} simulated days without reaching "
                     f"its target state. The scheduler's behaviour has "
                     f"changed; adjust the estimates in PEOPLE rather than "
                     f"writing the state directly.")


def seed(svc: LiveService, api: MockRazorpayApi) -> list[dict]:
    made = []
    for spec in PEOPLE:
        c = svc.create_customer(name=spec["name"], email=spec["email"],
                                contact=spec["contact"])
        m = svc.start_registration(
            customer_id=c.id, charge_amount_paise=spec["charge_paise"],
            # REQUESTED, NOT DECIDED HERE. The mock issues a token carrying
            # its own Rs 15,000 ceiling and the service adopts the PROVIDER's
            # value, so the console shows Rs 15,000 rather than this number.
            max_amount_paise=500000,
            est_salary=spec["est_salary"],
            est_payday=spec["est_payday"])
        # The customer approving the mandate on their phone. There is no
        # server call that does this on a real rail, which is why the mock
        # names the method `authorize` and not after an endpoint.
        auth = api.authorize(m.registration_order_id)
        m = svc.confirm_registration(m.id, auth.body["payment_id"])
        svc.deliver_mock_webhooks()
        made.append(dict(spec, customer=c, mandate=m))

    rohit, meera, kavya = made
    # ONE MANDATE AT A TIME, IN THIS ORDER. `MockPlan.debits` is a queue on the
    # rail, not a property of a mandate, so the order of the drives is what
    # decides which answer each debit gets.
    drive(svc, rohit["mandate"].id, label=rohit["name"],
          until=lambda: collected(svc, rohit["mandate"].id))
    drive(svc, meera["mandate"].id, label=meera["name"],
          until=lambda: presented(svc, meera["mandate"].id) >= 1)
    # To the top of the ladder: three declines, then the Payment Link.
    drive(svc, kavya["mandate"].id, label=kavya["name"],
          until=lambda: bool(svc.store.mandate(
              kavya["mandate"].id).backup_status))

    # A LAST TICK EACH, AT THE SAME HOUR. The drives finish at different points
    # on one shared clock, and a mandate whose newest decision is a week old
    # shows the console a stale answer. This asks all three what they would do
    # now. It can schedule, which costs no presentation; it cannot charge,
    # because a debit runs a tick after its notice.
    for row in made:
        svc.decide(row["mandate"].id)
        svc.deliver_mock_webhooks()
    return made


def main() -> int:
    replace = "--replace" in sys.argv[1:]
    config = load()
    if os.path.exists(config.db_path):
        if not replace:
            sys.stderr.write(
                f"{config.db_path} already exists. Delete it, or re-run with "
                f"--replace, which will.\n")
            return 1
        for suffix in ("", "-shm", "-wal"):
            try:
                os.remove(config.db_path + suffix)
            except FileNotFoundError:
                pass
        # THE AUDIT LOGS GO WITH THE DATABASE THEY DESCRIBE, and only then.
        # `AuditLog` opens one file per process start, so a directory that
        # keeps its database keeps every trail written against it. One whose
        # database has just been deleted keeps trails that point at rows that
        # no longer exist, and a growing pile of them.
        data_dir = os.path.dirname(config.db_path)
        orphaned = [n for n in os.listdir(data_dir)
                    if n.startswith("audit-") and n.endswith(".jsonl")]
        for name in orphaned:
            os.remove(os.path.join(data_dir, name))
        print(f"removed {os.path.relpath(config.db_path, ROOT)}"
              + (f" and {len(orphaned)} audit log"
                 f"{'' if len(orphaned) == 1 else 's'} written against it"
                 if orphaned else ""))
    os.makedirs(os.path.dirname(config.db_path), exist_ok=True)

    api = MockRazorpayApi(seed=17, plan=MockPlan(debits=list(DEBITS)))
    svc = LiveService(config, api=api)
    try:
        made = seed(svc, api)
        print(f"seeded {os.path.relpath(config.db_path, ROOT)} "
              f"at simulated hour {svc.now_t()} "
              f"(day {svc.now_t() // 24}, clock offset "
              f"{svc.clock_offset_h}h)")
        total = 0
        for row in made:
            m = svc.store.mandate(row["mandate"].id)
            c = svc.store.customer(m.customer_id)
            attempts = svc.store.attempts_for(m.id)
            got = sum(a.amount_paise for a in attempts if a.succeeded)
            total += got
            states = ", ".join(a.state.value for a in attempts) or "none"
            print(f"  c{c.seq}m{m.index_no}  {row['name']:<12s} "
                  f"Rs {m.charge_amount_paise / 100:>8,.2f}  "
                  f"cycle {m.cycle}  "
                  f"{presented(svc, m.id)}/4 presentations  "
                  f"collected Rs {got / 100:,.2f}")
            print(f"                 intended: {row['want']}")
            print(f"                 attempts: {states}")
            if m.reminders_sent or m.backup_status:
                print(f"                 ladder:   "
                      f"{m.reminders_sent} reminder(s) sent"
                      + (f", backup checkout {m.backup_status}"
                         if m.backup_status else ""))
            print(f"                 mandate id {m.id}")
        print(f"  total collected Rs {total / 100:,.2f}")
        if any(a.state is AttemptState.INTENT
               for row in made
               for a in svc.store.attempts_for(row["mandate"].id)):
            print("WARNING  a row is stranded in INTENT; the console will "
                  "refuse to tick that mandate")
            return 1
    finally:
        svc.store.close()
    # THE SERVER NEEDS THE SAME CEILING, AND IT IS NOT A DEFAULT ANYWHERE.
    # `live/config.py:DEFAULT_MAX_DEBIT_PAISE` is 500 paise -- a Rs 5 limit on
    # one live debit -- so a server started without this refuses every mandate
    # seeded above with "amount is above the configured ceiling". Raising the
    # default instead would raise it for the live rail too.
    print(f"\nnow:  RECOVERY_MAX_DEBIT_PAISE="
          f"{os.environ['RECOVERY_MAX_DEBIT_PAISE']} py -3.12 -m live.server")
    print("      (the same ceiling this script ran under; without it the "
          "service refuses every amount above Rs 5)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
