/* ===========================================================================
   Operator console behaviour.

   NO FRAMEWORK, and the reason is not minimalism for its own sake. The page is
   served under a content security policy that permits no third-party origin,
   the backend is a standard-library HTTP server with no build step, and the
   whole surface is one list, one spine and three sections. A bundler here
   would add a toolchain to maintain and would not make any of that easier to
   read.

   THE PAGE NEVER DECIDES ANYTHING, AND IT NEVER DERIVES ANYTHING THE BACKEND
   ALREADY COMPUTED. Every number rendered below is a field of `/api/state` or
   `/api/mandates/{id}` formatted for a human -- paise to rupees, an epoch
   second to a local time, a state name to a sentence. `attempts_used`,
   `gate_checks`, `p_now`, `index_score` and `uncertainty_band` are READ, never
   recomputed: reimplementing `_attempts_this_cycle` or the five Stage 0 rules
   in JavaScript would put a second, divergent copy of the money path's
   arithmetic in a browser.

   In particular `runDecision` posts an EMPTY body: the amount comes from the
   mandate the customer authorised, the time comes from the belief filter, and
   the legality comes from Stage 0. A console that could name an amount would
   be a hole in every one of those.
   =========================================================================== */
"use strict";

const $ = (id) => document.getElementById(id);

const state = {
  reveal: false,
  mandates: [],
  selected: null,
  detail: null,
  decisions: [],
  events: [],
  rejected: [],
  counts: null,
  config: null,
  health: null,
  token: "",
};

/* ------------------------------------------------------------------ fetch */

/* The operator token lives in sessionStorage: it is gone when the tab closes,
   is not shared with another tab, and never reaches disk. It travels in a
   custom header, which also makes any cross-site fetch preflight -- and the
   server answers no preflight. */
function operatorToken() {
  try { return sessionStorage.getItem("operator_token") || ""; }
  catch (e) { return state.token || ""; }
}

function setOperatorToken(value) {
  state.token = value;
  try { sessionStorage.setItem("operator_token", value); } catch (e) { /* private mode */ }
}

async function api(path, options) {
  const opts = Object.assign({ headers: {} }, options || {});
  if (opts.body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(opts.body);
  }
  const token = operatorToken();
  if (token) opts.headers["X-Operator-Token"] = token;
  const sep = path.includes("?") ? "&" : "?";
  const url = state.reveal ? `${path}${sep}reveal=1` : path;
  const res = await fetch(url, opts);
  let payload = {};
  try { payload = await res.json(); } catch (e) { payload = {}; }
  if (!res.ok) {
    const err = new Error(payload.error || `request failed (${res.status})`);
    err.status = res.status;
    throw err;
  }
  return payload;
}

/* ------------------------------------------------------------ formatting */

const PAISE = (n) =>
  typeof n === "number" ? `₹${(n / 100).toLocaleString("en-IN", {
    minimumFractionDigits: 2, maximumFractionDigits: 2 })}` : "—";

/* ONE CLOCK ON THIS PAGE, AND IT IS THE SIMULATED ONE.

   The service reasons in simulated hours counted from `epoch_origin`, and
   `hour 752` is the number it counts in. Nobody reads that as a moment. Every
   time on this page is rendered as `Day 27, 08:00` instead -- the same number,
   divided by 24 -- so the scheduler's target, the mandate's cycle and a
   webhook's arrival can be compared without a conversion in the reader's head.

   THE WALL CLOCK IS NOT DELETED, IT IS DEMOTED. `received_at`, `processed_at`
   and a transition's `at` are real seconds and a signature dispute is settled
   in real time, so the raw values stay -- inside the disclosures, labelled,
   where they cannot be mistaken for the clock the scheduler reasons in. */

/* Simulated hour -> a moment a person can read. */
const HOUR = (t) => {
  const h = Math.max(0, Math.trunc(Number(t) || 0));
  return `Day ${Math.floor(h / 24)}, ${String(h % 24).padStart(2, "0")}:00`;
};

/* THERE IS NO CONVERSION FROM A WALL-CLOCK SECOND TO THIS CLOCK, and there
   cannot be one. `advance_clock` moves the offset while the service runs, so a
   row stamped at a real second under one offset and read back under another
   would land on an hour it was never recorded at -- a webhook that answered a
   Day 12 debit rendering as Day 24 because the demonstration has since moved
   on. Every simulated time on this page comes from a field that IS a simulated
   hour: `target_t`, `notify_t`, `now_t`, `cycle_start_t`. Every wall-clock
   second is printed raw, by `RAW`, labelled, and only inside a disclosure. */

/* The raw wall clock. Used only where the real second is the point. */
const RAW = (ts) => {
  if (!ts) return "—";
  const d = new Date(ts * 1000);
  // A mandate expiry is ten years out. Dropping the year there turns 2036
  // into what looks like next week.
  const farOff = Math.abs(d - Date.now()) > 300 * 86400 * 1000;
  return d.toLocaleString(undefined, Object.assign(
    { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" },
    farOff ? { year: "numeric" } : {}));
};

const NUM = (x, dp) => Number(x).toFixed(dp === undefined ? 3 : dp);

/* A decline code and Razorpay's own word for it, never one without the
   other: the code is our vocabulary and the reason is theirs. */
function outcomeWords(a) {
  if (!a) return "";
  if (a.raw_reason && a.outcome_code) return `${a.outcome_code} · ${a.raw_reason}`;
  return a.raw_reason || a.outcome_code || "";
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

const SENTENCE = (s) =>
  !s ? "" : s.charAt(0).toUpperCase() + s.slice(1);

/* Enum to prose. A lookup table, so a value the backend adds shows up as
   itself rather than as a label somebody guessed. */
const INTERVENTION = {
  RETRY: "Retry the debit",
  NUDGE: "Prompt the customer to fund the account",
  ESCALATE: "Hand it to a human",
  STOP: "Stop for this cycle",
};
const CAUSE = {
  INSUFFICIENT_FUNDS: "insufficient funds",
  TIMING_MISMATCH: "asked on the wrong day",
  TECHNICAL: "a technical decline",
  MANDATE_AT_RISK: "the mandate is one attempt from death",
  ACCOUNT_UNAVAILABLE: "the account is unavailable",
  MANDATE_INVALID: "the mandate is no longer valid",
  LIMIT_EXCEEDED: "a limit was exceeded",
  RAIL_OUTAGE: "the rail, not this customer",
  FUNDS_LIENED: "the balance is claimed by another mandate",
  OUTCOME_UNKNOWN: "the outcome cannot be determined",
  UNKNOWN: "an undetermined cause",
};
/* The Payment Link that replaces the fourth mandate debit. Every state but the
   empty one holds that debit, and each holds it for a different reason. */
const BACKUP = {
  issued: "open — the fourth mandate debit is held until it is paid or closes",
  paid: "paid — this cycle is collected, and no debit was needed",
  expired: "closed unpaid — the cycle is forfeited so the mandate survives",
  cancelled: "cancelled — the cycle is forfeited so the mandate survives",
};
/* Why the fourth debit is held, in a sentence rather than a status. */
const BACKUP_WHY = {
  issued: "three debits declined on funds, so a payment link replaces the "
    + "fourth. Spending it would end the mandate and forfeit every remaining "
    + "billing cycle",
  paid: "the customer paid the link, so this cycle is collected and no fourth "
    + "debit was needed",
  expired: "the link closed unpaid. This cycle is forfeited and the fourth "
    + "debit is not fired, so the mandate survives into the next one",
  cancelled: "the link was cancelled. This cycle is forfeited and the fourth "
    + "debit is not fired, so the mandate survives into the next one",
};
const RULE_MEANS = {
  cap: "four presentations per cycle",
  peak: "no debit inside a peak window",
  lead: "24 hours between notice and debit",
  pending: "one outstanding notice at a time",
  represent: "no re-presentation of a funds decline",
};

/* ======================================================== environment ==== */

function renderEnv() {
  const cfg = state.config || {};
  const h = state.health || {};
  const live = cfg.mode === "live";
  const bar = $("env");

  // "PERMITTED" against the mock rail would be a claim about real money that
  // is not true, so offline gets its own words and its own colour temperature.
  bar.dataset.mode = live ? (cfg.debit_allowed ? "live" : "live-readonly")
    : "offline";
  $("env-state").textContent = live
    ? (cfg.debit_allowed
      ? `LIVE · ${cfg.key_prefix || "no key"} — REAL MONEY`
      : `LIVE · ${cfg.key_prefix || "no key"} — READ ONLY`)
    : "OFFLINE · MOCK RAIL — NO MONEY MOVES";

  const bits = [];
  if (cfg.debit_reason) bits.push(SENTENCE(cfg.debit_reason));
  // THE CEILING IS A LIVE-MONEY CONSTRAINT AND IS SHOWN IN BOTH LIVE MODES.
  // Offline it bounds nothing -- the mock rail moves no money -- so printing
  // it there is a number an operator has to work out is irrelevant.
  if (live && typeof cfg.max_debit_paise === "number") {
    bits.push(`ceiling ${PAISE(cfg.max_debit_paise)} per debit`);
  }
  if (!cfg.operator_auth_required) bits.push("operator API open on loopback");
  if (state.providerLost) {
    bits.push(`${state.providerLost} lost provider response${
      state.providerLost === 1 ? "" : "s"}`);
  }
  $("env-reason").textContent = bits.join(" · ");

  // The demonstration offset is not printed beside the clock: the clock ALREADY
  // includes it, so "Day 24, 00:00 · demonstration clock +564h" is one moment
  // stated twice, in two units.
  $("env-clock").textContent =
    typeof h.now_t === "number" ? HOUR(h.now_t) : "";
}

/* ============================================================= orders ==== */

function renderOrders() {
  const list = $("order-list");
  list.replaceChildren();
  $("orders-empty").hidden = state.mandates.length > 0;
  $("portfolio").hidden = state.mandates.length === 0;

  state.mandates.forEach((m) => {
    const li = document.createElement("li");
    const btn = el("button", "order-btn");
    btn.type = "button";
    btn.setAttribute("aria-current", String(m.id === state.selected));
    // The button's own text is two spans a screen reader reads as fragments.
    // Name it once, as a person and an amount.
    btn.setAttribute("aria-label",
      `${m.customer_name || m.id}, ${m.uid}, ${PAISE(m.charge_amount_paise)}, `
      + `${m.state.toLowerCase()}`);
    btn.append(el("span", "who", m.customer_name || m.id));
    btn.append(el("span", "sub",
      `${m.uid} · ${PAISE(m.charge_amount_paise)} · ${m.state.toLowerCase()}`));
    btn.addEventListener("click", () => select(m.id));
    li.append(btn);
    list.append(li);
  });

  const c = state.counts || {};
  $("pf-collected").textContent = PAISE(c.recovered_paise);
  $("pf-attempts").textContent =
    `${c.attempts_succeeded || 0} of ${c.attempts || 0}`;
  $("pf-open").textContent = String(c.attempts_unresolved || 0);
}

/* ====================================================== the answer line == */

/* State to a sentence. A lookup, because the alternative is a chain of
   conditions that quietly disagrees with `domain.py` the day a state is
   added. `key` is the phrase the display line colours. */
function answerFor(m, a) {
  if (!m) {
    return { lead: "", key: "The recovery agent", tail: "", tone: "" };
  }
  if (!m.chargeable) {
    const by = {
      PENDING: { lead: "Waiting for the customer to ", key: "approve the mandate",
                 tail: ".", tone: "h" },
      REJECTED: { lead: "The mandate was ", key: "rejected", tail: " at authorisation.",
                  tone: "q" },
      CANCELLED: { lead: "This mandate is ", key: "cancelled", tail: ".",
                   tone: "q" },
      PAUSED: { lead: "The customer has ", key: "paused", tail: " this mandate.",
                tone: "h" },
    }[m.state];
    if (by) return by;
    return { lead: "This order ", key: "cannot be charged", tail: ".", tone: "q" };
  }
  // THE LADDER OUTRANKS THE LAST ATTEMPT. Once a Payment Link stands in for
  // the fourth debit, "the debit was declined" describes an attempt that is no
  // longer what is happening to this mandate.
  const backup = {
    issued: { lead: "A ", key: "payment link", tail: " has replaced the fourth debit.",
              tone: "h" },
    paid: { lead: "The customer ", key: "paid the link", tail: ". This cycle is collected.",
            tone: "g" },
    expired: { lead: "The payment link ", key: "closed unpaid",
               tail: ". The cycle is forfeited and the mandate survives.", tone: "q" },
    cancelled: { lead: "The payment link was ", key: "cancelled",
                 tail: ". The cycle is forfeited and the mandate survives.", tone: "q" },
  }[m.backup_status];
  if (backup) return backup;
  if (!a) {
    return { lead: "Nothing has been ", key: "attempted", tail: " yet.", tone: "" };
  }
  if (a.conflicted) {
    return { lead: "Two ", key: "contradicting outcomes",
             tail: " arrived for one payment.", tone: "q" };
  }
  const by = {
    SUCCEEDED: { lead: "The debit was ", key: "collected", tail: ".", tone: "g" },
    FAILED: { lead: "The debit was ", key: "declined", tail: ".", tone: "q" },
    UNKNOWN: { lead: "The outcome is ", key: "unknown", tail: ".", tone: "h" },
    SUBMITTING: { lead: "A debit ", key: "may be at the provider", tail: ".", tone: "h" },
    SUBMITTED: { lead: "The debit is ", key: "submitted", tail: ", awaiting an answer.",
                 tone: "h" },
    ORDER_CREATED: { lead: "A debit is ", key: "scheduled", tail: ".", tone: "" },
    NOTIFIED: { lead: "The customer has been ", key: "notified", tail: ".", tone: "" },
    NOTIFICATION_FAILED: { lead: "The pre-debit notice ", key: "failed",
                           tail: ", so this attempt cannot be charged.", tone: "q" },
    INTENT: { lead: "An intent is ", key: "recorded", tail: ", nothing has been sent.",
              tone: "" },
  }[a.state];
  return by || { lead: "", key: a.state, tail: "", tone: "" };
}

function renderHead(m, a, d) {
  const ident = $("ident");
  ident.replaceChildren();
  if (!m) {
    ident.textContent = "Select a mandate";
  } else {
    // NOT the attempt count. `attempts_used` is what the scheduler was told
    // when it decided -- counted before this attempt existed -- so printing
    // it here would say "0 of 4 attempts" beside a debit that has since been
    // spent. It belongs next to the scheduler, and it is labelled there.
    const bits = [m.uid, m.customer_name, PAISE(m.charge_amount_paise),
                  `cycle ${m.cycle}`];
    ident.append(el("b", null, bits[0]));
    ident.append(document.createTextNode(" · " + bits.slice(1).join(" · ")));
  }

  const ans = answerFor(m, a);
  const h1 = $("answer");
  h1.replaceChildren();
  if (ans.lead) h1.append(document.createTextNode(ans.lead));
  h1.append(el("span", ans.tone || null, ans.key));
  if (ans.tail) h1.append(document.createTextNode(ans.tail));

  const because = $("because");
  if (!m) {
    because.textContent = "A UPI AutoPay mandate is debited once a cycle. When " +
      "the account is short the debit is declined — and this decides when to " +
      "ask again, what to do about it, and whether that is allowed.";
  } else if (m && m.backup_status) {
    // THE LADDER OUTRANKS THE LAST ATTEMPT HERE TOO. What is happening to this
    // mandate is the link, not the debit that preceded it.
    because.textContent = SENTENCE(BACKUP_WHY[m.backup_status]
      || "A backup checkout stands in for the fourth mandate debit") + ".";
  } else if (a && (a.state === "FAILED" || a.state === "SUCCEEDED")) {
    // The decision's `reason` describes the SUBMISSION -- "awaiting the
    // authoritative outcome" -- and the outcome has since arrived. Repeating
    // it here would contradict the sentence above it.
    because.textContent = a.state === "FAILED"
      ? `Razorpay answered ${outcomeWords(a) || "a decline"}` +
        `, at ${HOUR(a.target_t)}. The attempt is resolved and will not be ` +
        "retried under this notice."
      : `Collected at ${HOUR(a.target_t)}. Cycle ${a.cycle} is complete.`;
  } else if (d && d.reason) {
    because.textContent = SENTENCE(d.reason) + ".";
  } else if (a) {
    // No decision record for this attempt -- the ticks that produced it ran
    // before a restart. Report the durable facts and say so.
    because.textContent =
      (a.target_t ? `The debit is set for ${HOUR(a.target_t)}. ` : "") +
      "The reasoning behind it is not in this session's decision log.";
  } else {
    because.textContent = "No decision has run against this order in this " +
      "session.";
  }

  const blocked = $("blocked");
  blocked.hidden = !(m && m.blocked_because);
  if (m && m.blocked_because) blocked.textContent = SENTENCE(m.blocked_because) + ".";
}

/* ============================================================ latest tick ==
   The newest tick, when it is NOT the tick the spine below describes. Its own
   block, its own hour, and outside the spine's numbering -- see
   `currentDecision` for what folding it in produced.
   ========================================================================= */

function renderLatest(d) {
  const wrap = $("latest");
  wrap.hidden = !d;
  if (!d) return;
  $("latest-h").textContent =
    `The latest tick · ${HOUR(d.now_t)} · later than the attempt below`;
  $("latest-d").textContent = SENTENCE(d.reason || "Nothing to do") + ".";
}

/* ============================================================= the spine ==
   Razorpay event → belief → scheduler WHEN → diagnosis WHAT →
   Stage 0 WHETHER → provider → webhook.

   Rows 1, 2, 6 and 7 are reconstructed from DURABLE state -- the attempt, its
   transitions and the webhook rows -- so they survive a restart. Rows 3, 4
   and 5 are the tick's own reasoning, which `service.decisions` keeps in
   memory only; with no decision in this session they say so rather than
   inventing one.
   ========================================================================= */

function row(who, verb, opts) {
  const li = document.createElement("li");
  li.dataset.on = opts.on || "idle";

  const w = el("div", "who");
  w.append(el("b", null, who));
  const v = el("span", "verb" + (opts.own ? " own" : ""), verb);
  w.append(v);
  li.append(w);
  li.append(el("div", "bar"));

  const said = el("div", "said");
  said.append(el("div", "v", opts.v));
  if (opts.d) said.append(el("div", "d", opts.d));
  if (opts.quote) said.append(el("div", "quote", "“" + opts.quote + "”"));
  if (opts.ev && opts.ev.length) {
    const ev = el("div", "ev");
    opts.ev.forEach(([k, val]) => {
      const s = el("span");
      s.append(document.createTextNode(k + " "));
      s.append(el("b", null, val));
      ev.append(s);
    });
    said.append(ev);
  }
  if (opts.rules && opts.rules.length) {
    const rules = el("div", "rules");
    opts.rules.forEach((c) => {
      const s = el("span");
      s.dataset.v = c.verdict;
      s.append(document.createTextNode(c.rule + " "));
      s.append(el("b", null, c.verdict.toLowerCase()));
      rules.append(s);
    });
    said.append(rules);
  }
  li.append(said);
  return li;
}

function renderSpine(m, a, d, events) {
  const list = $("spine");
  list.replaceChildren();
  const note = $("spine-note");
  const rows = [];

  /* --- 1. what Razorpay said first ------------------------------------- */
  const trigger = events[0];
  if (trigger) {
    rows.push(row("Razorpay", "the event", {
      on: /failed|rejected|cancelled/.test(trigger.event_type) ? "bad" : "done",
      v: trigger.event_type,
      d: SENTENCE(trigger.result || "recorded") + ".",
      // No time. The only clock this row has is `received_at`, a real second,
      // and the hour that matters -- when the debit ran -- is on the attempt.
      ev: a && a.target_t ? [["debit", HOUR(a.target_t)]] : [],
    }));
  } else {
    rows.push(row("Razorpay", "the event", {
      on: "idle", v: "Nothing delivered yet",
      d: "No webhook has arrived for this mandate.",
    }));
  }

  /* --- 2. the belief. THE EVIDENCE IS REPORTED, NOT THE POSTERIOR.
         No balance, salary, payday or distribution crosses the HTTP
         boundary, and this row does not pretend otherwise. --------------- */
  const resolved = a && (a.state === "SUCCEEDED" || a.state === "FAILED");
  // THE FILTER IS IN MEMORY AND THE ATTEMPT ROW IS ON DISK. A resolved attempt
  // is not evidence that the belief read it: a service restarted on an
  // existing database serves outcomes it has never folded in. The service
  // reports which ones it did, and this row believes that rather than `state`.
  const folded = resolved && a.folded_in_session === true;
  const measurement = resolved
    ? `A ${a.state === "SUCCEEDED" ? "collected" : "declined"} ${
      PAISE(a.amount_paise)} debit is a censored measurement of the balance ` +
      "at that hour."
    : "";
  rows.push(row("Belief", "updated", {
    on: folded ? "done" : "idle",
    v: folded
      ? "Folded in a resolved debit"
      : resolved ? "Not recorded in this session" : "Nothing resolved to fold in",
    d: folded
      ? measurement + " Only a terminal outcome reaches the filter."
      : resolved
        ? measurement + " This attempt resolved before the service was " +
          "restarted, so the filter in this process has not read it."
        : "A submission is not an outcome, so nothing has been learned from it.",
  }));

  /* --- 3. the scheduler. IT OWNS TIMING AND NOTHING ELSE DOES. ---------- */
  if (d && d.target_t) {
    rows.push(row("Scheduler", "when", {
      on: "done", own: true, v: `Debit at ${HOUR(d.target_t)}`,
      d: d.notify_t
        ? `Pre-debit notice at ${HOUR(d.notify_t)} — the 24 hours' notice ` +
          "NPCI requires before an AutoPay debit."
        : "",
      ev: schedulerEvidence(d),
    }));
  } else if (d && /^timing:/.test(d.reason || "")) {
    rows.push(row("Scheduler", "when", {
      on: "hold", own: true, v: "No attempt proposed",
      d: "Waiting scores higher than asking now, so no debit was placed and " +
        "no attempt was spent.",
      ev: schedulerEvidence(d),
    }));
  } else if (d) {
    rows.push(row("Scheduler", "when", {
      on: "idle", own: true, v: "Not consulted",
      d: SENTENCE(d.reason || "The tick stopped before the scheduler ran") + ".",
      ev: schedulerEvidence(d),
    }));
  } else {
    rows.push(row("Scheduler", "when", {
      on: "idle", own: true, v: "Not recorded in this session",
      d: "The scheduler chose an hour" +
        (a && a.target_t
          ? ` — ${HOUR(a.target_t)}, which the attempt below still carries. ` +
            "The probabilities behind it are held in memory and do not "
          : ", and the evidence behind it is held in memory and does not ") +
        "survive a restart.",
    }));
  }

  /* --- 4. the diagnosis. IT NAMES A CAUSE AND PICKS AN INTERVENTION.
         It has no field for a time, so it cannot appear in row 3. -------- */
  if (d && d.intervention) {
    rows.push(row("Diagnosis", "what", {
      on: d.intervention === "RETRY" ? "done" : "hold", own: true,
      v: INTERVENTION[d.intervention] || d.intervention,
      d: d.root_cause
        ? `Root cause: ${CAUSE[d.root_cause] || d.root_cause.toLowerCase()}.`
        : "",
      quote: d.rationale || "",
      // A NUDGE ACCOMPANIES THE DEBIT, IT DOES NOT REPLACE IT. Saying only
      // "prompt the customer" beside a scheduled attempt reads as the
      // diagnosis layer having cancelled it -- which is what this service used
      // to do, and is the one thing that layer must never decide.
      ev: [["source", d.diagnosis_source === "llm"
        ? "language model" : "deterministic rules"],
      ...(d.intervention === "NUDGE"
        ? [["and the debit", "still runs at the scheduler's hour"]] : [])],
    }));
  } else if (d) {
    rows.push(row("Diagnosis", "what", {
      on: "idle", own: true, v: "Not consulted",
      d: "The diagnosis layer runs only once the scheduler has proposed an " +
        "hour. It cannot choose one.",
    }));
  } else {
    // NOT "not consulted". A diagnosis certainly ran for an attempt that
    // exists; its record did not survive. Asserting the first would be a
    // claim about the system, and it would be false.
    rows.push(row("Diagnosis", "what", {
      on: "idle", own: true, v: "Not recorded in this session",
      d: "An attempt exists, so a cause was named and an intervention " +
        "chosen. Which, is not on disk.",
    }));
  }

  /* --- 5. Stage 0. FIVE RULES, ALL EVALUATED. -------------------------- */
  const checks = (d && d.gate_checks) || [];
  if (d && d.gate_verdict === "ALLOWED") {
    rows.push(row("Stage 0", "whether", {
      on: "done", own: true, v: "Allowed",
      d: "Every rule permitted, so the action reached the executor.",
      rules: checks,
    }));
  } else if (d && d.gate_verdict === "REFUSED") {
    rows.push(row("Stage 0", "whether", {
      on: "bad", own: true,
      v: `Refused on the ${d.refused_rule || "constraint"} rule`,
      d: RULE_MEANS[d.refused_rule]
        ? SENTENCE(RULE_MEANS[d.refused_rule]) +
          ". The executor was never called, so no request left this process."
        : "The executor was never called, so no request left this process.",
      rules: checks,
    }));
  } else if (!d && a && (a.payment_id || a.submitted_at)) {
    rows.push(row("Stage 0", "whether", {
      on: "idle", own: true, v: "Not recorded in this session",
      d: "This debit reached Razorpay, so all five rules permitted it. The " +
        "five verdicts are in the audit log, not on this page.",
    }));
  } else {
    // `gate_checks` is empty on a scheduling tick: the gate adjudicates the
    // notification there and the five dispatch rules at the debit.
    rows.push(row("Stage 0", "whether", {
      on: "idle", own: true, v: "Not yet adjudicated",
      d: "The five rules are evaluated when a debit is submitted. Nothing " +
        "has been submitted for this attempt.",
      rules: checks,
    }));
  }

  /* --- 6. the provider ------------------------------------------------- */
  const prov = (d && d.provider) || {};
  if (d && d.acted) {
    rows.push(row("Provider", "execution", {
      on: "done", v: "Debit submitted to Razorpay",
      d: "A submission is an acknowledgement, not an outcome.",
      ev: [
        prov.http_status ? ["http", String(prov.http_status)] : null,
        prov.payment_id ? ["payment", prov.payment_id] : null,
        prov.order_id ? ["order", prov.order_id] : null,
      ].filter(Boolean),
    }));
  } else if (a && (a.payment_id || a.submitted_at)) {
    // A payment id is Razorpay's own acknowledgement that it took the debit.
    // Reading the order id alone here reported a completed debit as a
    // scheduled one after a restart.
    rows.push(row("Provider", "execution", {
      on: "done", v: "Debit submitted to Razorpay",
      d: "A submission is an acknowledgement, not an outcome.",
      ev: [
        a.payment_id ? ["payment", a.payment_id] : null,
        a.order_id ? ["order", a.order_id] : null,
        a.target_t ? ["charged", HOUR(a.target_t)] : null,
      ].filter(Boolean),
    }));
  } else if (a && a.order_id) {
    rows.push(row("Provider", "execution", {
      on: "done", v: "Pre-debit order created",
      d: "Razorpay has been told to expect this debit and to notify the " +
        "customer. No payment has been submitted against it.",
      ev: [["order", a.order_id]],
    }));
  } else {
    rows.push(row("Provider", "execution", {
      on: "idle", v: "Nothing submitted",
      d: "No order, no debit, and no NPCI attempt spent.",
    }));
  }

  /* --- 7. the authoritative answer ------------------------------------- */
  const aid = (d && d.attempt_id) || (a && a.id) || "";
  const answer = events.find((e) => e.attempt_id && e.attempt_id === aid &&
    /^payment\./.test(e.event_type));
  if (answer) {
    rows.push(row("Webhook", "the answer", {
      on: /failed/.test(answer.event_type) ? "bad" : "done",
      v: answer.event_type,
      d: SENTENCE(answer.result || "") + ".",
      // The raw arrival second is under "What arrived", where it is labelled
      // as the wall clock it is.
      ev: [["for", HOUR(a && a.target_t ? a.target_t : 0)],
           ["state", answer.processed_at ? "applied" : "queued"]],
    }));
  } else {
    rows.push(row("Webhook", "the answer", {
      on: "idle", v: "No answer yet",
      d: "Razorpay reports the outcome out of band. Until it does, the " +
        "attempt stays unresolved and is never retried.",
    }));
  }

  rows.forEach((li, i) => {
    li.style.animationDelay = `${i * 38}ms`;
    list.append(li);
  });

  note.hidden = !(d && d.gate_checks && d.gate_checks.length === 0 &&
    d.gate_verdict);
  if (!note.hidden) {
    note.textContent = "The five verdicts for this action were overwritten by " +
      "a later tick on another order, so they are not shown rather than " +
      "shown wrongly.";
  }
}

function schedulerEvidence(d) {
  const ev = [];
  if (d.p_now || d.p_later) {
    ev.push(["p(now)", NUM(d.p_now)]);
    ev.push(["p(later)", NUM(d.p_later)]);
    ev.push(["index", (d.index_score >= 0 ? "+" : "") + NUM(d.index_score)]);
  }
  // Empty when no diagnosis ran, which is exactly when there was no CaseView
  // to read it off.
  if (d.uncertainty_band) ev.push(["timing confidence", d.uncertainty_band]);
  // READ, never derived. This is `_attempts_this_cycle` as the tick saw it,
  // and recomputing it from `attempts[]` in the browser would put a second
  // copy of an NPCI rule here. "when it decided" is not padding: the count is
  // taken before this attempt is created.
  if (d.attempts_cap) {
    ev.push(["NPCI attempts when it decided",
             `${d.attempts_used} of ${d.attempts_cap}`]);
  }
  return ev;
}

/* ============================================================== events === */

function eventTone(e) {
  if (!e.processed_at) return "stale";
  if (/failed|rejected/.test(e.event_type)) return "bad";
  if (/ignored|no handler|no local|stale/.test(e.result || "")) return "stale";
  if (/captured|confirmed|delivered|SUCCEEDED/.test(e.event_type + e.result)) {
    return "good";
  }
  return "";
}

/* What one attempt did, in one sentence, from the durable row. */
function attemptOutcome(a) {
  const words = outcomeWords(a);
  const by = {
    SUCCEEDED: `Collected ${PAISE(a.amount_paise)}`,
    FAILED: `Declined${words ? " — " + words : ""}`,
    UNKNOWN: "Outcome unknown — never retried, resolved by reconciliation",
    SUBMITTED: "Submitted, awaiting the authoritative outcome",
    SUBMITTING: "A debit may be at the provider",
    NOTIFIED: "Notified. The debit has not been submitted",
    ORDER_CREATED: "Pre-debit order created. The customer has not been notified",
    NOTIFICATION_FAILED: "The pre-debit notice failed, so this attempt was "
      + "never charged and spent no NPCI presentation",
    INTENT: "Recorded, and nothing was sent",
  };
  return by[a.state] || a.state;
}

function attemptTone(a) {
  if (a.state === "SUCCEEDED") return "good";
  if (a.state === "FAILED" || a.state === "NOTIFICATION_FAILED") return "bad";
  if (a.state === "UNKNOWN") return "stale";
  return "";
}

/* ONE BLOCK PER ATTEMPT, IN THE ORDER THE ATTEMPTS HAPPENED.

   Every event row already carries `attempt_id`; the renderer used to drop it
   and print a flat list ordered by `received_at`. That is the WALL clock, so
   two attempts nineteen simulated days apart appeared twelve real seconds
   apart, six rows across two cycles looked unrelated, and the reader had no
   way to tell which decline belonged to which debit.

   The heading is the attempt and its simulated hours. The deliveries that
   carried its outcome are inside it, behind a disclosure: they are Razorpay's
   evidence for the line above, not six separate things that happened. */
function renderEvents(m, events) {
  const list = $("event-list");
  list.replaceChildren();
  // OLDEST FIRST, BY THE SIMULATED HOUR THE DEBIT RAN AT. Reversing the served
  // order is not enough: the store orders by `created_at`, a wall-clock second,
  // and a seeded month is driven in far less than a second -- so three attempts
  // days apart came back in whatever order SQLite returned them and were
  // numbered 1, 2, 3 against days 25, 28, 27. `target_t` is the hour the block
  // is headed with, so it is the hour the blocks are ordered by.
  const attempts = ((m && m.attempts) || []).slice()
    .sort((x, y) => (x.target_t || 0) - (y.target_t || 0)
      || (x.created_at || 0) - (y.created_at || 0));
  const byAttempt = new Map();
  const loose = [];
  events.forEach((e) => {
    if (e.attempt_id && attempts.some((a) => a.id === e.attempt_id)) {
      if (!byAttempt.has(e.attempt_id)) byAttempt.set(e.attempt_id, []);
      byAttempt.get(e.attempt_id).push(e);
    } else {
      loose.push(e);
    }
  });

  attempts.forEach((a, i) => {
    const mine = byAttempt.get(a.id) || [];
    const li = document.createElement("li");
    li.dataset.tone = attemptTone(a);

    const head = el("div", "att-h");
    head.append(el("b", null, `Attempt ${i + 1}`));
    head.append(el("span", null, `· Cycle ${a.cycle} ·`));
    head.append(el("span", null, HOUR(a.target_t)));
    li.append(head);

    li.append(el("div", "att-line",
      `Notified ${HOUR(a.notify_t)} · charged ${HOUR(a.target_t)}`));
    li.append(el("div", "att-out", attemptOutcome(a)));

    const det = document.createElement("details");
    det.className = "att-raw";
    const verified = mine.length
      ? `${mine.length} webhook${mine.length === 1 ? "" : "s"} · signature verified`
      : "no webhook delivered for this attempt";
    det.append(el("summary", null, verified));
    if (mine.length) det.append(rawList(mine));
    li.append(det);
    list.append(li);
  });

  // Deliveries this mandate received that belong to no attempt: the token
  // events from registration, and anything Razorpay sent that ingestion could
  // not join. Kept visible rather than dropped.
  if (loose.length) {
    const li = document.createElement("li");
    li.append(el("div", "att-h", "Not tied to an attempt"));
    li.append(el("div", "att-line",
      "Registration and token events for this mandate."));
    const det = document.createElement("details");
    det.className = "att-raw";
    det.append(el("summary", null,
      `${loose.length} delivery${loose.length === 1 ? "" : "-ies"} · signature verified`));
    det.append(rawList(loose));
    li.append(det);
    list.append(li);
  }

  $("events-none").hidden = attempts.length > 0 || loose.length > 0;
  renderRefused();
}

function rawList(rows) {
  const ol = el("ol", "raw");
  rows.forEach((e) => {
    const li = document.createElement("li");
    li.dataset.tone = eventTone(e);
    li.append(el("span", "n", e.event_type || "unnamed event"));
    // THE ONE PLACE THE WALL CLOCK SURVIVES, and it is labelled. `received_at`
    // is a real second: it settles a signature dispute, and it is not the
    // clock the scheduler reasons in.
    li.append(el("span", "t", `received ${RAW(e.received_at)}`));
    li.append(el("span", "r",
      SENTENCE(e.result || (e.processed_at ? "recorded" : "queued for processing"))));
    ol.append(li);
  });
  return ol;
}

/* Deliveries that FAILED VERIFICATION. Their own section, never under "What
   arrived": a forged delivery is refused the deduplication key on purpose and
   nothing about it arrived. */
function renderRefused() {
  const rejected = $("rejected-list");
  rejected.replaceChildren();
  const rows = state.rejected || [];
  $("refused-wrap").hidden = rows.length === 0;
  if (!rows.length) return;
  $("refused-lede").textContent =
    `${rows.length} deliver${rows.length === 1 ? "y" : "ies"} failed signature ` +
    "verification and were not recorded. Nothing here reached the state machine.";
  rows.forEach((r) => {
    const li = document.createElement("li");
    li.append(el("span", "n", r.event_type || "unnamed delivery"));
    li.append(el("span", "t", `received ${RAW(r.at)}`));
    li.append(el("span", "r",
      `${SENTENCE(r.reason || "refused")}. Claimed ${r.claimed_id || "no event id"}.`));
    rejected.append(li);
  });
}

/* =============================================================== facts === */

function fact(dl, term, value, mono, bad) {
  if (value === "" || value === null || value === undefined) return;
  const wrap = document.createElement("div");
  wrap.append(el("dt", null, term));
  const dd = el("dd", (mono ? "mono" : "") + (bad ? " bad" : ""), String(value));
  wrap.append(dd);
  dl.append(wrap);
}

function renderFacts(m) {
  $("facts-wrap").hidden = !m;
  $("danger-zone").hidden = !m;
  if (!m) return;

  const dl = $("facts");
  dl.replaceChildren();
  fact(dl, "Customer", m.customer_name);
  fact(dl, "Mandate state", m.state);
  fact(dl, "Debit amount", PAISE(m.charge_amount_paise));
  fact(dl, "Authorised ceiling", PAISE(m.max_amount_paise));
  fact(dl, "Billing cycle", `${m.cycle} · every ${m.cycle_days} days`);
  // THE LADDER, and only when it has moved. A row reading "0 reminders" on
  // every healthy mandate is a row nobody reads.
  if (m.reminders_sent) {
    fact(dl, "Funding reminders", `${m.reminders_sent} sent this cycle`);
  }
  if (m.backup_status) {
    fact(dl, "Backup checkout", BACKUP[m.backup_status] || m.backup_status,
      false, m.backup_status === "expired" || m.backup_status === "cancelled");
  }

  // CUT FROM THE DEFAULT VIEW: `Frequency`, `Expires` and `Chargeable`.
  // Frequency is `monthly` on every mandate here. Expiry is ten years out.
  // And `Chargeable` restated `Mandate state` in one word while the sentence
  // at the top of the page already says what the mandate can do and why --
  // three rows that a reader has to scan and none that changes a decision.
  // `chargeable` is still READ, by `renderActions`, to decide whether the
  // control is live.

  // `est_salary` and `est_payday` are in this payload and are NOT rendered.
  // They are an operator's stated guess at a customer's pay cycle, recorded
  // so a decision made on them can be questioned -- not a measurement, and
  // not a fact about the customer. Putting them on the page invites them to
  // be read as one.

  const pv = $("facts-provider");
  pv.replaceChildren();
  fact(pv, "Provider token status", m.token_status);
  fact(pv, "Mandate token", m.rzp_token_id, true);
  fact(pv, "Customer record", m.rzp_customer_id, true);
  fact(pv, "Authorisation order", m.registration_order_id, true);
  fact(pv, "Authorisation payment", m.registration_payment_id, true);
  fact(pv, "Backup payment link", m.backup_vendor_id, true);
  fact(pv, "Cycle opened", HOUR(m.cycle_start_t), true);

  const hist = $("history");
  hist.replaceChildren();
  (m.transitions || []).slice().reverse().forEach((t) => {
    const li = document.createElement("li");
    li.dataset.v = t.verdict;
    li.append(el("span", "s", `${t.from_state || "—"} → ${t.to_state}`));
    li.append(el("span", "d", t.detail || t.source));
    li.append(el("span", "w", RAW(t.at)));
    hist.append(li);
  });
}

/* ============================================================== toasts === */

function toast(message, kind) {
  const node = el("div", "toast", message);
  node.dataset.kind = kind || "info";
  $("toasts").append(node);
  setTimeout(() => node.remove(), 5600);
}

async function busy(button, fn) {
  button.classList.add("is-busy");
  button.disabled = true;
  try {
    return await fn();
  } catch (err) {
    toast(err.message, "bad");
    return null;
  } finally {
    button.classList.remove("is-busy");
    button.disabled = false;
  }
}

/* =============================================================== loads === */

/* THE DECISION BEHIND THE PAYMENT ON SCREEN, not merely the newest one.

   The two differ, and the difference matters. After a funds decline the rule
   engine answers NUDGE, which spends no attempt and changes nothing, so the
   next tick produces an identical NUDGE -- and the ten decisions `/api/state`
   serves fill up with them. Taking the newest would make the spine describe a
   non-event while the headline describes the declined debit above it.

   So the join is on `attempt_id`: the decision that produced the attempt being
   displayed. That is an equality test between two fields the API already
   sends, not a rule about which tick matters. With no attempt yet -- a mandate
   that has only ever waited -- the newest decision IS the story, and it is
   used. */
/* TWO RECORDS, TWO MOMENTS, AND THEY ARE NEVER SPLICED.

   `currentDecision` returns `{ own, latest }`.

   `own` is the decision the spine is drawn from: the tick, or the two ticks,
   that produced the attempt on screen. Rows 3 to 5 of the spine are that
   tick's reasoning and rows 6 and 7 are that attempt's outcome, so both halves
   have to describe one moment or the chain is a fiction.

   `latest` is the newest tick for this mandate WHEN IT DESCRIBES SOMETHING
   ELSE -- a wait, days after the attempt below resolved. It carries no
   `attempt_id`, because a tick that spends nothing creates nothing to carry
   one.

   Folding `latest` into the spine is what produced the worst frame on this
   page: rows 3 to 5 read "No attempt proposed / Not consulted / Not yet
   adjudicated" from today's wait, and rows 6 and 7 read "Debit submitted to
   Razorpay / payment.failed" from an attempt weeks older. Read top to bottom
   that says the scheduler proposed nothing, Stage 0 never adjudicated, and a
   debit went out anyway -- a picture of the guardrail being bypassed, drawn
   from two records that were each correct. So it gets its own block, above the
   spine and labelled with its own hour.

   With no attempt yet -- a mandate that has only ever waited -- the newest
   decision IS the story and there is no second moment to keep apart. */
function currentDecision(attempt) {
  const mine = state.decisions.filter((d) => d.mandate_id === state.selected);
  if (!attempt) return { own: mine[0] || null, latest: null };
  const newest = mine[0];
  const describesAnother = Boolean(
    newest && !newest.attempt_id && attempt.id &&
    (attempt.state === "SUCCEEDED" || attempt.state === "FAILED" ||
     attempt.state === "NOTIFICATION_FAILED"));
  return { own: ownDecision(mine, attempt),
           latest: describesAnother ? newest : null };
}

function ownDecision(mine, attempt) {
  if (attempt) {
    // ONE ATTEMPT, TWO TICKS. NPCI wants 24 hours' notice, so scheduling and
    // charging are separate calls: the first carries the hour the scheduler
    // chose, the probabilities behind it and the diagnosis; the second
    // carries Stage 0's verdicts and what the provider said. Both tag
    // themselves with the same `attempt_id`.
    //
    // Showing either alone drops half the chain, so they are folded oldest to
    // newest, a later value winning only where it has one. That is a merge of
    // two records of one attempt on a key the API already supplies -- not a
    // judgement about which tick counts.
    const owned = mine.filter((d) => d.attempt_id === attempt.id);
    if (owned.length) {
      const merged = {};
      owned.slice().reverse().forEach((d) => {
        Object.keys(d).forEach((k) => {
          const v = d[k];
          const empty = v === "" || v === 0 || v === false || v === null ||
            v === undefined ||
            (Array.isArray(v) && !v.length) ||
            (v && typeof v === "object" && !Array.isArray(v) &&
             !Object.keys(v).length);
          if (!empty || !(k in merged)) merged[k] = v;
        });
      });
      return merged;
    }
  }
  // NO DECISION IN THE SERVED WINDOW DESCRIBES THIS ATTEMPT. Rows 3 to 5 then
  // say "not recorded in this session", which is what is true: `decisions` is
  // in memory and ten deep. Returning the newest one instead is the splice
  // this function exists to refuse.
  return null;
}

function paint() {
  renderEnv();
  renderOrders();
  const m = state.detail;
  // `attempts_for` is ordered newest first, so [0] is the live attempt.
  const a = m && m.attempts && m.attempts.length ? m.attempts[0] : null;
  const { own, latest } = currentDecision(a);
  const events = state.events.filter(
    (e) => !state.selected || e.mandate_id === state.selected);
  renderHead(m, a, own);
  renderLatest(latest);
  renderSpine(m, a, own, events);
  renderEvents(m, events);
  renderFacts(m);
  renderActions(m, a);
}

/* THE MANDATE TO OPEN ON, when the operator has not chosen one.
   The list order is insertion order, so the first row is whichever mandate
   was seeded first -- usually one that collected on its first attempt and has
   nothing left to explain. A mandate standing on the escalation ladder, or
   one waiting on an outcome, is the one an operator opened this page for. */
function mostInteresting(mandates) {
  return mandates.find((m) => m.backup_status)
    || mandates.find((m) => m.unresolved_attempts > 0)
    || mandates[0];
}

async function refresh() {
  try {
    const snap = await api("/api/state");
    state.config = snap.config;
    state.health = snap.health;
    state.counts = snap.counts;
    state.providerLost = snap.provider_lost;
    state.mandates = snap.mandates || [];
    state.decisions = snap.decisions || [];
    state.events = snap.recent_events || [];
    state.rejected = snap.rejected_events || [];
    if (!state.selected && state.mandates.length) {
      state.selected = mostInteresting(state.mandates).id;
    }
    if (state.selected && !state.mandates.some((m) => m.id === state.selected)) {
      state.selected = state.mandates.length ? state.mandates[0].id : null;
      state.detail = null;
    }
    if (state.selected) {
      state.detail = await api(
        `/api/mandates/${encodeURIComponent(state.selected)}`);
    } else {
      state.detail = null;
    }
    paint();
  } catch (err) {
    if (err.status === 401) {
      const supplied = await askForToken();
      if (supplied) { await refresh(); return; }
      toast("This console needs an operator token.", "bad");
    } else {
      toast(err.message, "bad");
    }
  }
}

function askForToken() {
  return new Promise((resolve) => {
    const dialog = $("token-dialog");
    const input = $("token-input");
    input.value = "";
    dialog.returnValue = "";
    dialog.showModal();
    dialog.addEventListener("close", function once() {
      dialog.removeEventListener("close", once);
      const value = input.value.trim();
      input.value = "";
      if (dialog.returnValue !== "yes" || !value) { resolve(false); return; }
      setOperatorToken(value);
      resolve(true);
    });
  });
}

async function select(id) {
  state.selected = id;
  await refresh();
}

/* ============================================================== actions ==
   WHICH CONTROLS EXIST IS DECIDED BY WHAT THE RAIL CAN DO. The offline-only
   ones are REMOVED in live mode rather than disabled: the service refuses
   both of them there, and a greyed-out button implies a permission that could
   be granted. It cannot.
   ======================================================================== */

function offline() {
  return Boolean(state.config && state.config.mode === "offline");
}

/* True when running a tick could actually take a customer's money: the live
   rail, with debits authorised. Offline the mock rail moves nothing and a
   confirmation on every tick would train the operator to click through the
   one that matters. */
function debitCanMoveMoney() {
  return Boolean(state.config && state.config.mode === "live"
    && state.config.debit_allowed);
}

function renderActions(m, a) {
  $("acts").hidden = !m;
  // The scheduler reasons in days. Offline there is no customer and no money,
  // so the clock can move; live `advance_clock` refuses, because Stage 0's
  // peak and lead rules read that clock. It lives in the environment band
  // beside the clock it moves, so it is not gated on a mandate being selected.
  $("act-advance").hidden = !offline();
  // Reconcile asks the provider how debits it never answered for ended. With
  // nothing unresolved there is nothing to ask, so the control is not there.
  const open = (state.counts && state.counts.attempts_unresolved) || 0;
  const rec = $("act-reconcile");
  rec.hidden = open === 0;

  if (!m) return;

  // Authorisation is a human on a phone. Offline the mock stands in for one;
  // live there is no such call and the control does not exist.
  $("act-authorize").hidden = !(offline() && m.state === "PENDING");

  const decide = $("act-decide");
  const money = debitCanMoveMoney();
  decide.dataset.live = money ? "yes" : "no";
  decide.disabled = !m.chargeable;
  decide.textContent = money
    ? "Run the chain — a debit may be submitted"
    : "Ask the agent what to do now";
  decide.title = m.chargeable
    ? (money ? "Live rail with debits authorised."
      : (state.config && state.config.debit_reason) || "")
    : m.blocked_because;

  const reveal = $("reveal-toggle");
  const canReveal = Boolean(state.config && state.config.operator_auth_required);
  reveal.disabled = !canReveal;
  reveal.title = canReveal ? ""
    : "Full identifiers need a configured RECOVERY_OPERATOR_TOKEN, because a " +
      "service that cannot tell who is asking does not hand them out.";
  reveal.textContent = state.reveal ? "Hide full identifiers"
    : "Show full identifiers";
  reveal.setAttribute("aria-pressed", String(state.reveal));

  $("act-cancel").disabled = !m.rzp_token_id || m.state === "CANCELLED";
}

function confirmLiveDebit() {
  const d = state.detail || {};
  $("debit-env").textContent =
    `LIVE — ${(state.config || {}).key_prefix || "no key"}`;
  $("debit-mandate").textContent = `${d.uid || "?"} · ${d.id || state.selected}`;
  $("debit-customer").textContent = d.customer_name || "—";
  $("debit-amount").textContent =
    `${PAISE(d.charge_amount_paise)} — the mandate's own amount`;
  return new Promise((resolve) => {
    const dialog = $("debit-dialog");
    dialog.returnValue = "";
    dialog.showModal();
    dialog.addEventListener("close", function once() {
      dialog.removeEventListener("close", once);
      resolve(dialog.returnValue === "yes");
    });
  });
}

$("act-decide").addEventListener("click", (e) => busy(e.currentTarget, async () => {
  // NO BODY. The amount is the mandate's, the hour is the scheduler's, and
  // the legality is Stage 0's. There is nothing here for an operator -- or
  // anything else holding an HTTP client -- to supply.
  if (debitCanMoveMoney() && !(await confirmLiveDebit())) {
    toast("Nothing was submitted.", "info");
    return null;
  }
  const out = await api(
    `/api/mandates/${encodeURIComponent(state.selected)}/decide`,
    { method: "POST", body: {} });
  const d = out.decision || {};
  toast(d.acted ? "Debit submitted. Waiting for the authoritative outcome."
    : SENTENCE(d.reason || "Nothing to do."), d.acted ? "ok" : "info");
  await refresh();
}));

$("act-advance").addEventListener("click", (e) => busy(e.currentTarget, async () => {
  // NO `hours`. The service knows the next hour at which a tick would do
  // something different; naming a number here would put a second, worse copy
  // of the scheduler's own knowledge in the browser.
  const out = await api("/api/demo/advance", { method: "POST", body: {} });
  const acted = (out.decisions || []).filter((x) => x.acted).length;
  toast(acted
    ? `${HOUR(out.now_t)}. ${acted} debit${acted === 1 ? "" : "s"} submitted.`
    : `${HOUR(out.now_t)}. The scheduler is still waiting.`,
    acted ? "ok" : "info");
  await refresh();
}));

$("act-authorize").addEventListener("click", (e) => busy(e.currentTarget, async () => {
  await api(`/api/mandates/${encodeURIComponent(state.selected)}/mock-authorize`,
    { method: "POST", body: {} });
  toast("Mock rail: the customer approved the mandate. Token confirmed.", "ok");
  await refresh();
}));

$("act-reconcile").addEventListener("click", (e) => busy(e.currentTarget, async () => {
  const out = await api("/api/reconcile", { method: "POST", body: {} });
  const rows = out.reconciled || [];
  toast(rows.length
    ? rows.map((r) => `${r.attempt.slice(0, 8)}… ${r.result}`).join(" · ")
    : "Nothing was unresolved.", "ok");
  await refresh();
}));

$("reveal-toggle").addEventListener("click", (e) => {
  state.reveal = !state.reveal;
  e.currentTarget.setAttribute("aria-pressed", String(state.reveal));
  refresh();
});

$("act-cancel").addEventListener("click", async () => {
  const dialog = $("confirm-dialog");
  dialog.returnValue = "";
  dialog.showModal();
  dialog.addEventListener("close", async function once() {
    dialog.removeEventListener("close", once);
    if (dialog.returnValue !== "yes") return;
    try {
      await api(`/api/mandates/${encodeURIComponent(state.selected)}/cancel`,
        { method: "POST", body: {} });
      toast("Mandate cancelled at the provider.", "ok");
      await refresh();
    } catch (err) {
      toast(err.message, "bad");
    }
  });
});

/* ========================================================= registration == */

function openRegister() {
  const dialog = $("register-dialog");
  $("reg-error").hidden = true;
  $("reg-note").textContent = state.config && state.config.mode === "live"
    ? "This creates a real customer and a real mandate order at Razorpay. " +
      "The customer must then authorise it in their UPI app; serve that flow " +
      "with scripts/razorpay_autopay_register.py."
    : "This creates a customer and a mandate on the mock rail. No money moves.";
  dialog.showModal();
}

$("new-order").addEventListener("click", openRegister);
$("empty-new").addEventListener("click", openRegister);

$("register-form").addEventListener("submit", async (event) => {
  const dialog = $("register-dialog");
  if (event.submitter && event.submitter.value === "cancel") return;
  event.preventDefault();

  const form = event.currentTarget;
  const data = Object.fromEntries(new FormData(form).entries());
  const errorBox = $("reg-error");
  errorBox.hidden = true;

  await busy($("reg-submit"), async () => {
    try {
      const created = await api("/api/customers", {
        method: "POST",
        body: { name: data.name, email: data.email, contact: data.contact },
      });
      const mandate = await api("/api/mandates", {
        method: "POST",
        body: {
          customer_id: created.customer.id,
          charge_amount_paise: Number(data.charge_amount_paise),
          max_amount_paise: Number(data.max_amount_paise),
          est_salary: Number(data.est_salary),
          est_payday: Number(data.est_payday),
        },
      });
      dialog.close();
      toast("Registered. It stays PENDING until the customer authorises it.",
        "ok");
      await select(mandate.mandate.id);
    } catch (err) {
      errorBox.textContent = err.message;
      errorBox.hidden = false;
      throw err;
    }
  });
});

/* ================================================================= boot ==
   READ ONLY. `refresh` issues two GETs and nothing else; no decision runs, no
   attempt is created and no provider call is made by loading this page.
   ======================================================================== */

refresh();
