// HSA Simulator UI. Plain JavaScript, no build step. Talks to the JSON API under /api.

const $ = (sel, root = document) => root.querySelector(sel);
const state = {
  accounts: [],
  selectedId: null,
  categories: [],
  merchant: 0,            // index into PRESETS, or "other"
  revealedCard: null,     // the card as returned at issuance, the only time the full number exists
  expanded: new Set(),    // activity items currently showing their checks
};

const PRESETS = [
  { name: "Corner Pharmacy", mcc: "5912", kind: "Pharmacy" },
  { name: "St. Mercy Hospital", mcc: "8062", kind: "Hospital" },
  { name: "Bright Smile Dental", mcc: "8021", kind: "Dentist" },
  { name: "ClearView Optical", mcc: "8043", kind: "Eyeglasses" },
  { name: "Trattoria Roma", mcc: "5812", kind: "Restaurant" },
  { name: "Volt Electronics", mcc: "5732", kind: "Electronics" },
  { name: "FreshMart Grocery", mcc: "5411", kind: "Grocery" },
];
const RACE_MERCHANTS = { 5912: "Corner Pharmacy", 8062: "St. Mercy Hospital", 5812: "Trattoria Roma" };
const REASONS = {
  INSUFFICIENT_FUNDS: "Not enough money",
  NOT_QUALIFIED_MEDICAL_EXPENSE: "Not a medical expense",
  CARD_FROZEN: "Card frozen",
  CARD_CANCELLED: "Card was replaced",
  CARD_EXPIRED: "Card expired",
};
// What to do about each decline, shown under the reason.
const NEXT_STEP = {
  INSUFFICIENT_FUNDS: "Add money, then try again. Nothing was charged.",
  NOT_QUALIFIED_MEDICAL_EXPENSE: "HSA money can only pay for qualified medical costs. Nothing was charged.",
  CARD_FROZEN: "Unfreeze the card to use it again. Nothing was charged.",
  CARD_CANCELLED: "This card was replaced. Use the current card. Nothing was charged.",
  CARD_EXPIRED: "Replace the card to get a new expiry date. Nothing was charged.",
};
// Short names for the five checks, for the compact step strip.
const CHECK_SHORT = {
  card_not_cancelled: "Card in use",
  card_not_frozen: "Not frozen",
  card_not_expired: "Not expired",
  merchant_qualified: "Eligible merchant",
  sufficient_funds: "Enough money",
};

// ---------------------------------------------------------------- helpers

const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const usd = (cents) => (cents / 100).toLocaleString("en-US", { style: "currency", currency: "USD" });
const toCents = (s) => Math.round(Number(String(s).replace(/[$,\s]/g, "")) * 100);
const timeOfDay = (iso) => new Date(iso).toLocaleTimeString([], { hour: "numeric", minute: "2-digit", second: "2-digit" });
const longDate = (isoDate) => new Date(`${isoDate}T12:00:00`).toLocaleDateString("en-US", { year: "numeric", month: "long", day: "numeric" });
const uuid = () => (crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(16).slice(2)}`);
const plural = (n, one, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;
const ms = (v) => (v < 10 ? v.toFixed(1) : Math.round(v)) + " ms";
const ordinal = (n) => n + (["th", "st", "nd", "rd"][((n % 100) - 20) % 10] || ["th", "st", "nd", "rd"][n % 100] || "th");
const selected = () => state.accounts.find((a) => a.id === state.selectedId);
const isEligible = (mcc) => Boolean(state.categories.find((c) => c.mcc === mcc)?.qualified);
const categoryLabel = (mcc) => state.categories.find((c) => c.mcc === mcc)?.label || `Category ${mcc}`;

class ApiError extends Error {
  constructor(status, body) {
    super(body?.error?.message || `The server answered ${status}.`);
    this.field = body?.error?.field || null;
  }
}

async function api(path, { method = "GET", body } = {}) {
  let res;
  try {
    res = await fetch(`/api${path}`, {
      method,
      headers: body ? { "Content-Type": "application/json" } : {},
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch {
    throw new ApiError(0, { error: { message: "Can't reach the server. Is `python run.py` still running?" } });
  }
  const data = await res.json().catch(() => null);
  if (!res.ok) throw new ApiError(res.status, data);
  return data;
}

function toast(message, kind = "info") {
  const el = document.createElement("div");
  el.className = `toast ${kind}`;
  el.textContent = message;
  $("#toasts").appendChild(el);
  setTimeout(() => el.remove(), kind === "error" ? 6000 : 3000);
}

// ---------------------------------------------------------------- form errors

/** Show an error under the input it belongs to, so the message sits where the fix is. */
function showFieldError(form, field, message) {
  const input = form.elements[field];
  if (!input || input.closest("[hidden]")) return false;
  const label = input.closest("label");
  const note = document.createElement("span");
  note.className = "field-error";
  note.id = `${form.id}-${field}-error`;
  note.textContent = message;
  label.appendChild(note);
  input.setAttribute("aria-invalid", "true");
  input.setAttribute("aria-describedby", note.id);
  input.focus();
  return true;
}

function clearFieldErrors(form) {
  form.querySelectorAll(".field-error").forEach((el) => el.remove());
  form.querySelectorAll("[aria-invalid]").forEach((el) => {
    el.removeAttribute("aria-invalid");
    el.removeAttribute("aria-describedby");
  });
}

/** Run a form's action: disable its button, and put any error next to the right field. */
async function submitForm(form, action) {
  const button = form.querySelector("[type=submit]");
  clearFieldErrors(form);
  button.disabled = true;
  try {
    await action();
  } catch (err) {
    if (!(err.field && showFieldError(form, err.field, err.message))) toast(err.message, "error");
  } finally {
    button.disabled = false;
  }
}

async function withButton(button, action) {
  button.disabled = true;
  try { await action(); }
  catch (err) { toast(err.message, "error"); }
  finally { button.disabled = false; }
}

// ---------------------------------------------------------------- loading

function selectAccount(id) {
  state.selectedId = id;
  state.revealedCard = null;
  state.expanded.clear();
  renderReceipt(null);
  $("#race-result").innerHTML = "";
  document.querySelectorAll("form").forEach(clearFieldErrors);
}

async function refresh() {
  state.accounts = await api("/accounts");
  if (!state.accounts.some((a) => a.id === state.selectedId)) selectAccount(state.accounts[0]?.id ?? null);
  renderShell();
  if (selected()) await renderAccount();
}

// ---------------------------------------------------------------- overview

function renderShell() {
  const has = state.accounts.length > 0;
  $("#welcome").hidden = has;
  $("#workspace").hidden = !has;
  $("#switcher").hidden = !has;
  $("#account-select").innerHTML = state.accounts.map((a) =>
    `<option value="${esc(a.id)}" ${a.id === state.selectedId ? "selected" : ""}>${esc(a.holder_name)} (${usd(a.balance_cents)})</option>`).join("");
}

async function renderAccount() {
  const acct = selected();
  $("#acct-name").textContent = acct.holder_name;
  $("#acct-meta").textContent =
    `${acct.email}. ${acct.coverage_type === "family" ? "Family" : "Self-only"} health plan, born ${longDate(acct.date_of_birth)}.`;
  $("#acct-balance").textContent = usd(acct.balance_cents);

  const c = acct.contributions;
  $("#limit-label").textContent = `${c.tax_year} contributions`;
  $("#limit-text").textContent = `${usd(c.contributed_cents)} of ${usd(c.limit_cents)} IRS limit`;
  $("#limit-bar").style.width = `${Math.min(100, (c.contributed_cents / c.limit_cents) * 100)}%`;
  $("#limit-meter").setAttribute("aria-valuemax", c.limit_cents);
  $("#limit-meter").setAttribute("aria-valuenow", c.contributed_cents);
  $("#deposit-hint").textContent = c.remaining_cents > 0
    ? `You can add ${usd(c.remaining_cents)} more in ${c.tax_year}.`
    : `This account has reached its ${c.tax_year} limit. Deposits reopen in January.`;

  renderCard(acct);
  renderGates(acct);
  updatePayButton();
  updateContention();
  await renderActivity(acct.id);
}

const CONTACTLESS = `<svg class="contactless" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true">
  <path d="M8.5 8.5a5 5 0 0 1 0 7"/><path d="M12 6a8.5 8.5 0 0 1 0 12"/><path d="M15.5 3.5a12 12 0 0 1 0 17"/></svg>`;

function renderCard(acct) {
  const card = acct.card;
  const visual = $("#card-visual");
  const reveal = $("#card-once");

  if (!card) {
    visual.className = "";
    visual.innerHTML = `<div class="card-empty">
      <span class="ghost-chip" aria-hidden="true"></span>
      <b>No card yet</b>
      <p>Issue a virtual debit card to pay eligible medical expenses from this balance.</p>
      <button class="btn primary" data-card="issue" type="button">Issue virtual card</button>
    </div>`;
    $("#card-actions").innerHTML = "";
    reveal.hidden = true;
    return;
  }

  const frozen = card.status === "frozen";
  const exp = `${String(card.exp_month).padStart(2, "0")}/${String(card.exp_year).slice(-2)}`;
  visual.className = `card-visual${frozen ? " frozen" : ""}`;
  visual.setAttribute("aria-label", `HSA debit card ending ${card.last4}, ${frozen ? "frozen" : "active"}, expires ${exp}`);
  visual.innerHTML = `
    <div class="card-row"><span class="card-brand">HSA<small>DEBIT</small></span><span class="card-status">${frozen ? "Frozen" : "Active"}</span></div>
    <div class="card-chip-row"><span class="card-chip" aria-hidden="true"></span>${CONTACTLESS}</div>
    <div class="card-number">•••• •••• •••• ${esc(card.last4)}</div>
    <div class="card-foot">
      <div><span>Card holder</span><b>${esc(acct.holder_name.toUpperCase())}</b></div>
      <div style="text-align:right"><span>Expires</span><b>${exp}</b></div>
    </div>`;

  // The full number exists only in the issuance response, so it's shown once, beside the card.
  const revealed = state.revealedCard?.id === card.id ? state.revealedCard : null;
  reveal.hidden = !revealed;
  if (revealed) {
    reveal.innerHTML = `New card number, shown only this once:
      <span class="pan">${esc(revealed.number)}</span>
      The server keeps just the last four digits. <button class="linkish" type="button" data-card="hide-number">Hide</button>`;
  }
  $("#card-actions").innerHTML = `
    ${frozen
      ? `<button class="btn" data-card="unfreeze" type="button">Unfreeze card</button>`
      : `<button class="btn" data-card="freeze" type="button">Freeze card</button>`}
    <button class="btn" data-card="replace" type="button">Replace card</button>`;
}

/** No card, nothing to pay with: lock the forms that need one and say why, once. */
function renderGates(acct) {
  for (const id of ["form-purchase", "form-race"]) {
    const form = $(`#${id}`);
    form.querySelector("fieldset").disabled = !acct.card;
    $(".gate", form.parentElement).hidden = Boolean(acct.card);
  }
}

// ---------------------------------------------------------------- paying

function renderMerchants() {
  const tile = (p, i) => {
    const ok = isEligible(p.mcc);
    return `<button type="button" class="tile" role="radio" aria-checked="false" data-merchant="${i}">
      <span class="tile-name">${esc(p.name)}</span>
      <span class="tile-cat">${esc(p.kind)}</span>
      <span class="tile-foot"><span class="tile-elig ${ok ? "ok" : "no"}">${ok ? "Eligible" : "Not eligible"}</span>
        <span class="tile-mcc" title="Merchant category code">MCC ${p.mcc}</span></span>
    </button>`;
  };
  $("#merchant-presets").innerHTML = PRESETS.map(tile).join("") +
    `<button type="button" class="tile other" role="radio" aria-checked="false" data-merchant="other">
      <span class="tile-name">Another merchant</span>
      <span class="tile-cat">Type a name, pick a category</span>
    </button>`;

  const option = (c) => `<option value="${c.mcc}">${esc(c.label)} (${c.mcc})</option>`;
  $("#mcc-select").innerHTML =
    `<optgroup label="HSA eligible">${state.categories.filter((c) => c.qualified).map(option).join("")}</optgroup>
     <optgroup label="Not eligible">${state.categories.filter((c) => !c.qualified).map(option).join("")}
       <option value="1234">Unknown category (1234)</option></optgroup>`;
  chooseMerchant(0);
}

function chooseMerchant(choice) {
  state.merchant = choice;
  const form = $("#form-purchase");
  const other = choice === "other";
  $("#custom-merchant").hidden = !other;
  if (other) {
    form.merchant_name.value = "";
    form.merchant_name.focus();
  } else {
    form.merchant_name.value = PRESETS[choice].name;
    form.mcc.value = PRESETS[choice].mcc;
  }
  document.querySelectorAll("[data-merchant]").forEach((t) =>
    t.setAttribute("aria-checked", String(t.dataset.merchant === String(choice))));
}

function updatePayButton() {
  const cents = toCents($("#form-purchase").amount.value);
  $("#pay-btn").textContent = cents > 0 ? `Pay ${usd(cents)}` : "Pay";
}

function checksHtml(decision) {
  const icon = { passed: "✓", failed: "✕", skipped: "–" };
  return `<ol class="checks">${decision.map((c) => `
    <li class="${c.status}">
      <span class="icon" aria-hidden="true">${icon[c.status]}</span>
      <span class="what">${esc(c.label)}<span class="visually-hidden">: ${c.status}</span>
        <span class="detail">${esc(c.detail)}</span></span>
    </li>`).join("")}</ol>`;
}

function stripHtml(decision) {
  const icon = { passed: "✓", failed: "✕", skipped: "" };
  return `<ol class="strip" aria-label="Checks, in order">${decision.map((c) =>
    `<li class="${c.status}"><span class="dot" aria-hidden="true">${icon[c.status]}</span>${CHECK_SHORT[c.check] || esc(c.label)}<span class="visually-hidden">: ${c.status}</span></li>`).join("")}</ol>`;
}

/** The receipt answers "what happened and why" in its first two lines. */
function renderReceipt(t) {
  const area = $("#slip-area");
  if (!t) {
    area.innerHTML = `<div class="receipt-empty">
      <b>The decision appears here</b>
      <p>Every payment runs these checks in order. The first one that fails decides the decline, and the rest are skipped.</p>
      <ol class="strip preview" aria-hidden="true">${Object.values(CHECK_SHORT).map((n) => `<li><span class="dot"></span>${n}</li>`).join("")}</ol>
    </div>`;
    return;
  }
  const ok = t.status === "approved";
  const failed = t.decision.find((c) => c.status === "failed");
  const before = ok ? t.balance_after_cents + t.amount_cents : t.balance_after_cents;
  area.innerHTML = `<article class="receipt" aria-label="Payment result">
    <div class="receipt-head">
      <span class="verdict-icon ${ok ? "ok" : "no"}" aria-hidden="true">${ok ? "✓" : "✕"}</span>
      <span class="verdict ${ok ? "ok" : "no"}">${ok ? "Approved" : "Declined"}</span>
      <span class="receipt-amount ${ok ? "" : "no"}">${usd(t.amount_cents)}</span>
      <span class="receipt-where">${esc(t.merchant_name)}, ${esc(t.merchant_category)} (MCC ${esc(t.mcc)})</span>
    </div>
    <p class="reason ${ok ? "ok" : "no"}">${ok
      ? `Paid. It's an eligible medical expense and the balance covered it.`
      : `${esc(failed.detail)}.<small>${NEXT_STEP[t.decline_reason] || ""}</small>`}</p>
    ${stripHtml(t.decision)}
    <div class="balance-move"><span>Balance</span>
      <span>${ok ? `${usd(before)} → <b>${usd(t.balance_after_cents)}</b>` : `<b>${usd(before)}</b>, unchanged`}</span></div>
    <details class="more"><summary>What each check found</summary>${checksHtml(t.decision)}</details>
  </article>`;
}

// ---------------------------------------------------------------- activity

async function renderActivity(accountId) {
  const items = await api(`/accounts/${accountId}/activity`);
  const feed = $("#activity-body");
  if (!items.length) {
    feed.innerHTML = `<li class="feed-empty">Nothing yet. Add money above, then pay with the card.</li>`;
    return;
  }
  feed.innerHTML = items.map((i) => {
    if (i.kind === "deposit") {
      return `<li><div class="item">
        <span class="item-icon deposit" aria-hidden="true">+</span>
        <div><div class="item-title">Deposit</div><div class="item-sub">Counts toward ${i.tax_year}, ${timeOfDay(i.created_at)}</div></div>
        <div class="item-money"><b class="in">+${usd(i.amount_cents)}</b><span>Balance ${usd(i.balance_after_cents)}</span></div>
        <span class="toggle-spacer"></span>
      </div></li>`;
    }
    const ok = i.status === "approved";
    const open = state.expanded.has(i.id);
    return `<li><div class="item">
        <span class="item-icon ${ok ? "ok" : "no"}" aria-hidden="true">${ok ? "✓" : "✕"}</span>
        <div><div class="item-title">${esc(i.merchant_name)}</div>
          <div class="item-sub">${ok ? esc(i.merchant_category) : `<span class="no">Declined: ${esc(REASONS[i.decline_reason] || i.decline_reason)}</span>`}, ${timeOfDay(i.created_at)}</div></div>
        <div class="item-money">${ok
          ? `<b>−${usd(i.amount_cents)}</b><span>Balance ${usd(i.balance_after_cents)}</span>`
          : `<b class="void">${usd(i.amount_cents)}</b><span>Not charged</span>`}</div>
        <button class="toggle" type="button" data-trace="${esc(i.id)}" aria-expanded="${open}">${open ? "Hide checks" : "Show checks"}</button>
      </div>
      ${open ? `<div class="item-trace">${checksHtml(i.decision)}</div>` : ""}</li>`;
  }).join("");
}

// ---------------------------------------------------------------- race

function raceAmounts() {
  const form = $("#form-race");
  const base = form.amounts.value.split(",").map((s) => s.trim()).filter(Boolean);
  const repeat = Math.max(1, Math.min(50, Number(form.repeat.value) || 1));
  return Array.from({ length: repeat }, () => base).flat();
}

/** Before anything is sent, say whether this setup can show a race at all. */
function updateContention({ afterRace = false } = {}) {
  const box = $("#contention");
  const [kind, html] = afterRace ? ["", ""] : contentionMessage(selected());
  // Only touch the DOM when the message changes. Leaving the amounts field fires
  // "change"; redrawing then would swap out the suggestion button between
  // mouse-down and mouse-up, and the click would never land.
  if (box.innerHTML === html && box.className === `contention ${kind}`.trim()) return;
  box.className = `contention ${kind}`.trim();
  box.innerHTML = html;
}

function contentionMessage(acct) {
  if (!acct?.card) return ["", ""];
  const amounts = raceAmounts();
  const cents = amounts.map(toCents);
  if (!amounts.length || cents.some((c) => !(c > 0))) return ["", "Enter amounts like 80, 50."];
  const total = cents.reduce((a, b) => a + b, 0);
  const n = plural(amounts.length, "purchase");
  const all = amounts.length === 2 ? "both purchases" : `all ${n}`;

  if (acct.card.status === "frozen") return ["", `The card is frozen, so ${all} will be declined. Unfreeze it to see a race.`];
  if ($("#form-race").mcc.value === "5812") {
    return ["", `A restaurant isn't a medical expense, so ${all} will be declined before the balance is even checked.`];
  }
  if (acct.balance_cents === 0) return ["", "The balance is $0.00, so every purchase will be declined. Add money first."];
  if (total <= acct.balance_cents) {
    const a = Math.max(1, Math.round(acct.balance_cents * 0.8 / 100));
    const b = Math.max(1, Math.round(acct.balance_cents * 0.5 / 100));
    return ["calm", `The balance of ${usd(acct.balance_cents)} covers ${all} (${usd(total)}), so none has to be declined and nothing competes for the money. ` +
      `<button class="btn" type="button" id="btn-make-race" data-a="${a}" data-b="${b}">Use $${a} and $${b} instead</button>`];
  }
  return ["", `${n[0].toUpperCase() + n.slice(1)} totaling ${usd(total)} against a balance of ${usd(acct.balance_cents)}: some must be declined. Which ones depends on who gets the lock first.`];
}

function fireConcurrently(cardId, amounts, mcc) {
  const name = RACE_MERCHANTS[mcc] || "Merchant";
  // Promise.all with no awaits in between: every request leaves the browser together.
  return Promise.all(amounts.map((amount, idx) =>
    api("/transactions", {
      method: "POST",
      body: { card_id: cardId, amount, mcc, merchant_name: `${name}, request ${idx + 1}`, idempotency_key: uuid() },
    }).then((t) => ({ ok: true, t }), (e) => ({ ok: false, e }))));
}

/** Turn the server's lock timings into positions on a shared time axis. */
function timelineLayout(txns) {
  const rows = [...txns].sort((a, b) => a.lock_timing.acquired_ms - b.lock_timing.acquired_ms);
  const start = Math.min(...rows.map((t) => t.lock_timing.requested_ms));
  const end = Math.max(...rows.map((t) => t.lock_timing.released_ms));
  const span = Math.max(end - start, 0.5);
  const at = (v) => ((v - start) / span) * 100;
  let idle = 0;
  rows.forEach((t, i) => {
    if (i) idle += Math.max(0, t.lock_timing.acquired_ms - rows[i - 1].lock_timing.released_ms);
  });
  return {
    rows, span, at,
    askedWithin: Math.max(...rows.map((t) => t.lock_timing.requested_ms)) - start,
    idleShare: idle / span,
  };
}

function axisTicks(span) {
  const raw = span / 4;
  const pow = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 5, 10].map((m) => m * pow).find((s) => s >= raw);
  const ticks = [];
  for (let v = 0; v <= span + 1e-9; v += step) ticks.push(v);
  return ticks;
}

/** One request: amber while queued, navy while it holds the lock, then a mark
    for the decision it made. The balance it saw is what explains that decision. */
function timelineRowHtml(t, i, at, stagger) {
  const { requested_ms: req, acquired_ms: acq, released_ms: rel } = t.lock_timing;
  const ok = t.status === "approved";
  const saw = ok ? t.balance_after_cents + t.amount_cents : t.balance_after_cents;
  const delay = (extra = 0) => `--d:${(i * stagger + extra).toFixed(2)}s`;
  const why = ok ? `saw ${usd(saw)}, left ${usd(t.balance_after_cents)}`
    : t.decline_reason === "INSUFFICIENT_FUNDS" ? `saw only ${usd(saw)}`
    : esc(REASONS[t.decline_reason]);
  return `<div class="tl-row" role="row">
    <div class="tl-name" role="cell"><b>${usd(t.amount_cents)}</b><span>${ordinal(i + 1)} to get the lock</span></div>
    <div class="tl-track" role="cell" aria-label="Waited ${ms(acq - req)} for the lock, held it ${ms(rel - acq)}, then ${ok ? "approved" : "declined"}">
      <div class="tl-lane">
        <span class="seg wait" style="left:${at(req)}%;width:${at(acq) - at(req)}%;${delay()}"></span>
        <span class="seg hold" style="left:${at(acq)}%;width:${at(rel) - at(acq)}%;${delay(0.15)}"></span>
        <span class="outcome ${ok ? "ok" : "no"}" style="left:${at(rel)}%;${delay(0.3)}" aria-hidden="true">${ok ? "✓" : "✕"}</span>
      </div>
    </div>
    <div class="tl-out" role="cell" style="${delay(0.3)}"><b class="${ok ? "ok" : "no"}">${ok ? "Approved" : "Declined"}</b>
      <span>${why}. Waited ${ms(acq - req)}</span></div>
  </div>`;
}

function renderRace(target, { startCents, results, finalCents }) {
  const txns = results.filter((r) => r.ok && r.t.lock_timing).map((r) => r.t);
  const errors = results.filter((r) => !r.ok);
  if (!txns.length) {
    target.innerHTML = `<p class="tl-note">No purchase was processed. ${esc(errors[0]?.e.message || "")}</p>`;
    return;
  }
  const { rows, span, at, askedWithin, idleShare } = timelineLayout(txns);
  const approved = rows.filter((t) => t.status === "approved");
  const spent = approved.reduce((s, t) => s + t.amount_cents, 0);
  const consistent = finalCents === startCents - spent && finalCents >= 0 && !errors.length;
  const stagger = Math.min(0.12, 1.2 / rows.length);

  target.innerHTML = `
    <div class="timeline">
      <p class="tl-summary">
        One at a time: <span class="num-ok">${approved.length} approved</span>, <span class="num-no">${rows.length - approved.length} declined</span>.
        Balance ${usd(startCents)} → ${usd(finalCents)}${consistent ? ", never below zero." : "."}
      </p>
      <p class="tl-sub">${plural(rows.length, "purchase")} asked for the account's lock within ${ms(askedWithin)} of each other. Rows are in the order they got it.</p>
      ${consistent ? "" : `<p class="tl-note warn">Something doesn't add up: ${plural(errors.length, "request")} failed, or the balance isn't ${usd(startCents)} minus ${usd(spent)}. ${esc(errors[0]?.e.message || "")}</p>`}
      <div class="tl-legend" aria-hidden="true">
        <span><i class="sw wait"></i>Waiting for the lock</span>
        <span><i class="sw hold"></i>Holding the lock</span>
        <span><span class="sw-out"><i class="ok"></i><i class="no"></i></span>Decision, made while holding it</span>
      </div>
      <div class="tl-grid${rows.length > 12 ? " dense" : ""}" role="table" aria-label="Purchases in the order they got the lock">
        ${rows.map((t, i) => timelineRowHtml(t, i, at, stagger)).join("")}
        <div class="tl-axis" aria-hidden="true"><div class="tl-lane">${axisTicks(span).map((v) => `<span style="left:${(v / span) * 100}%">${v === 0 ? "0 ms" : ms(v)}</span>`).join("")}</div></div>
      </div>
      <div class="tl-notes">
        <p class="tl-note">Times are measured on the server. A purchase starts holding the lock only after the one above it lets go, so each one decides using the balance the previous one left.</p>
        ${rows.length > 6 ? `<p class="tl-note">Browsers open at most six connections to one server, so a batch of ${rows.length} leaves the browser in waves. The guarantee doesn't depend on that; the automated tests release 40 at once from threads.</p>` : ""}
        ${idleShare > 0.15 ? `<p class="tl-note">The gaps between bars are SQLite's busy handler: a waiting request sleeps for a growing interval (1, 2, 5, 10 ms and so on) before trying again, so it can wake after the lock is already free. A database with row locks, such as Postgres, hands the lock over immediately.</p>` : ""}
      </div>
    </div>`;
  const tl = $(".timeline", target);
  requestAnimationFrame(() => tl.classList.add("play"));
}

async function runRace(accountId, cardId, amounts, mcc) {
  const startCents = (await api(`/accounts/${accountId}`)).balance_cents;
  const results = await fireConcurrently(cardId, amounts, mcc);
  await refresh();
  renderRace($("#race-result"), { startCents, results, finalCents: selected().balance_cents });
  updateContention({ afterRace: true });
}

async function runScenario() {
  const stamp = uuid().slice(0, 4);
  const acct = await api("/accounts", {
    method: "POST",
    body: { holder_name: `Race demo ${stamp}`, email: `race-${stamp}-${uuid().slice(0, 8)}@example.com`, date_of_birth: "1990-01-01", coverage_type: "self_only" },
  });
  await api(`/accounts/${acct.id}/deposits`, { method: "POST", body: { amount: "100.00", idempotency_key: uuid() } });
  const card = await api(`/accounts/${acct.id}/cards`, { method: "POST", body: {} });
  selectAccount(acct.id);
  const form = $("#form-race");
  form.amounts.value = "80, 50";
  form.repeat.value = "1";
  form.mcc.value = "5912";
  await runRace(acct.id, card.id, ["80.00", "50.00"], "5912");
  $("#h-race").scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "start" });
}

// ---------------------------------------------------------------- events

const dialog = $("#dialog-account");
function openNewAccount() {
  clearFieldErrors($("#form-account"));
  dialog.showModal();
  $("[name=holder_name]", dialog).focus();
}

$("#btn-new-account").addEventListener("click", openNewAccount);
$("#btn-cancel-account").addEventListener("click", () => dialog.close());
$("#btn-scenario").addEventListener("click", (e) => withButton(e.currentTarget, runScenario));
$("#welcome").addEventListener("click", (e) => {
  const b = e.target.closest("[data-action]");
  if (b?.dataset.action === "new-account") openNewAccount();
  else if (b) withButton(b, runScenario);
});

// Typing in a field clears the error pinned to it.
document.addEventListener("input", (e) => {
  if (e.target.getAttribute?.("aria-invalid")) {
    e.target.closest("label")?.querySelector(".field-error")?.remove();
    e.target.removeAttribute("aria-invalid");
  }
});

$("#form-account").addEventListener("submit", (e) => {
  e.preventDefault();
  const form = e.currentTarget;
  submitForm(form, async () => {
    const acct = await api("/accounts", { method: "POST", body: Object.fromEntries(new FormData(form)) });
    dialog.close();
    form.reset();
    selectAccount(acct.id);
    toast(`Created an account for ${acct.holder_name}`);
    await refresh();
  });
});

$("#account-select").addEventListener("change", (e) => {
  selectAccount(e.target.value);
  refresh().catch((err) => toast(err.message, "error"));
});

$("#deposit-quick").addEventListener("click", (e) => {
  const chip = e.target.closest("[data-amount]");
  if (chip) $("#form-deposit").amount.value = chip.dataset.amount;
});

$("#form-deposit").addEventListener("submit", (e) => {
  e.preventDefault();
  const form = e.currentTarget;
  submitForm(form, async () => {
    const dep = await api(`/accounts/${state.selectedId}/deposits`, {
      method: "POST", body: { amount: form.amount.value, idempotency_key: uuid() },
    });
    toast(`Deposited ${usd(dep.amount_cents)}`);
    form.amount.value = "";
    await refresh();
  });
});

// Card buttons live inside the card area, which is redrawn on every refresh.
$(".overview-card").addEventListener("click", (e) => {
  const btn = e.target.closest("[data-card]");
  if (!btn) return;
  const action = btn.dataset.card;
  if (action === "hide-number") {
    state.revealedCard = null;
    renderCard(selected());
    return;
  }
  withButton(btn, async () => {
    if (action === "issue" || action === "replace") {
      if (action === "replace" && !confirm("Cancel this card and issue a new number? The old card will stop working.")) return;
      state.revealedCard = await api(`/accounts/${state.selectedId}/cards`, { method: "POST", body: { replace: action === "replace" } });
      toast(action === "issue" ? "Issued a virtual card" : "Replaced the card. The old one is cancelled.");
    } else {
      await api(`/cards/${selected().card.id}/${action}`, { method: "POST" });
      toast(action === "freeze" ? "Froze the card" : "Unfroze the card");
    }
    await refresh();
  });
});

$("#merchant-presets").addEventListener("click", (e) => {
  const tile = e.target.closest("[data-merchant]");
  if (tile) chooseMerchant(tile.dataset.merchant === "other" ? "other" : Number(tile.dataset.merchant));
});
$("#form-purchase").amount.addEventListener("input", updatePayButton);

$("#form-purchase").addEventListener("submit", (e) => {
  e.preventDefault();
  const form = e.currentTarget;
  submitForm(form, async () => {
    const t = await api("/transactions", {
      method: "POST",
      body: { merchant_name: form.merchant_name.value, mcc: form.mcc.value, amount: form.amount.value,
              card_id: selected().card.id, idempotency_key: uuid() },
    });
    renderReceipt(t);
    await refresh();
  });
});

$("#form-race").addEventListener("input", () => updateContention());
$("#form-race").addEventListener("change", () => updateContention());
$("#contention").addEventListener("click", (e) => {
  const b = e.target.closest("#btn-make-race");
  if (!b) return;
  $("#form-race").amounts.value = `${b.dataset.a}, ${b.dataset.b}`;
  $("#form-race").repeat.value = "1";
  updateContention();
});

$("#form-race").addEventListener("submit", (e) => {
  e.preventDefault();
  const form = e.currentTarget;
  const acct = selected();
  const amounts = raceAmounts();
  submitForm(form, async () => {
    if (!amounts.length || amounts.some((a) => !(toCents(a) > 0))) {
      throw Object.assign(new Error("Enter amounts separated by commas, like 80, 50."), { field: "amounts" });
    }
    await runRace(acct.id, acct.card.id, amounts, form.mcc.value);
  });
});

$("#activity-body").addEventListener("click", (e) => {
  const b = e.target.closest("[data-trace]");
  if (!b) return;
  const id = b.dataset.trace;
  state.expanded.has(id) ? state.expanded.delete(id) : state.expanded.add(id);
  renderActivity(state.selectedId).catch((err) => toast(err.message, "error"));
});

// ---------------------------------------------------------------- start

(async function start() {
  try {
    state.categories = await api("/merchant-categories");
    renderMerchants();
    renderReceipt(null);
    await refresh();
  } catch (err) {
    toast(err.message, "error");
  }
})();
