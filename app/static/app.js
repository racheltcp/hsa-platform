// HSA Simulator UI. Vanilla JS, no build step. Talks to the JSON API under /api.

const $ = (sel, root = document) => root.querySelector(sel);
const state = {
  accounts: [],
  selectedId: null,
  categories: [],
  revealedCard: null,     // card returned at issuance (only time the full number exists)
  expanded: new Set(),    // ledger rows showing their checks
};

const PRESETS = [
  { name: "Corner Pharmacy", mcc: "5912", label: "Pharmacy" },
  { name: "St. Mercy Hospital", mcc: "8062", label: "Hospital" },
  { name: "Bright Smile Dental", mcc: "8021", label: "Dentist" },
  { name: "ClearView Optical", mcc: "8043", label: "Eyeglasses" },
  { name: "Trattoria Roma", mcc: "5812", label: "Restaurant" },
  { name: "Volt Electronics", mcc: "5732", label: "Electronics" },
  { name: "FreshMart Grocery", mcc: "5411", label: "Grocery" },
];
const RACE_MERCHANTS = { 5912: "Corner Pharmacy", 8062: "St. Mercy Hospital", 5812: "Trattoria Roma" };

const REASONS = {
  INSUFFICIENT_FUNDS: "Not enough money",
  NOT_QUALIFIED_MEDICAL_EXPENSE: "Not a medical expense",
  CARD_FROZEN: "Card frozen",
  CARD_CANCELLED: "Card cancelled",
  CARD_EXPIRED: "Card expired",
  ACCOUNT_CLOSED: "Account closed",
};

// ---------------------------------------------------------------- helpers

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}
const usd = (cents) => (cents / 100).toLocaleString("en-US", { style: "currency", currency: "USD" });
const toCents = (s) => Math.round(Number(String(s).replace(/[$,\s]/g, "")) * 100);
const clock = (iso) => new Date(iso).toLocaleTimeString([], { hour: "numeric", minute: "2-digit", second: "2-digit" });
const longDate = (isoDate) => new Date(`${isoDate}T12:00:00`).toLocaleDateString("en-US", { year: "numeric", month: "long", day: "numeric" });
const uuid = () => (crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(16).slice(2)}`);
const plural = (n, one, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;
const ms = (v) => (v < 10 ? v.toFixed(1) : Math.round(v)) + " ms";

class ApiError extends Error {
  constructor(status, body) {
    super(body?.error?.message || `The server answered ${status}`);
    this.status = status;
  }
}

async function api(path, { method = "GET", body } = {}) {
  const res = await fetch(`/api${path}`, {
    method,
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
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

async function busy(button, fn) {
  if (button) button.disabled = true;
  try { return await fn(); }
  catch (err) { toast(err.message, "error"); }
  finally { if (button) button.disabled = false; }
}

const selected = () => state.accounts.find((a) => a.id === state.selectedId);

function selectAccount(id) {
  state.selectedId = id;
  state.revealedCard = null;
  state.expanded.clear();
  $("#slip-area").innerHTML = `<p class="slip-empty">Swipe the card to see each check the purchase goes through.</p>`;
  $("#race-result").innerHTML = "";
}

// ---------------------------------------------------------------- loading

async function refresh() {
  state.accounts = await api("/accounts");
  if (!state.accounts.some((a) => a.id === state.selectedId)) {
    selectAccount(state.accounts[0]?.id ?? null);
  }
  renderShell();
  if (selected()) await renderAccount();
}

// ---------------------------------------------------------------- rendering

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
    `${acct.email}. ${acct.coverage_type === "family" ? "Family" : "Self-only"} coverage, born ${longDate(acct.date_of_birth)}.`;
  $("#acct-balance").textContent = usd(acct.balance_cents);

  const c = acct.contributions;
  const pct = Math.min(100, (c.contributed_cents / c.annual_limit_cents) * 100);
  $("#limit-label").textContent = `Contributed in ${c.tax_year}`;
  $("#limit-bar").style.width = `${pct}%`;
  const meter = $("#limit-meter");
  meter.setAttribute("aria-valuemax", c.annual_limit_cents);
  meter.setAttribute("aria-valuenow", c.contributed_cents);
  $("#limit-text").textContent = `${usd(c.contributed_cents)} of the ${usd(c.annual_limit_cents)} IRS limit`;
  $("#deposit-hint").textContent = c.remaining_cents > 0
    ? `You can deposit ${usd(c.remaining_cents)} more in ${c.tax_year} before reaching the IRS limit.`
    : `This account has reached its ${c.tax_year} IRS limit. Deposits will be refused until January.`;

  renderCard(acct);
  updateContention();
  await renderLedger(acct.id);
}

function renderCard(acct) {
  const card = acct.card;
  const visual = $("#card-visual");
  const once = $("#card-once");
  const revealed = state.revealedCard && card && state.revealedCard.id === card.id ? state.revealedCard : null;

  if (!card) {
    visual.className = "card-visual none";
    visual.innerHTML = `<p><b>No card yet</b><br>Issue a virtual card to start swiping.</p>`;
    $("#card-actions").innerHTML = `<button class="btn primary" data-card="issue" type="button">Issue virtual card</button>`;
    once.hidden = true;
    return;
  }
  const exp = `${String(card.exp_month).padStart(2, "0")}/${String(card.exp_year).slice(-2)}`;
  visual.className = `card-visual${card.status === "frozen" ? " frozen" : ""}`;
  visual.innerHTML = `
    <div class="card-top"><span class="card-chip" aria-hidden="true"></span><span>HSA debit</span></div>
    <div class="card-number" aria-label="Card number">${esc(revealed ? revealed.number : `•••• •••• •••• ${card.last4}`)}</div>
    <div class="card-bottom"><span>${esc(acct.holder_name)}</span><span>Expires ${exp}</span></div>`;
  once.hidden = !revealed;
  once.textContent = "This is the only time the full number is shown. The server keeps just the last four digits.";
  $("#card-actions").innerHTML = `
    ${card.status === "active"
      ? `<button class="btn" data-card="freeze" type="button">Freeze card</button>`
      : `<button class="btn" data-card="unfreeze" type="button">Unfreeze card</button>`}
    <button class="btn danger" data-card="replace" type="button">Replace card</button>`;
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

function slipHtml(t) {
  const ok = t.status === "approved";
  return `<article class="slip" aria-label="Authorization result">
    <p class="slip-title">${usd(t.amount_cents)} at ${esc(t.merchant_name)}</p>
    <span class="verdict-stamp ${ok ? "ok" : "no"}">${ok ? "Approved" : "Declined"}</span>
    <p class="slip-sub">${esc(t.merchant_category)}, MCC ${esc(t.mcc)}. ${ok ? `Balance now ${usd(t.balance_after_cents)}.` : `Balance unchanged at ${usd(t.balance_after_cents)}.`}</p>
    ${checksHtml(t.decision)}
  </article>`;
}

async function renderLedger(accountId) {
  const items = await api(`/accounts/${accountId}/activity`);
  const body = $("#activity-body");
  if (!items.length) {
    body.innerHTML = `<tr class="empty-row"><td colspan="5">Nothing here yet. Deposits and every purchase attempt, approved or declined, will be listed.</td></tr>`;
    return;
  }
  body.innerHTML = items.map((i) => {
    if (i.kind === "deposit") {
      return `<tr>
        <td>${clock(i.created_at)}</td>
        <td>Deposit<span class="sub">Counts toward ${i.tax_year}</span></td>
        <td><span class="result-tag in">Credited</span></td>
        <td class="num">+${usd(i.amount_cents)}</td>
        <td class="num">${usd(i.balance_after_cents)}</td></tr>`;
    }
    const ok = i.status === "approved";
    const open = state.expanded.has(i.id);
    return `<tr>
        <td>${clock(i.created_at)}</td>
        <td>${esc(i.merchant_name)}<span class="sub">${esc(i.merchant_category)}, MCC ${esc(i.mcc)}</span></td>
        <td><span class="result-tag ${ok ? "ok" : "no"}">${ok ? "Approved" : `Declined: ${esc(REASONS[i.decline_reason] || i.decline_reason)}`}</span>
          <button class="linkish" type="button" data-trace="${esc(i.id)}" aria-expanded="${open}">${open ? "Hide checks" : "Show checks"}</button></td>
        <td class="num">${ok ? "−" : ""}${usd(i.amount_cents)}</td>
        <td class="num">${usd(i.balance_after_cents)}</td></tr>
      ${open ? `<tr class="trace-row"><td></td><td colspan="4">${checksHtml(i.decision)}</td></tr>` : ""}`;
  }).join("");
}

function renderCategorySelect() {
  const qualified = state.categories.filter((c) => c.qualified);
  const other = state.categories.filter((c) => !c.qualified);
  $("#mcc-select").innerHTML =
    `<optgroup label="Medical (approved)">${qualified.map((c) => `<option value="${c.mcc}">${esc(c.label)} (${c.mcc})</option>`).join("")}</optgroup>
     <optgroup label="Not medical (declined)">${other.map((c) => `<option value="${c.mcc}">${esc(c.label)} (${c.mcc})</option>`).join("")}
       <option value="1234">Unknown category (1234)</option></optgroup>`;
  const ok = new Set(qualified.map((c) => c.mcc));
  $("#merchant-presets").innerHTML = PRESETS.map((p, i) =>
    `<button type="button" class="chip" data-preset="${i}" aria-pressed="false">
       <span class="mark ${ok.has(p.mcc) ? "ok" : "no"}" aria-hidden="true"></span>${esc(p.label)}
       <span class="visually-hidden">${ok.has(p.mcc) ? "(medical)" : "(not medical)"}</span></button>`).join("");
  applyPreset(0);
}

function applyPreset(i) {
  const p = PRESETS[i];
  const form = $("#form-purchase");
  form.merchant_name.value = p.name;
  form.mcc.value = p.mcc;
  document.querySelectorAll("[data-preset]").forEach((b) => b.setAttribute("aria-pressed", String(Number(b.dataset.preset) === i)));
}

// ---------------------------------------------------------------- race

function raceAmounts() {
  const form = $("#form-race");
  const base = form.amounts.value.split(",").map((s) => s.trim()).filter(Boolean);
  const repeat = Math.max(1, Math.min(50, Number(form.repeat.value) || 1));
  return Array.from({ length: repeat }, () => base).flat();
}

/** Tell the reviewer, before they press send, whether this setup can actually show a race. */
function updateContention({ afterRace = false } = {}) {
  const acct = selected();
  const box = $("#contention");
  if (!acct) return;
  if (afterRace) { box.className = "contention"; box.textContent = ""; return; }
  const amounts = raceAmounts();
  const cents = amounts.map(toCents);
  box.className = "contention";
  if (!acct.card) { box.textContent = "Issue a card first."; return; }
  if (!amounts.length || cents.some((c) => !(c > 0))) { box.textContent = "Enter amounts like 80, 50."; return; }
  const total = cents.reduce((a, b) => a + b, 0);
  const n = plural(amounts.length, "purchase");
  const all = amounts.length === 2 ? "both purchases" : `all ${n}`;
  if ($("#form-race").mcc.value === "5812") {
    box.textContent = `A restaurant isn't a medical expense, so ${all} will be declined before the balance is even checked.`;
    return;
  }
  if (acct.balance_cents === 0) {
    box.textContent = "The balance is $0.00, so every purchase will be declined. Add money first.";
    return;
  }
  if (total <= acct.balance_cents) {
    const a = Math.max(1, Math.round(acct.balance_cents * 0.8 / 100));
    const b = Math.max(1, Math.round(acct.balance_cents * 0.5 / 100));
    box.className = "contention calm";
    box.innerHTML = `The balance of ${usd(acct.balance_cents)} covers ${all} (${usd(total)}), so none has to be declined and nothing competes for the money.
      <button class="btn" type="button" id="btn-make-race" data-a="${a}" data-b="${b}">Use $${a} and $${b} instead</button>`;
    return;
  }
  box.textContent = `${n[0].toUpperCase() + n.slice(1)} totaling ${usd(total)} against a balance of ${usd(acct.balance_cents)}: some must be declined. Which ones depends on who reaches the lock first.`;
}

async function fireConcurrently(cardId, amounts, mcc) {
  const name = RACE_MERCHANTS[mcc] || "Merchant";
  // Promise.all with no awaits in between: every request leaves the browser together.
  return Promise.all(amounts.map((amount, idx) =>
    api("/transactions", {
      method: "POST",
      body: { card_id: cardId, amount, mcc, merchant_name: `${name}, request ${idx + 1}`, idempotency_key: uuid() },
    }).then((t) => ({ ok: true, t, idx, amount }), (e) => ({ ok: false, e, idx, amount }))));
}

function niceStep(span) {
  const raw = span / 4;
  const pow = 10 ** Math.floor(Math.log10(raw));
  return [1, 2, 5, 10].map((m) => m * pow).find((s) => s >= raw) || raw;
}

function renderRace(target, { startCents, results, finalCents }) {
  const txns = results.filter((r) => r.ok && r.t.processing).map((r) => r.t);
  const errors = results.filter((r) => !r.ok);
  if (!txns.length) {
    target.innerHTML = `<p class="tl-note">No purchase went through. ${errors.map((r) => esc(r.e.message)).join(" ")}</p>`;
    return;
  }
  const rows = [...txns].sort((a, b) => a.processing.lock_acquired_at_ms - b.processing.lock_acquired_at_ms);
  const t0 = Math.min(...rows.map((t) => t.processing.received_at_ms));
  const tEnd = Math.max(...rows.map((t) => t.processing.committed_at_ms));
  const span = Math.max(tEnd - t0, 0.5);
  const pct = (t) => `${((t - t0) / span) * 100}%`;
  const width = (a, b) => `${Math.max(((b - a) / span) * 100, 0)}%`;
  const arrivalSpread = Math.max(...rows.map((t) => t.processing.received_at_ms)) - t0;

  const approved = rows.filter((t) => t.status === "approved");
  const spent = approved.reduce((s, t) => s + t.amount_cents, 0);
  const consistent = finalCents === startCents - spent && finalCents >= 0 && !errors.length;

  let idle = 0;
  rows.forEach((t, i) => { if (i) idle += Math.max(0, t.processing.lock_acquired_at_ms - rows[i - 1].processing.committed_at_ms); });

  const step = niceStep(span);
  const ticks = [];
  for (let v = 0; v <= span + 1e-9; v += step) ticks.push(v);

  const stagger = Math.min(0.12, 1.2 / rows.length);
  target.innerHTML = `
    <div class="timeline">
      <p class="tl-summary">
        ${plural(rows.length, "purchase")} reached the server within ${ms(arrivalSpread)} of each other.
        The lock let them through one at a time:
        <span class="num-ok">${approved.length} approved</span>, <span class="num-no">${rows.length - approved.length} declined</span>,
        and the balance went from ${usd(startCents)} to ${usd(finalCents)}${consistent ? ", never below zero." : "."}
      </p>
      ${consistent ? "" : `<p class="tl-note" style="color:var(--decline)">Something is off: ${errors.length} request(s) failed or the balance does not match ${usd(startCents)} minus ${usd(spent)}.</p>`}
      <div class="tl-legend" aria-hidden="true">
        <span><i class="sw wait"></i>Waiting for the lock</span>
        <span><i class="sw hold" style="background:var(--approve)"></i>Holding the lock, approved</span>
        <span><i class="sw hold" style="background:var(--decline)"></i>Holding the lock, declined</span>
      </div>
      <div class="tl-grid${rows.length > 12 ? " dense" : ""}" role="table" aria-label="Purchases in the order they acquired the lock">
        ${rows.map((t, i) => {
          const p = t.processing;
          const ok = t.status === "approved";
          return `<div class="tl-row" role="row" style="--d:${(i * stagger).toFixed(2)}s">
            <div class="tl-name" role="cell"><b>${usd(t.amount_cents)}</b><span>${ordinal(i + 1)} through</span></div>
            <div class="tl-track" role="cell" aria-label="Waited ${ms(p.lock_wait_ms)}, held the lock ${ms(p.lock_held_ms)}">
              <span class="seg wait" style="left:${pct(p.received_at_ms)};width:${width(p.received_at_ms, p.lock_acquired_at_ms)};--d:${(i * stagger).toFixed(2)}s"></span>
              <span class="seg hold ${ok ? "ok" : "no"}" style="left:${pct(p.lock_acquired_at_ms)};width:${width(p.lock_acquired_at_ms, p.committed_at_ms)};--d:${(i * stagger + 0.15).toFixed(2)}s"></span>
            </div>
            <div class="tl-out" role="cell"><b class="${ok ? "ok" : "no"}">${ok ? "Approved" : `Declined`}</b>
              <span>${ok ? `${usd(t.balance_after_cents)} left` : esc(REASONS[t.decline_reason] || t.decline_reason)}, waited ${ms(p.lock_wait_ms)}</span></div>
          </div>`;
        }).join("")}
        <div class="tl-axis" aria-hidden="true">${ticks.map((v) => `<span style="left:${(v / span) * 100}%">${v === 0 ? "0 ms" : ms(v)}</span>`).join("")}</div>
      </div>
      <p class="tl-note">Each bar is one request, timed on the server. A purchase only starts holding the lock after the one above it has let go, which is why the second purchase sees the balance the first one left.</p>
      ${rows.length > 6 ? `<p class="tl-note">Browsers open at most six connections to one server, so a batch of ${rows.length} leaves the browser in waves. The server-side guarantee is the same either way, and the automated tests fire 40 at once from threads.</p>` : ""}
      ${idle > span * 0.15 ? `<p class="tl-note">The empty space between bars is SQLite's busy handler: a waiting request sleeps for a growing interval (1, 2, 5, 10 ms and so on) before trying the lock again. A database with row-level locks, such as Postgres, wakes the next request the moment the lock is free.</p>` : ""}
    </div>`;
  const tl = $(".timeline", target);
  requestAnimationFrame(() => tl.classList.add("play"));
}

function ordinal(n) {
  const s = ["th", "st", "nd", "rd"], v = n % 100;
  return n + (s[(v - 20) % 10] || s[v] || s[0]);
}

async function runRace(accountId, cardId, amounts, mcc) {
  const startCents = (await api(`/accounts/${accountId}`)).balance_cents;
  const results = await fireConcurrently(cardId, amounts, mcc);
  await refresh();
  renderRace($("#race-result"), { startCents, results, finalCents: selected().balance_cents });
  updateContention({ afterRace: true });
}

async function runScenario() {
  const stamp = Date.now().toString(36).slice(-4);
  const acct = await api("/accounts", {
    method: "POST",
    body: { holder_name: `Race demo ${stamp}`, email: `race-${stamp}-${uuid().slice(0, 6)}@example.com`, date_of_birth: "1990-01-01", coverage_type: "self_only" },
  });
  await api(`/accounts/${acct.id}/deposits`, { method: "POST", body: { amount: "100.00", idempotency_key: uuid() } });
  const card = await api(`/accounts/${acct.id}/cards`, { method: "POST", body: {} });
  await refresh();
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
function openNewAccount() { dialog.showModal(); $("[name=holder_name]", dialog).focus(); }

$("#btn-new-account").addEventListener("click", openNewAccount);
$("#btn-cancel-account").addEventListener("click", () => dialog.close());
$("#btn-scenario").addEventListener("click", (e) => busy(e.currentTarget, runScenario));
$("#welcome").addEventListener("click", (e) => {
  const b = e.target.closest("[data-action]");
  if (!b) return;
  if (b.dataset.action === "new-account") openNewAccount();
  else busy(b, runScenario);
});

$("#form-account").addEventListener("submit", (e) => {
  e.preventDefault();
  const form = e.currentTarget;
  busy(form.querySelector("[type=submit]"), async () => {
    const acct = await api("/accounts", { method: "POST", body: Object.fromEntries(new FormData(form)) });
    dialog.close();
    form.holder_name.value = "";
    form.email.value = "";
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
  busy(form.querySelector("[type=submit]"), async () => {
    const dep = await api(`/accounts/${state.selectedId}/deposits`, {
      method: "POST", body: { amount: form.amount.value, idempotency_key: uuid() },
    });
    toast(`Deposited ${usd(dep.amount_cents)}`);
    form.amount.value = "";
    await refresh();
  });
});

$("#card-actions").addEventListener("click", (e) => {
  const btn = e.target.closest("[data-card]");
  if (!btn) return;
  const action = btn.dataset.card;
  const card = selected()?.card;
  busy(btn, async () => {
    if (action === "issue" || action === "replace") {
      if (action === "replace" && !confirm("Cancel this card and issue a new number? The old card will stop working.")) return;
      state.revealedCard = await api(`/accounts/${state.selectedId}/cards`, { method: "POST", body: { replace: action === "replace" } });
      toast(action === "issue" ? "Issued a virtual card" : "Replaced the card. The old one is cancelled.");
    } else {
      await api(`/cards/${card.id}/${action}`, { method: "POST" });
      toast(action === "freeze" ? "Froze the card" : "Unfroze the card");
    }
    await refresh();
  });
});

$("#merchant-presets").addEventListener("click", (e) => {
  const chip = e.target.closest("[data-preset]");
  if (chip) applyPreset(Number(chip.dataset.preset));
});
$("#form-purchase").addEventListener("input", (e) => {
  if (e.target.name !== "amount") document.querySelectorAll("[data-preset]").forEach((b) => b.setAttribute("aria-pressed", "false"));
});

$("#form-purchase").addEventListener("submit", (e) => {
  e.preventDefault();
  const form = e.currentTarget;
  const card = selected()?.card;
  if (!card) { toast("Issue a card before swiping.", "error"); return; }
  busy(form.querySelector("[type=submit]"), async () => {
    const t = await api("/transactions", {
      method: "POST",
      body: { merchant_name: form.merchant_name.value, mcc: form.mcc.value, amount: form.amount.value, card_id: card.id, idempotency_key: uuid() },
    });
    $("#slip-area").innerHTML = slipHtml(t);
    await refresh();
  });
});

$("#form-race").addEventListener("input", () => updateContention());
$("#form-race").addEventListener("change", () => updateContention());
$("#contention").addEventListener("click", (e) => {
  const b = e.target.closest("#btn-make-race");
  if (!b) return;
  const form = $("#form-race");
  form.amounts.value = `${b.dataset.a}, ${b.dataset.b}`;
  form.repeat.value = "1";
  updateContention();
});

$("#form-race").addEventListener("submit", (e) => {
  e.preventDefault();
  const acct = selected();
  if (!acct?.card) { toast("Issue a card before running a race.", "error"); return; }
  const form = e.currentTarget;
  const amounts = raceAmounts();
  if (!amounts.length) { toast("Enter at least one amount, like 80, 50.", "error"); return; }
  busy(form.querySelector("[type=submit]"), () => runRace(acct.id, acct.card.id, amounts, form.mcc.value));
});

$("#activity-body").addEventListener("click", (e) => {
  const b = e.target.closest("[data-trace]");
  if (!b) return;
  const id = b.dataset.trace;
  state.expanded.has(id) ? state.expanded.delete(id) : state.expanded.add(id);
  renderLedger(state.selectedId).catch((err) => toast(err.message, "error"));
});

// ---------------------------------------------------------------- boot

(async function boot() {
  try {
    state.categories = await api("/merchant-categories");
    renderCategorySelect();
    await refresh();
  } catch (err) {
    toast(`Can't reach the server: ${err.message}`, "error");
  }
})();
