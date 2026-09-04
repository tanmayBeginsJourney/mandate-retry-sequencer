/* ===========================================================================
   Operator console behaviour.

   NO FRAMEWORK, and the reason is not minimalism for its own sake. The page is
   served under a content security policy that permits no third-party origin,
   the backend is a standard-library HTTP server with no build step, and the
   whole surface is six lists and a form. A bundler here would add a toolchain
   to maintain and would not make any of that easier to read.

   THE PAGE NEVER DECIDES ANYTHING. It renders what the service reports and
   posts one of four operator intents. In particular `runDecision` posts an
   EMPTY body: the amount comes from the mandate the customer authorised, the
   time comes from the belief filter, and the legality comes from Stage 0. A
   console that could name an amount would be a hole in every one of those.
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
  config: null,
  token: "",
  loading: true,
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

/* ------------------------------------------------------------- rendering */

const PAISE = (n) =>
  typeof n === "number" ? `₹${(n / 100).toLocaleString("en-IN", {
    minimumFractionDigits: 2, maximumFractionDigits: 2 })}` : "—";

const WHEN = (ts) => {
  if (!ts) return "—";
  const d = new Date(ts * 1000);
  // A mandate expiry is ten years out. Dropping the year there turns 2036
  // into what looks like next week.
  const farOff = Math.abs(d - Date.now()) > 300 * 86400 * 1000;
  return d.toLocaleString(undefined, Object.assign(
    { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" },
    farOff ? { year: "numeric" } : {}));
};

/* State -> the tone the CSS uses. Kept as one table so a new state shows up
   as "no tone" rather than as a colour somebody guessed. */
const TONE = {
  SUCCEEDED: "ok", ACTIVE: "ok",
  FAILED: "bad", REJECTED: "bad", CANCELLED: "bad",
  UNKNOWN: "warn", PAUSED: "warn", PENDING: "warn",
  INTENT: "muted", ORDER_CREATED: "info", NOTIFIED: "info",
  SUBMITTED: "info", AUTHORIZED: "info",
};

function badge(text, tone) {
  const el = document.createElement("span");
  el.className = "badge " + (tone || TONE[text] || "muted");
  el.textContent = text;
  return el;
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

/* ------------------------------------------------------------ status bar */

function renderConfig(cfg, counts, provider) {
  state.config = cfg;
  const live = cfg.mode === "live";

  $("live-dot").dataset.live = cfg.mode;
  const envChip = $("env-chip");
  envChip.dataset.state = cfg.mode;
  $("env-value").textContent = live
    ? `LIVE · ${cfg.key_prefix || "no key"}`
    : "OFFLINE · mock rail";

  // "PERMITTED" against the mock rail would be a claim about real money that
  // is not true. Offline gets its own word.
  const debitChip = $("debit-chip");
  debitChip.dataset.state = !live ? "offline"
    : cfg.debit_allowed ? "allowed" : "blocked";
  $("debit-value").textContent = !live ? "NONE — MOCK RAIL"
    : cfg.debit_allowed ? "PERMITTED" : "BLOCKED";
  const clock = state.health && state.health.clock_offset_h
    ? ` · demonstration clock +${state.health.clock_offset_h}h` : "";
  $("debit-reason").textContent = cfg.debit_reason +
    ` · ceiling ${PAISE(cfg.max_debit_paise)} per debit` + clock;

  $("stat-mandates").textContent = counts.mandates_active;
  $("stat-mandates-note").textContent =
    `of ${counts.mandates} registered`;
  $("stat-collected").textContent = PAISE(counts.recovered_paise);
  $("stat-collected").dataset.tone = counts.recovered_paise > 0 ? "ok" : "";
  $("stat-collected-note").textContent =
    `${counts.attempts_succeeded} of ${counts.attempts} attempts collected`;
  $("stat-unresolved").textContent = counts.attempts_unresolved;
  $("stat-unresolved").dataset.tone =
    counts.attempts_conflicted > 0 ? "bad"
      : counts.attempts_unresolved > 0 ? "warn" : "";
  // A lost response is why an attempt is unresolved, so it belongs here and
  // not on a tile of its own: the operator reading this number is the one who
  // needs to know the rail stopped answering.
  const lost = provider && provider.lost > 0
    ? ` · ${provider.lost} lost provider response${provider.lost === 1 ? "" : "s"}`
    : "";
  $("stat-unresolved-note").textContent = (counts.attempts_conflicted > 0
    ? `${counts.attempts_conflicted} contradicted — needs a human`
    : "awaiting an authoritative answer") + lost;
}

/* -------------------------------------------------------------- mandates */

function renderMandates(mandates) {
  state.mandates = mandates;
  const list = $("mandate-list");
  list.replaceChildren();
  $("mandate-empty").hidden = mandates.length > 0;

  mandates.forEach((m) => {
    const li = document.createElement("li");
    const btn = el("button", "mandate");
    btn.type = "button";
    btn.setAttribute("aria-current", String(m.id === state.selected));
    btn.append(el("span", "mandate-name", m.customer_name || m.id));
    btn.append(badge(m.state));
    btn.append(el("span", "mandate-meta",
      `${m.uid} · ${PAISE(m.charge_amount_paise)} · ${m.frequency}`));
    btn.addEventListener("click", () => select(m.id));
    li.append(btn);
    list.append(li);
  });
}

/* ---------------------------------------------------------------- detail */

const LIFECYCLE = ["INTENT", "ORDER_CREATED", "NOTIFIED", "SUBMITTED",
                   "AUTHORIZED", "SUCCEEDED"];

function renderLifecycle(attempt) {
  const list = $("lifecycle");
  list.replaceChildren();
  const reached = new Set();
  let failedAt = null;
  if (attempt) {
    (attempt.transitions || []).forEach((t) => {
      if (t.verdict === "APPLIED") reached.add(t.to_state);
    });
    reached.add(attempt.state);
    if (attempt.state === "FAILED") failedAt = "FAILED";
    if (attempt.state === "UNKNOWN") failedAt = "UNKNOWN";
  }
  LIFECYCLE.forEach((step) => {
    const li = el("li", null, step.toLowerCase().replace(/_/g, " "));
    if (!attempt) li.dataset.on = "idle";
    else if (step === attempt.state) li.dataset.on = "current";
    else if (reached.has(step)) li.dataset.on = "done";
    else li.dataset.on = "idle";
    list.append(li);
  });
  if (failedAt) {
    const li = el("li", null,
      failedAt === "FAILED" ? "failed" : "outcome unknown");
    li.dataset.on = "failed";
    list.append(li);
  }
}

function fact(dl, term, value, mono) {
  const wrap = document.createElement("div");
  wrap.append(el("dt", null, term));
  wrap.append(el("dd", mono ? "mono" : null, value === "" || value === null ||
    value === undefined ? "—" : String(value)));
  dl.append(wrap);
}

function renderDetail(detail) {
  state.detail = detail;
  $("detail-empty").hidden = true;
  $("detail-body").hidden = false;
  $("detail-actions").hidden = false;

  const dl = $("detail-facts");
  dl.replaceChildren();
  fact(dl, "State", detail.state);
  fact(dl, "Provider token status", detail.token_status);
  fact(dl, "Chargeable", detail.chargeable ? "yes"
    : `no — ${detail.blocked_because}`);
  fact(dl, "Debit amount", PAISE(detail.charge_amount_paise));
  fact(dl, "Authorised ceiling", PAISE(detail.max_amount_paise));
  fact(dl, "Frequency", detail.frequency);
  fact(dl, "Mandate token", detail.rzp_token_id, true);
  fact(dl, "Customer", detail.rzp_customer_id, true);
  fact(dl, "Registration order", detail.registration_order_id, true);
  fact(dl, "Cycle", `${detail.cycle} · ${detail.cycle_days} days`);
  fact(dl, "Expires", WHEN(detail.expire_at));

  $("act-cancel").disabled = !detail.rzp_token_id ||
    detail.state === "CANCELLED";
  // Authorisation is a human on a phone. Offline, the mock stands in for one;
  // live, there is no such call and the button does not exist.
  const offline = state.config && state.config.mode === "offline";
  $("act-authorize").hidden = !(offline && detail.state === "PENDING");
  // The scheduler reasons in days. Offline there is no customer and no money,
  // so the clock can move; live it cannot, and the button is not there.
  $("act-advance").hidden = !offline;
  $("act-decide").disabled = !detail.chargeable;

  const attempts = detail.attempts || [];
  const tbody = $("attempt-table").querySelector("tbody");
  tbody.replaceChildren();
  $("attempt-empty").hidden = attempts.length > 0;
  attempts.forEach((a) => {
    const tr = document.createElement("tr");
    const stateCell = document.createElement("td");
    stateCell.append(badge(a.state));
    if (a.conflicted) stateCell.append(badge("CONFLICT", "bad"));
    tr.append(stateCell);
    tr.append(el("td", "num", PAISE(a.amount_paise)));
    tr.append(el("td", "num", String(a.target_t)));
    tr.append(el("td", null, a.raw_reason || a.outcome_code || "—"));
    tr.append(el("td", "mono", a.order_id || "—"));
    tr.append(el("td", "mono", a.payment_id || "—"));
    tbody.append(tr);
  });

  renderLifecycle(attempts[0]);
}

/* ------------------------------------------------------------- decisions */

function renderDecisions(decisions) {
  state.decisions = decisions;
  const list = $("decision-list");
  list.replaceChildren();
  $("decision-empty").hidden = decisions.length > 0;

  decisions.forEach((d) => {
    const li = document.createElement("li");
    li.dataset.acted = String(!!d.acted);
    li.dataset.refused = String(d.gate_verdict === "REFUSED");

    const head = el("div", "t-head");
    head.append(el("span", "t-title",
      d.acted ? "Debit submitted"
        : d.gate_verdict === "REFUSED" ? "Refused by Stage 0"
          : "No action"));
    head.append(el("span", "t-time", WHEN(d.at)));
    li.append(head);
    li.append(el("p", "t-body", d.reason));

    const chain = el("div", "t-chain");
    if (d.target_t) chain.append(badge(`scheduler → hour ${d.target_t}`, "info"));
    if (d.intervention) chain.append(badge(`diagnosis → ${d.intervention}`));
    if (d.gate_verdict) {
      chain.append(badge(`Stage 0 → ${d.gate_verdict}` +
        (d.refused_rule ? ` (${d.refused_rule})` : ""),
        d.gate_verdict === "ALLOWED" ? "ok" : "bad"));
    }
    // A submission is not an outcome. Saying so plainly beats showing the
    // INDETERMINATE code, which looks like a decline to anyone reading fast.
    if (d.outcome_raw === "submitted_awaiting_outcome") {
      chain.append(badge("outcome → not yet known", "warn"));
    } else if (d.outcome_raw === "transport_lost") {
      chain.append(badge("outcome → response lost", "warn"));
    } else if (d.outcome_code) {
      chain.append(badge(`outcome → ${d.outcome_code}`,
        d.outcome_code === "OK" ? "ok" : "bad"));
    }
    if (chain.childElementCount) li.append(chain);

    if (d.p_now || d.p_later) {
      const nums = el("div", "t-nums");
      nums.append(el("span", null, `p(now) ${d.p_now.toFixed(3)}`));
      nums.append(el("span", null, `p(later) ${d.p_later.toFixed(3)}`));
      nums.append(el("span", null, `index ${d.index_score.toFixed(3)}`));
      li.append(nums);
    }
    list.append(li);
  });

  const latest = decisions.find((d) => d.intervention);
  renderDiagnosis(latest);
}

function renderDiagnosis(d) {
  const body = $("llm-body");
  body.replaceChildren();
  const chip = $("llm-source");
  if (!d) {
    chip.textContent = "—";
    chip.className = "badge muted";
    body.append(el("p", "empty", "No diagnosis has been produced yet."));
    return;
  }
  chip.textContent = d.diagnosis_source === "llm"
    ? "model" : "deterministic fallback";
  chip.className = "badge " + (d.diagnosis_source === "llm" ? "info" : "muted");

  const cards = [
    ["Root cause", d.root_cause, true],
    ["Intervention", d.intervention, true],
    ["Justification", d.rationale || "—", false],
  ];
  cards.forEach(([title, value, strong]) => {
    const card = el("div", "llm-card");
    card.append(el("h3", null, title));
    card.append(el("p", strong ? "strong" : null, value));
    body.append(card);
  });

  const card = el("div", "llm-card");
  card.append(el("h3", null, "What it did not choose"));
  card.append(el("p", null,
    `The debit hour (${d.target_t || "none proposed"}) came from the belief ` +
    `filter, and the amount from the mandate the customer authorised. ` +
    `Neither is expressible in a diagnosis.`));
  body.append(card);
}

/* -------------------------------------------------------------- webhooks */

function renderEvents(events) {
  state.events = events;
  const list = $("event-list");
  list.replaceChildren();
  $("event-empty").hidden = events.length > 0;

  events.forEach((e) => {
    const li = document.createElement("li");
    li.dataset.valid = String(!!e.signature_valid);
    li.dataset.changed = String(!!e.processed_at &&
      !/ignored|no handler|no local/.test(e.result || ""));

    const head = el("div", "t-head");
    head.append(el("span", "t-title", e.event_type || "unnamed event"));
    if (!e.signature_valid) head.append(badge("SIGNATURE REJECTED", "bad"));
    else if (!e.processed_at) head.append(badge("queued", "warn"));
    head.append(el("span", "t-time", WHEN(e.received_at)));
    li.append(head);
    if (e.result) li.append(el("p", "t-body", e.result));
    const nums = el("div", "t-nums");
    nums.append(el("span", null, e.event_id));
    if (e.processed_at) {
      nums.append(el("span", null, `processed ${WHEN(e.processed_at)}`));
    }
    li.append(nums);
    list.append(li);
  });
}

/* ---------------------------------------------------------------- toasts */

function toast(message, kind) {
  const node = el("div", "toast", message);
  node.dataset.kind = kind || "info";
  $("toasts").append(node);
  setTimeout(() => node.remove(), 5200);
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

/* ----------------------------------------------------------------- loads */

async function refresh() {
  try {
    const snap = await api("/api/state");
    state.health = snap.health;
    renderConfig(snap.config, snap.counts,
      { calls: snap.provider_calls, lost: snap.provider_lost });
    renderMandates(snap.mandates || []);
    renderDecisions(snap.decisions || []);
    renderEvents(snap.recent_events || []);
    if (state.selected) await loadDetail(state.selected);
  } catch (err) {
    if (err.status === 401) {
      const supplied = await askForToken();
      if (supplied) { await refresh(); return; }
      toast("This console needs an operator token.", "bad");
    } else {
      toast(err.message, "bad");
    }
  } finally {
    state.loading = false;
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

async function loadDetail(id) {
  try {
    const data = await api(`/api/mandates/${encodeURIComponent(id)}`);
    renderDetail(data);
  } catch (err) {
    toast(err.message, "bad");
  }
}

async function select(id) {
  state.selected = id;
  renderMandates(state.mandates);
  await loadDetail(id);
}

/* ---------------------------------------------------------------- actions */

$("refresh").addEventListener("click", (e) => busy(e.currentTarget, refresh));

$("reveal-toggle").addEventListener("click", (e) => {
  state.reveal = !state.reveal;
  e.currentTarget.setAttribute("aria-pressed", String(state.reveal));
  e.currentTarget.textContent = state.reveal ? "Hide full IDs" : "Show full IDs";
  refresh();
});

$("act-authorize").addEventListener("click", (e) => busy(e.currentTarget, async () => {
  await api(`/api/mandates/${encodeURIComponent(state.selected)}/mock-authorize`,
    { method: "POST", body: {} });
  toast("Mock rail: the customer approved the mandate. Token confirmed.", "ok");
  await refresh();
}));

/* True when running a tick could actually take a customer's money: the live
   rail, with debits authorised. Offline the mock rail moves nothing and a
   confirmation on every tick would train the operator to click through the
   one that matters. */
function debitCanMoveMoney() {
  return Boolean(state.config && state.config.mode === "live"
    && state.config.debit_allowed);
}

function confirmLiveDebit() {
  const d = state.detail || {};
  $("debit-env").textContent = `LIVE — ${(state.config || {}).key_prefix || "no key"}`;
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
    : d.reason, d.acted ? "ok" : "info");
  await refresh();
}));

$("act-advance").addEventListener("click", (e) => busy(e.currentTarget, async () => {
  const out = await api("/api/demo/advance", { method: "POST", body: { hours: 12 } });
  const acted = (out.decisions || []).filter((d) => d.acted).length;
  toast(acted
    ? `Clock at hour ${out.now_t}. ${acted} debit${acted === 1 ? "" : "s"} submitted.`
    : `Clock at hour ${out.now_t}. The scheduler is still waiting.`,
    acted ? "ok" : "info");
  await refresh();
}));

$("act-reconcile").addEventListener("click", (e) => busy(e.currentTarget, async () => {
  const out = await api("/api/reconcile", { method: "POST", body: {} });
  const n = (out.reconciled || []).length;
  toast(n ? `Reconciled ${n} attempt${n === 1 ? "" : "s"} against the provider.`
    : "Nothing was unresolved.", "ok");
  await refresh();
}));

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

/* ---------------------------------------------------------- registration */

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

$("new-mandate").addEventListener("click", openRegister);
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
      toast("Mandate created. It stays PENDING until the customer authorises it.",
        "ok");
      await refresh();
      await select(mandate.mandate.id);
    } catch (err) {
      errorBox.textContent = err.message;
      errorBox.hidden = false;
      throw err;
    }
  });
});

/* ------------------------------------------------------------------ boot */

refresh();
