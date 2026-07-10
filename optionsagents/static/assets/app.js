/* global fetch, navigator, document, window, localStorage, setInterval, alert, confirm */

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];

const fmtUsd = (v) => {
  if (v == null || Number.isNaN(v)) return "–";
  const n = Number(v);
  return `${n < 0 ? "−$" : "$"}${Math.abs(n).toLocaleString(undefined, { maximumFractionDigits: 2 })}`;
};

const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

const pnlHtml = (v) => {
  if (v == null) return '<span class="muted">–</span>';
  const n = Number(v);
  const cls = n > 0 ? "pos" : n < 0 ? "neg" : "muted";
  const sign = n > 0 ? "+" : n < 0 ? "−" : "";
  return `<span class="${cls}">${sign}$${Math.abs(n).toLocaleString(undefined, { maximumFractionDigits: 2 })}</span>`;
};

async function api(path, opts = {}) {
  const res = await fetch(path, { credentials: "same-origin", ...opts });
  if (res.status === 401 && !path.includes("/auth/")) {
    window.location.href = "/login";
    throw new Error("sign in required");
  }
  if (!res.ok) {
    let msg = res.statusText;
    try {
      const j = await res.json();
      msg = j.detail || msg;
      if (Array.isArray(msg)) msg = msg.map((e) => e.msg || JSON.stringify(e)).join("; ");
    } catch (_) { /* ignore */ }
    throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  if (res.status === 204) return null;
  return res.json();
}

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    toast("Copied to clipboard");
  } catch (_) {
    prompt("Copy:", text);
  }
}

function toast(msg) {
  const el = $("#toast");
  if (!el) return alert(msg);
  el.textContent = msg;
  el.classList.remove("hidden");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => el.classList.add("hidden"), 2200);
}

function legStr(legs) {
  return (legs || []).map((l) =>
    `${l.action === "buy" ? "+" : "−"}${l.contracts} ${l.expiry} ${l.strike} ${String(l.right).toUpperCase()[0]}`
  ).join(", ");
}

function renderTiles(acct, root = "#home-tiles") {
  const closed = window._closed || [];
  const wins = closed.filter((p) => (p.realized_pnl ?? 0) > 0).length;
  const winRate = closed.length ? `${Math.round((100 * wins) / closed.length)}%` : "–";
  const el = $(root);
  if (!el) return;
  el.innerHTML = `
    <div class="tile"><div class="label">Equity</div><div class="value">${fmtUsd(acct.equity)}</div><div class="sub">from ${fmtUsd(acct.starting_cash)}</div></div>
    <div class="tile"><div class="label">Cash</div><div class="value">${fmtUsd(acct.cash)}</div></div>
    <div class="tile"><div class="label">Unrealized</div><div class="value">${pnlHtml(acct.unrealized_pnl)}</div><div class="sub">${acct.open_positions || 0} open</div></div>
    <div class="tile"><div class="label">Realized</div><div class="value">${pnlHtml(acct.realized_pnl)}</div><div class="sub">win ${winRate}</div></div>`;
}

function renderPositions(positions) {
  const open = (positions || []).filter((p) => p.status === "open");
  const closed = (positions || []).filter((p) => p.status === "closed").reverse();
  window._closed = closed;

  const openEmpty = $("#open-empty");
  const closedEmpty = $("#closed-empty");
  if (openEmpty) openEmpty.classList.toggle("hidden", open.length > 0);
  if (closedEmpty) closedEmpty.classList.toggle("hidden", closed.length > 0);

  const openBody = $("#open-table tbody");
  if (openBody) {
    openBody.innerHTML = open.map((p) => `
      <tr>
        <td><strong>${esc(p.underlying)}</strong><br><span class="muted">${esc(p.mode)}</span></td>
        <td>${esc(p.strategy)}</td>
        <td>${esc(legStr(p.legs))}</td>
        <td>${esc(p.price_type)} $${Number(p.entry_net).toFixed(2)}</td>
        <td>${p.last_mark != null ? `$${Number(p.last_mark).toFixed(2)}` : "–"}</td>
        <td>${pnlHtml(p.unrealized_pnl)}</td>
        <td>${fmtUsd(p.max_risk)}</td>
        <td><button class="btn sm danger" data-close="${esc(p.id)}">Close</button></td>
      </tr>`).join("");
  }

  const openCards = $("#open-cards");
  if (openCards) {
    openCards.innerHTML = open.map((p) => `
      <div class="pos-card">
        <div class="row"><strong>${esc(p.underlying)}</strong>${pnlHtml(p.unrealized_pnl)}</div>
        <div class="row"><span class="muted">${esc(p.strategy)} · ${esc(p.mode)}</span><span>${fmtUsd(p.max_risk)} risk</span></div>
        <div class="muted" style="font-size:12px;margin-bottom:8px">${esc(legStr(p.legs))}</div>
        <button class="btn sm danger block" data-close="${esc(p.id)}">Close position</button>
      </div>`).join("");
  }

  const closedBody = $("#closed-table tbody");
  if (closedBody) {
    closedBody.innerHTML = closed.map((p) => `
      <tr>
        <td><strong>${esc(p.underlying)}</strong></td>
        <td>${esc(p.strategy)}</td>
        <td class="muted">${esc((p.opened_at || "").slice(0, 16).replace("T", " "))}</td>
        <td class="muted">${esc((p.closed_at || "").slice(0, 16).replace("T", " "))}</td>
        <td>${pnlHtml(p.realized_pnl)}</td>
        <td>${esc(p.exit_reason || "")}</td>
      </tr>`).join("");
  }
}

function renderStrategies(engine) {
  const strats = engine?.strategies || [];
  const empty = $("#strat-empty");
  if (empty) empty.classList.toggle("hidden", strats.length > 0);
  const tbody = $("#strat-table tbody");
  if (!tbody) return;
  tbody.innerHTML = strats.map((s) => `
    <tr>
      <td><strong>${esc(s.ticker)}</strong></td>
      <td>${esc(s.signal)}</td>
      <td>${esc(s.mode)}</td>
      <td class="muted">${esc(s.schedule)}</td>
      <td class="muted">${s.last_run ? esc(s.last_run.slice(0, 16).replace("T", " ")) : "never"}</td>
      <td>${esc(s.last_result || "–")}</td>
      <td>${s.running ? "running" : s.enabled ? "active" : "paused"}</td>
      <td>
        <div class="btn-row">
          <button class="btn sm" data-run="${esc(s.id)}" ${s.running ? "disabled" : ""}>Run</button>
          <button class="btn sm" data-toggle="${esc(s.id)}">${s.enabled ? "Pause" : "Resume"}</button>
          <button class="btn sm danger" data-del="${esc(s.id)}">Del</button>
        </div>
      </td>
    </tr>`).join("");
}

function renderAutonomous(auto) {
  if (!auto) return;
  const enabled = auto.enabled;
  $("#pill-auto")?.classList.toggle("on", enabled);
  const btn = $("#btn-auto-toggle");
  if (btn) {
    btn.textContent = enabled ? "Pause AI brain" : "Enable AI brain";
    btn.classList.toggle("primary", !enabled);
  }
  const risk = auto.risk || {};
  const cfg = auto.config || {};
  const tiles = $("#auto-tiles");
  if (tiles) {
    tiles.innerHTML = `
      <div class="tile"><div class="label">Universe</div><div class="value">${cfg.universe_size ?? "–"}</div><div class="sub">top ${cfg.scan_top_n ?? "–"}</div></div>
      <div class="tile"><div class="label">Cycle</div><div class="value">${cfg.cycle_interval_minutes ?? "–"}m</div><div class="sub">${auto.due ? "due" : auto.cycle_running ? "running" : "wait"}</div></div>
      <div class="tile"><div class="label">Loss room</div><div class="value">${fmtUsd(risk.daily_loss_remaining)}</div></div>
      <div class="tile"><div class="label">Open risk</div><div class="value">${fmtUsd(risk.total_open_risk)}</div></div>`;
  }
  const st = $("#auto-status");
  if (st) {
    let txt = auto.last_cycle ? `Last cycle ${auto.last_cycle.slice(0, 16).replace("T", " ")}` : "No cycles yet.";
    if (auto.last_result) txt += ` · opened ${auto.last_result.trades_opened}`;
    if (risk.kill_switch_active) txt = "Kill switch active — daily loss limit hit.";
    st.textContent = txt;
  }
  const feed = $("#auto-feed");
  if (feed) {
    const events = auto.events || [];
    feed.innerHTML = events.length
      ? events.map((e) => `<div class="ev"><time>${esc(e.time)}</time><span class="kind">${esc(e.kind)}</span><span>${esc(e.message)}</span></div>`).join("")
      : '<div class="empty">Enable the AI brain to start autonomous scanning.</div>';
  }
}

function renderFeed(engine, journal, autonomous) {
  const feed = $("#activity-feed");
  if (!feed) return;
  const events = [];
  (engine?.events || []).forEach((e) => events.push({ time: e.time, kind: e.kind, msg: e.message }));
  (autonomous?.events || []).forEach((e) => events.push({ time: e.time, kind: `auto:${e.kind}`, msg: e.message }));
  (journal || []).forEach((j) => {
    const { time, event, ...rest } = j;
    const detail = Object.entries(rest).map(([k, v]) => `${k}=${Array.isArray(v) ? v.join("|") : v}`).join(", ");
    events.push({ time: (time || "").replace("T", " ").replace("Z", ""), kind: event, msg: detail });
  });
  feed.innerHTML = events.slice(0, 60).map((e) =>
    `<div class="ev"><time>${esc(e.time)}</time><span class="kind">${esc(e.kind)}</span><span>${esc(e.msg)}</span></div>`
  ).join("") || '<div class="empty">No activity yet.</div>';
}

function renderTradingView(setup, user) {
  if (!setup) return;
  $("#tv-username").textContent = user?.tradingview_username || "Not linked";
  $("#tv-status").textContent = user?.tv_connected ? "Connected" : "Setup required";
  $("#tv-status").className = user?.tv_connected ? "pill on" : "pill";
  $("#tv-webhook-url").textContent = user?.webhook_url || setup.user?.webhook_url || "";
  $("#tv-secret").textContent = setup.webhook_secret || "—";
  $("#pine-day").textContent = setup.pine_day || "";
  $("#pine-swing").textContent = setup.pine_swing || "";
  const steps = $("#tv-steps");
  if (steps) {
    steps.innerHTML = (setup.steps || []).map((s) => `
      <div class="step">
        <h3>${s.step}. ${esc(s.title)}</h3>
        <p>${esc(s.body)}</p>
        ${s.code ? `<div class="codebox" style="margin-top:8px">${esc(s.code)}</div>` : ""}
        ${s.link ? `<p style="margin-top:6px"><a href="${esc(s.link)}" target="_blank" rel="noopener">Open TradingView ↗</a></p>` : ""}
      </div>`).join("");
  }
}

function renderAccount(user) {
  if (!user) return;
  $("#acct-email").textContent = user.email;
  $("#acct-name").textContent = user.display_name || "—";
  $("#acct-tv").textContent = user.tradingview_username || "Not set";
  $("#acct-tv-status").textContent = user.tv_connected ? "Connected" : "Not connected";
}

async function refreshApp() {
  const s = await api("/api/state");
  window._state = s;
  renderPositions(s.positions);
  renderTiles(s.account);
  renderStrategies(s.engine);
  renderAutonomous(s.autonomous);
  renderFeed(s.engine, s.journal, s.autonomous);
  renderAccount(s.user);
  $("#pill-engine")?.classList.toggle("on", s.engine?.running);
  $("#pill-market")?.classList.toggle("on", s.engine?.market_open);
  $("#pill-updated").textContent = `Updated ${new Date().toLocaleTimeString()}`;
  const banner = $("#tv-banner");
  if (banner) banner.classList.toggle("hidden", !!s.user?.tv_connected);
}

async function refreshTradingView() {
  const setup = await api("/api/tradingview/setup");
  renderTradingView(setup, setup.user);
}

function showPanel(name) {
  $$(".panel").forEach((p) => p.classList.toggle("active", p.dataset.panel === name));
  $$(".nav-btn").forEach((b) => b.classList.toggle("active", b.dataset.nav === name));
  if (name === "tv") refreshTradingView();
  if (name === "account") renderAccount(window._state?.user);
}

async function initApp() {
  try {
    await api("/api/auth/me");
  } catch (_) {
    window.location.href = "/login";
    return;
  }
  showPanel("home");
  await refreshApp();
  if (!window._tvLoaded) {
    window._tvLoaded = true;
    try { await refreshTradingView(); } catch (_) { /* optional */ }
  }
  setInterval(refreshApp, 5000);
  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("/sw.js").catch(() => {});
  }
}

function bindAppEvents() {
  $$(".nav-btn").forEach((btn) => btn.addEventListener("click", () => showPanel(btn.dataset.nav)));

  $("#add-form")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = new FormData(e.target);
    await api("/api/strategies", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        ticker: String(f.get("ticker")).trim().toUpperCase(),
        signal: f.get("signal"),
        mode: f.get("mode"),
        trigger: f.get("trigger"),
        run_time: f.get("run_time") || "10:00",
        interval_minutes: parseInt(f.get("interval_minutes") || "60", 10),
      }),
    });
    e.target.reset();
    refreshApp();
  });

  $("#trigger-select")?.addEventListener("change", (e) => {
    $("#lbl-time")?.classList.toggle("hidden", e.target.value !== "daily");
    $("#lbl-interval")?.classList.toggle("hidden", e.target.value !== "interval");
  });

  document.body.addEventListener("click", async (e) => {
    const t = e.target.closest("[data-close],[data-run],[data-toggle],[data-del]");
    if (!t) return;
    if (t.dataset.close && confirm("Close at current mid?")) {
      const r = await api(`/positions/${t.dataset.close}/close`, { method: "POST" });
      toast(`Closed · P&L ${fmtUsd(r.pnl)}`);
      refreshApp();
    }
    if (t.dataset.run) { await api(`/api/strategies/${t.dataset.run}/run`, { method: "POST" }); refreshApp(); }
    if (t.dataset.toggle) { await api(`/api/strategies/${t.dataset.toggle}/toggle`, { method: "POST" }); refreshApp(); }
    if (t.dataset.del && confirm("Delete strategy?")) {
      await api(`/api/strategies/${t.dataset.del}`, { method: "DELETE" });
      refreshApp();
    }
  });

  $("#btn-auto-toggle")?.addEventListener("click", async () => {
    await api("/api/autonomous/toggle", { method: "POST" });
    refreshApp();
  });
  $("#btn-auto-run")?.addEventListener("click", async () => {
    await api("/api/autonomous/run", { method: "POST" });
    toast("Cycle started");
    refreshApp();
  });

  $("#btn-copy-url")?.addEventListener("click", () => copyText($("#tv-webhook-url")?.textContent || ""));
  $("#btn-copy-secret")?.addEventListener("click", () => copyText($("#tv-secret")?.textContent || ""));
  $("#btn-copy-pine-day")?.addEventListener("click", () => copyText($("#pine-day")?.textContent || ""));
  $("#btn-copy-pine-swing")?.addEventListener("click", () => copyText($("#pine-swing")?.textContent || ""));

  $("#tv-connect-form")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = new FormData(e.target);
    const r = await api("/api/tradingview/connect", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        tradingview_username: String(f.get("tradingview_username")).trim(),
        confirm: !!f.get("confirm"),
      }),
    });
    toast(r.user.tv_connected ? "TradingView connected" : "Username saved");
    refreshTradingView();
    refreshApp();
  });

  $("#btn-regen-secret")?.addEventListener("click", async () => {
    if (!confirm("Regenerate webhook secret? Update your Pine scripts and TradingView alerts.")) return;
    const r = await api("/api/tradingview/regenerate-secret", { method: "POST" });
    renderTradingView(r, r.user);
    toast("New secret generated");
  });

  $("#btn-logout")?.addEventListener("click", async () => {
    await api("/api/auth/logout", { method: "POST" });
    window.location.href = "/login";
  });
}

async function initAuthPage(kind) {
  const form = $("#auth-form");
  const err = $("#auth-error");
  form?.addEventListener("submit", async (e) => {
    e.preventDefault();
    err.textContent = "";
    const f = new FormData(form);
    const body = {
      email: String(f.get("email")).trim(),
      password: String(f.get("password")),
      display_name: String(f.get("display_name") || "").trim(),
    };
    try {
      await api(kind === "signup" ? "/api/auth/signup" : "/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      window.location.href = "/app";
    } catch (ex) {
      err.textContent = ex.message;
    }
  });
  try {
    await api("/api/auth/me");
    window.location.href = "/app";
  } catch (_) { /* stay on auth page */ }
}

window.App = { initApp, initAuthPage, bindAppEvents, api, refreshApp };
