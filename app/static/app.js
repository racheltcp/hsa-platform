// HSA Simulator UI. Vanilla JS, no build step. Talks to the JSON API under /api.

const $ = (sel) => document.querySelector(sel);
const state = { accounts: [], selectedId: null, categories: [], revealedCard: null };

const PRESETS = [
  { name: "Corner Pharmacy", mcc: "5912", label: "Pharmacy" },
  { name: "St. Mercy Hospital", mcc: "8062", label: "Hospital" },
  { name: "Bright Smile Dental", mcc: "8021", label: "Dentist" },
  { name: "ClearView Optical", mcc: "8043", label: "Eyeglasses" },
  { name: "Trattoria Roma", mcc: "5812", label: "Restaurant" },
  { name: "Volt Electronics", mcc: "5732", label: "Electronics" },
  { name: "FreshMart Grocery", mcc: "5411", label: "Grocery" },
];

const REASONS = {
  INSUFFICIENT_FUNDS: "Insufficient funds",
  NOT_QUALIFIED_MEDICAL_EXPENSE: "Not a qualified medical expense",
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
const usd = (cents) =>
  (cents / 100).toLocaleString("en-US", { style: "currency", currency: "USD" });
const time = (iso) => new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
const uuid = () => (crypto.randomUUID ? crypto.randomUUID() : String(Math.random()).slice(2) + Date.now());

class ApiError extends Error {
  constructor(status, body) {
    super(body?.error?.message || `Request failed (${status})`);
    this.status = status;
    this.code = body?.error?.code;
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
  setTimeout(() => el.remove(), kind === "error" ? 5000 : 2800);
}

async function withBusy(button, fn) {
  button.disabled = true;
  try { return await fn(); }
  catch (err) { toast(err.message, "error"); }
  finally { button.disabled = false; }
}

function selectAccount(id) {
  state.selectedId = id;
  state.revealedCard = null;
  $("#purchase-result").innerHTML = "";
  $("#race-result").innerHTML = "";
}

const formData = (form) => Object.fromEntries(new FormData(form).entries());
const selected = () => state.accounts.find((a) => a.id === state.selectedId);

// ---------------------------------------------------------------- data loading

async function loadAccounts() {
  state.accounts = await api("/accounts");
  if (!state.accounts.some((a) => a.id === state.selectedId)) {
    state.selectedId = state.accounts[0]?.id ?? null;
  }
  renderAccountList();
  await renderSelected();
}

async function refresh() {
  await loadAccounts();
}

// ---------------------------------------------------------------- rendering

function renderAccountList() {
  $("#account-count").textContent = state.accounts.length ? `${state.accounts.length}` : "";
  const list = $("#account-list");
  if (!state.accounts.length) {
    list.innerHTML = `<li class="empty-list">No accounts yet.</li>`;
    return;
  }
  list.innerHTML = state.accounts.map((a) => `
    <li><button data-id="${esc(a.id)}" class="${a.id === state.selectedId ? "active" : ""}">
      <span class="name">${esc(a.holder_name)}</span>
      <span class="bal">${usd(a.balance_cents)}</span>
      <span class="sub">${esc(a.email)}</span>
      <span class="sub">${a.card ? (a.card.status === "frozen" ? "card frozen" : "•••• " + esc(a.card.last4)) : "no card"}</span>
    </button></li>`).join("");
}

async function renderSelected() {
  const acct = selected();
  $("#empty-state").hidden = !!acct;
  $("#account-view").hidden = !acct;
  if (!acct) return;

  $("#acct-id").textContent = acct.id;
  $("#acct-name").textContent = acct.holder_name;
  $("#acct-meta").textContent =
    `${acct.email} · ${acct.coverage_type === "family" ? "Family" : "Self-only"} coverage · born ${acct.date_of_birth}`;
  $("#acct-balance").textContent = usd(acct.balance_cents);

  const c = acct.contributions;
  $("#limit-label").textContent = `${c.tax_year} contributions`;
  $("#limit-bar").style.width = `${Math.min(100, (c.contributed_cents / c.annual_limit_cents) * 100)}%`;
  $("#limit-text").textContent =
    `${usd(c.contributed_cents)} of ${usd(c.annual_limit_cents)} IRS limit · ${usd(c.remaining_cents)} remaining`;

  renderCard(acct);
  await renderActivity(acct.id);
}

function renderCard(acct) {
  const card = acct.card;
  const visual = $("#card-visual");
  const actions = $("#card-actions");
  const once = $("#card-once");
  const revealed = state.revealedCard && card && state.revealedCard.id === card.id ? state.revealedCard : null;

  if (!card) {
    visual.className = "card-visual none";
    visual.innerHTML = `<span>No card issued</span>`;
    $("#card-status-chip").innerHTML = "";
    actions.innerHTML = `<button class="btn primary" data-card-action="issue">Issue virtual card</button>`;
    once.hidden = true;
    return;
  }
  visual.className = `card-visual ${card.status === "frozen" ? "frozen" : ""}`;
  visual.innerHTML = `
    <div class="card-top"><span>HSA Debit</span><span>${card.status === "frozen" ? "Frozen" : "Virtual"}</span></div>
    <div class="card-number">${esc(revealed ? revealed.number : card.masked_number)}</div>
    <div class="card-bottom"><span>${esc(acct.holder_name)}</span><span>Exp ${String(card.exp_month).padStart(2, "0")}/${String(card.exp_year).slice(-2)}</span></div>`;
  $("#card-status-chip").innerHTML =
    `<span class="pill ${card.status === "active" ? "ok" : "neutral"}">${esc(card.status)}</span>`;
  once.hidden = !revealed;
  once.textContent = "Full card number is shown once at issuance. Only the last 4 digits are stored.";
  actions.innerHTML = `
    ${card.status === "active"
      ? `<button class="btn" data-card-action="freeze">Freeze card</button>`
      : `<button class="btn" data-card-action="unfreeze">Unfreeze card</button>`}
    <button class="btn danger" data-card-action="replace">Cancel &amp; replace</button>`;
}

async function renderActivity(accountId) {
  const items = await api(`/accounts/${accountId}/activity`);
  const body = $("#activity-body");
  if (!items.length) {
    body.innerHTML = `<tr class="empty-row"><td colspan="6">No activity yet. Deposit funds to get started.</td></tr>`;
    return;
  }
  body.innerHTML = items.map((i) => {
    if (i.kind === "deposit") {
      return `<tr>
        <td>${time(i.created_at)}</td><td>Deposit</td>
        <td>Contribution <span class="sub">· tax year ${i.tax_year}</span></td>
        <td><span class="pill info">credited</span></td>
        <td class="num">+${usd(i.amount_cents)}</td><td class="num">${usd(i.balance_after_cents)}</td></tr>`;
    }
    const ok = i.status === "approved";
    return `<tr>
      <td>${time(i.created_at)}</td><td>Purchase</td>
      <td>${esc(i.merchant_name)}<div class="sub">MCC ${esc(i.mcc)} · ${esc(i.merchant_category)}</div></td>
      <td><span class="pill ${ok ? "ok" : "bad"}">${ok ? "approved" : "declined"}</span>
        ${ok ? "" : `<div class="sub">${esc(REASONS[i.decline_reason] || i.decline_reason)}</div>`}</td>
      <td class="num">${ok ? "−" : ""}${usd(i.amount_cents)}</td><td class="num">${usd(i.balance_after_cents)}</td></tr>`;
  }).join("");
}

function renderCategorySelect() {
  const groups = [
    ["Qualified medical", state.categories.filter((c) => c.qualified)],
    ["Not qualified", state.categories.filter((c) => !c.qualified)],
  ];
  $("#mcc-select").innerHTML = groups.map(([label, cats]) =>
    `<optgroup label="${label}">${cats.map((c) =>
      `<option value="${c.mcc}">${c.mcc} · ${esc(c.label)}</option>`).join("")}</optgroup>`).join("") +
    `<optgroup label="Other"><option value="1234">1234 · Unknown category</option></optgroup>`;

  const qualified = new Set(state.categories.filter((c) => c.qualified).map((c) => c.mcc));
  $("#merchant-presets").innerHTML = PRESETS.map((p, i) =>
    `<button type="button" class="chip" data-preset="${i}">
       <span class="dot ${qualified.has(p.mcc) ? "ok" : "bad"}"></span>${esc(p.label)}</button>`).join("");
  applyPreset(0);
}

function applyPreset(i) {
  const p = PRESETS[i];
  const form = $("#form-purchase");
  form.merchant_name.value = p.name;
  form.mcc.value = p.mcc;
}

function txnResultHtml(t) {
  const ok = t.status === "approved";
  return `<div class="result ${ok ? "ok" : "bad"}">
    <span class="pill ${ok ? "ok" : "bad"}">${ok ? "APPROVED" : "DECLINED"}</span>
    <strong>${usd(t.amount_cents)} at ${esc(t.merchant_name)}</strong>
    <span class="muted">${ok ? "" : esc(REASONS[t.decline_reason] || t.decline_reason) + " · "}balance now ${usd(t.balance_after_cents)}</span>
  </div>`;
}

// ---------------------------------------------------------------- concurrency

/** Fire all purchases at once (Promise.all, no awaiting in between). */
async function fireConcurrently(cardId, amounts, mcc) {
  const merchant = mcc === "5812" ? "Trattoria Roma" : mcc === "8062" ? "St. Mercy Hospital" : "Corner Pharmacy";
  const started = performance.now();
  const results = await Promise.all(amounts.map((amount, idx) =>
    api("/transactions", {
      method: "POST",
      body: { card_id: cardId, amount, mcc, merchant_name: `${merchant} #${idx + 1}`, idempotency_key: uuid() },
    }).then((t) => ({ ok: true, t, idx }), (e) => ({ ok: false, e, idx, amount }))));
  return { results, ms: Math.round(performance.now() - started) };
}

function renderRace(target, { startCents, results, ms, finalCents }) {
  const txns = results.filter((r) => r.ok).map((r) => r.t);
  const errors = results.filter((r) => !r.ok);
  const approved = txns.filter((t) => t.status === "approved");
  const spent = approved.reduce((s, t) => s + t.amount_cents, 0);
  const consistent = finalCents === startCents - spent && finalCents >= 0;
  const bySettlement = [...txns].sort((a, b) =>
    b.balance_after_cents - a.balance_after_cents || a.created_at.localeCompare(b.created_at));

  target.innerHTML = `
    <div class="race-summary">
      <div><span class="eyebrow">Sent at once</span><b>${results.length}</b></div>
      <div><span class="eyebrow">Approved</span><b>${approved.length}</b></div>
      <div><span class="eyebrow">Declined</span><b>${txns.length - approved.length}</b></div>
      <div><span class="eyebrow">Starting balance</span><b>${usd(startCents)}</b></div>
      <div><span class="eyebrow">Approved total</span><b>${usd(spent)}</b></div>
      <div><span class="eyebrow">Final balance</span><b>${usd(finalCents)}</b></div>
      <div><span class="eyebrow">Wall time</span><b>${ms} ms</b></div>
    </div>
    <p class="verdict ${consistent && !errors.length ? "ok" : "bad"}">
      ${consistent && !errors.length
        ? `✓ Ledger consistent: ${usd(startCents)} − ${usd(spent)} = ${usd(finalCents)}, never negative.`
        : `✗ Inconsistent result. ${errors.length} request error(s).`}
    </p>
    <div class="table-wrap"><table>
      <thead><tr><th>Request</th><th class="num">Amount</th><th>Result</th><th class="num">Balance after</th></tr></thead>
      <tbody>${bySettlement.map((t) => `<tr>
        <td>${esc(t.merchant_name)}</td><td class="num">${usd(t.amount_cents)}</td>
        <td><span class="pill ${t.status === "approved" ? "ok" : "bad"}">${t.status}</span>
          ${t.decline_reason ? `<span class="sub"> ${esc(REASONS[t.decline_reason])}</span>` : ""}</td>
        <td class="num">${usd(t.balance_after_cents)}</td></tr>`).join("")}
        ${errors.map((r) => `<tr><td>Request ${r.idx + 1}</td><td class="num">${esc(r.amount)}</td>
          <td colspan="2"><span class="pill bad">error</span> ${esc(r.e.message)}</td></tr>`).join("")}
      </tbody></table></div>
    <p class="muted small" style="margin-top:8px">Rows ordered by the sequence in which the server committed them (highest balance-after first).</p>`;
}

async function runSpecScenario() {
  const stamp = Date.now().toString(36);
  const acct = await api("/accounts", {
    method: "POST",
    body: { holder_name: `Race Demo ${stamp}`, email: `race-${stamp}@example.com`, date_of_birth: "1990-01-01", coverage_type: "self_only" },
  });
  await api(`/accounts/${acct.id}/deposits`, { method: "POST", body: { amount: "100.00" } });
  const card = await api(`/accounts/${acct.id}/cards`, { method: "POST", body: {} });
  selectAccount(acct.id);
  const { results, ms } = await fireConcurrently(card.id, ["80.00", "50.00"], "5912");
  await refresh();
  const final = selected();
  $("#form-race").amounts.value = "80, 50";
  $("#form-race").repeat.value = "1";
  renderRace($("#race-result"), { startCents: 10000, results, ms, finalCents: final.balance_cents });
  $("#race-result").closest(".panel").scrollIntoView({ behavior: "smooth", block: "start" });
}

// ---------------------------------------------------------------- events

$("#form-account").addEventListener("submit", (e) => {
  e.preventDefault();
  const form = e.target;
  withBusy(form.querySelector("button"), async () => {
    const acct = await api("/accounts", { method: "POST", body: formData(form) });
    selectAccount(acct.id);
    form.holder_name.value = "";
    form.email.value = "";
    toast(`Account created for ${acct.holder_name}`);
    await refresh();
  });
});

$("#account-list").addEventListener("click", (e) => {
  const btn = e.target.closest("button[data-id]");
  if (!btn) return;
  selectAccount(btn.dataset.id);
  renderAccountList();
  renderSelected().catch((err) => toast(err.message, "error"));
});

$("#deposit-quick").addEventListener("click", (e) => {
  const chip = e.target.closest("[data-amount]");
  if (chip) $("#form-deposit").amount.value = chip.dataset.amount;
});

$("#form-deposit").addEventListener("submit", (e) => {
  e.preventDefault();
  const form = e.target;
  withBusy(form.querySelector("button[type=submit]"), async () => {
    const dep = await api(`/accounts/${state.selectedId}/deposits`, {
      method: "POST", body: { amount: form.amount.value, idempotency_key: uuid() },
    });
    toast(`Deposited ${usd(dep.amount_cents)}`);
    form.amount.value = "";
    await refresh();
  });
});

$("#card-actions").addEventListener("click", (e) => {
  const btn = e.target.closest("[data-card-action]");
  if (!btn) return;
  const action = btn.dataset.cardAction;
  const card = selected()?.card;
  withBusy(btn, async () => {
    if (action === "issue" || action === "replace") {
      if (action === "replace" && !confirm("Cancel the current card and issue a new number?")) return;
      state.revealedCard = await api(`/accounts/${state.selectedId}/cards`, {
        method: "POST", body: { replace: action === "replace" },
      });
      toast(action === "issue" ? "Virtual card issued" : "Card replaced; old card cancelled");
    } else {
      await api(`/cards/${card.id}/${action}`, { method: "POST" });
      toast(action === "freeze" ? "Card frozen" : "Card active");
    }
    await refresh();
  });
});

$("#merchant-presets").addEventListener("click", (e) => {
  const chip = e.target.closest("[data-preset]");
  if (chip) applyPreset(Number(chip.dataset.preset));
});

$("#form-purchase").addEventListener("submit", (e) => {
  e.preventDefault();
  const form = e.target;
  const card = selected()?.card;
  if (!card) { toast("Issue a card first", "error"); return; }
  withBusy(form.querySelector("button"), async () => {
    const t = await api("/transactions", {
      method: "POST",
      body: { ...formData(form), card_id: card.id, idempotency_key: uuid() },
    });
    $("#purchase-result").innerHTML = txnResultHtml(t);
    await refresh();
  });
});

$("#form-race").addEventListener("submit", (e) => {
  e.preventDefault();
  const form = e.target;
  const acct = selected();
  if (!acct?.card) { toast("Issue a card first", "error"); return; }
  const base = form.amounts.value.split(",").map((s) => s.trim()).filter(Boolean);
  const repeat = Math.max(1, Math.min(50, Number(form.repeat.value) || 1));
  const amounts = Array.from({ length: repeat }, () => base).flat();
  if (!amounts.length) { toast("Enter at least one amount", "error"); return; }
  withBusy(form.querySelector("button"), async () => {
    const startCents = (await api(`/accounts/${acct.id}`)).balance_cents;
    const { results, ms } = await fireConcurrently(acct.card.id, amounts, form.mcc.value);
    await refresh();
    renderRace($("#race-result"), { startCents, results, ms, finalCents: selected().balance_cents });
  });
});

$("#btn-scenario").addEventListener("click", (e) => withBusy(e.currentTarget, runSpecScenario));

// ---------------------------------------------------------------- boot

(async function boot() {
  try {
    state.categories = await api("/merchant-categories");
    renderCategorySelect();
    await refresh();
  } catch (err) {
    toast(`Could not reach the server: ${err.message}`, "error");
  }
})();
