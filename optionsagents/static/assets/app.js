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
    const onAuthPage = ["/login", "/signup"].some((p) => window.location.pathname.startsWith(p));
    if (!onAuthPage) {
      sessionStorage.setItem("oa_auth_redirect", "1");
      window.location.href = "/login";
    }
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
  toast._t = setTimeout(() => el.classList.add("hidden"), 4200);
}

async function checkPersistenceBanner() {
  try {
    const h = await fetch("/health").then((r) => r.json());
    const warn = h.persistence?.warning;
    const banner = $("#signals-banner");
    if (warn && banner) {
      banner.textContent = warn;
      banner.classList.remove("hidden");
    }
  } catch (_) { /* ignore */ }
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

function orderForPosition(positionId, orders) {
  const list = orders || window._orders || [];
  return list.find((o) => o.position_id === positionId);
}

function whyButtonHtml(positionId, orders) {
  if (!orderForPosition(positionId, orders)) return "";
  return `<button class="btn sm" type="button" data-why-position="${esc(positionId)}">Why?</button>`;
}

function renderPositions(positions, orders) {
  const orderList = orders ?? window._orders ?? [];
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
        <td><div class="btn-row">${whyButtonHtml(p.id, orderList)}<button class="btn sm danger" data-close="${esc(p.id)}">Close</button></div></td>
      </tr>`).join("");
  }

  const openCards = $("#open-cards");
  if (openCards) {
    openCards.innerHTML = open.map((p) => `
      <div class="pos-card">
        <div class="row"><strong>${esc(p.underlying)}</strong>${pnlHtml(p.unrealized_pnl)}</div>
        <div class="row"><span class="muted">${esc(p.strategy)} · ${esc(p.mode)}</span><span>${fmtUsd(p.max_risk)} risk</span></div>
        <div class="muted" style="font-size:12px;margin-bottom:8px">${esc(legStr(p.legs))}</div>
        <div class="btn-row">
          ${whyButtonHtml(p.id, orderList)}
          <button class="btn sm danger" data-close="${esc(p.id)}">Close position</button>
        </div>
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
        <td>${whyButtonHtml(p.id, orderList)}</td>
      </tr>`).join("");
  }
}

function statusBadge(status) {
  return `<span class="badge ${esc(status)}">${esc(status)}</span>`;
}

function renderOrders(orders) {
  window._orders = orders || [];
  const list = $("#order-list");
  const empty = $("#orders-empty");
  if (!list) return;
  if (empty) empty.classList.toggle("hidden", window._orders.length > 0);
  list.innerHTML = window._orders.map((o) => `
    <button class="order-row" type="button" data-order="${esc(o.id)}">
      <div class="top"><span class="ticker">${esc(o.ticker)}</span>${statusBadge(o.status)}</div>
      <div class="meta">${esc(o.source_label)} · ${esc(o.signal)} · ${esc(o.mode)} · ${esc((o.created_at || "").slice(0, 16).replace("T", " "))}</div>
      <div class="summary">${esc(o.trigger_summary)}</div>
      ${o.realized_pnl != null ? `<div class="meta" style="margin-top:6px">Closed P&amp;L ${pnlHtml(o.realized_pnl)}</div>` : ""}
    </button>`).join("");
}

function kvBlock(heading, obj) {
  const rows = Object.entries(obj || {})
    .filter(([, v]) => v !== null && v !== undefined && v !== "")
    .map(([k, v]) => `<dt>${esc(k.replaceAll("_", " "))}</dt><dd>${esc(Array.isArray(v) ? v.join(", ") : v)}</dd>`);
  if (!rows.length) return "";
  return `<div class="detail-block"><h3>${esc(heading)}</h3><dl class="kv">${rows.join("")}</dl></div>`;
}

function showOrderDetail(order) {
  const sheet = $("#order-detail");
  const body = $("#order-detail-body");
  if (!sheet || !body || !order) return;
  const title = $("#order-detail-title");
  if (title) title.textContent = `${order.ticker} — ${order.source_label}`;
  const blocks = [
    ["Why this happened", order.teach_summary],
    ["Trigger", order.trigger_summary],
    ["Setup / signal", order.source_rationale],
    ["Research context", order.decision_context],
    ["Options plan", order.plan_rationale],
  ].filter(([, text]) => text && String(text).trim());

  body.innerHTML = `
    <div class="detail-grid">
      <div class="tile"><div class="label">Status</div><div class="value" style="font-size:16px">${esc(order.status)}</div></div>
      <div class="tile"><div class="label">Source</div><div class="value" style="font-size:16px">${esc(order.source_label)}</div></div>
      <div class="tile"><div class="label">Signal</div><div class="value" style="font-size:16px">${esc(order.signal)} / ${esc(order.mode)}</div></div>
      <div class="tile"><div class="label">Risk</div><div class="value" style="font-size:16px">${order.max_risk != null ? fmtUsd(order.max_risk) : "–"}</div></div>
    </div>
    ${blocks.map(([h, t]) => `
      <div class="detail-block"><h3>${esc(h)}</h3><p>${esc(t)}</p></div>`).join("")}
    ${kvBlock("Market conditions at entry", order.chain_conditions)}
    ${kvBlock("Risk rules applied", order.mode_rules)}
    ${order.strategy_education ? `<div class="detail-block"><h3>🎓 What is a ${esc(String(order.strategy || "").replaceAll("_", " "))}?</h3><p class="learn-box">${esc(order.strategy_education)}</p></div>` : ""}
    ${order.warnings?.length ? `<div class="detail-block"><h3>Notes</h3><p>${order.warnings.map((w) => esc(w)).join("<br>")}</p></div>` : ""}
    ${order.exit_reason ? `<div class="detail-block"><h3>Exit</h3><p>Closed via ${esc(order.exit_reason)}${order.realized_pnl != null ? ` · P&amp;L ${fmtUsd(order.realized_pnl)}` : ""}</p></div>` : ""}`;
  sheet.classList.remove("hidden");
}

function closeOrderDetail() {
  $("#order-detail")?.classList.add("hidden");
}

function renderStats(stats) {
  if (!stats) return;
  const tiles = $("#stats-tiles");
  if (tiles) {
    tiles.innerHTML = `
      <div class="tile"><div class="label">Closed trades</div><div class="value">${stats.total_closed ?? 0}</div><div class="sub">win ${stats.win_rate_pct ?? 0}%</div></div>
      <div class="tile"><div class="label">Realized P&amp;L</div><div class="value">${pnlHtml(stats.realized_pnl)}</div></div>
      <div class="tile"><div class="label">Expectancy</div><div class="value">${pnlHtml(stats.expectancy)}</div><div class="sub">per trade</div></div>
      <div class="tile"><div class="label">Max drawdown</div><div class="value">${fmtUsd(stats.max_drawdown)}</div><div class="sub">${stats.max_drawdown_pct ?? 0}%</div></div>
      <div class="tile"><div class="label">Avg win</div><div class="value">${pnlHtml(stats.avg_win)}</div><div class="sub">avg loss ${fmtUsd(stats.avg_loss)}</div></div>
      <div class="tile"><div class="label">Skipped</div><div class="value">${stats.skipped_orders ?? 0}</div><div class="sub">risk/earnings/regime blocks</div></div>`;
  }
  const sourceLabels = {
    free_signal: "Free signals",
    autonomous: "Autonomous AI",
    tradingview: "TradingView",
    strategy: "Scheduled plans",
    unknown: "Other",
  };
  const renderBreakdown = (tableId, data) => {
    const tbody = $(`${tableId} tbody`);
    if (!tbody) return;
    const rows = Object.entries(data || {});
    tbody.innerHTML = rows.length
      ? rows.map(([key, row]) => `
        <tr>
          <td>${esc(sourceLabels[key] || key)}</td>
          <td>${row.trades ?? 0}</td>
          <td>${row.win_rate_pct ?? 0}%</td>
          <td>${pnlHtml(row.pnl)}</td>
        </tr>`).join("")
      : '<tr><td colspan="4" class="muted">No closed trades yet</td></tr>';
  };
  renderBreakdown("#stats-source-table", stats.by_source);
  renderBreakdown("#stats-mode-table", stats.by_mode);
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

function renderScanner(scanner) {
  if (!scanner) return;
  const enabled = scanner.enabled;
  $("#pill-scanner")?.classList.toggle("on", enabled && scanner.running);
  const btn = $("#btn-scanner-toggle");
  if (btn) {
    btn.textContent = enabled ? "Stop scanning" : "Start scanning";
    btn.classList.toggle("primary", !enabled);
  }
  const runBtn = $("#btn-scanner-run");
  if (runBtn) {
    runBtn.disabled = !!scanner.cycle_running;
    runBtn.textContent = scanner.cycle_running ? "Scanning…" : "Scan now";
  }
  const risk = window._state?.risk;
  const disc = scanner.discovery || {};
  const tiles = $("#scanner-tiles");
  if (tiles) {
    tiles.innerHTML = `
      <div class="tile"><div class="label">Status</div><div class="value">${enabled ? "on" : "off"}</div><div class="sub">${scanner.market_open ? "market open" : "market closed"}</div></div>
      <div class="tile"><div class="label">Scans every</div><div class="value">${scanner.scan_interval_minutes ?? 5}m</div><div class="sub">during market hours</div></div>
      <div class="tile"><div class="label">Universe</div><div class="value">${scanner.universe_size ?? 0}</div><div class="sub">${disc.auto ? "auto-discovered" : "built-in fallback"}</div></div>
      <div class="tile"><div class="label">Trade risk</div><div class="value">${fmtUsd(risk?.trade_budget_usd)}</div><div class="sub">${risk?.risk_pct_per_trade ?? "–"}% equity</div></div>`;
  }
  const st = $("#scanner-status");
  if (st) {
    let txt = scanner.last_scan
      ? `Last scan ${scanner.last_scan.slice(0, 16).replace("T", " ")}`
      : "No scans yet.";
    if (scanner.last_ai_result) txt += ` · AI opened ${scanner.last_ai_result.trades_opened}`;
    if (disc.auto) {
      const parts = [];
      if (disc.most_actives) parts.push(`${disc.most_actives} most active`);
      if (disc.day_gainers) parts.push(`${disc.day_gainers} gainers`);
      if (disc.day_losers) parts.push(`${disc.day_losers} losers`);
      if (disc.trending) parts.push(`${disc.trending} trending`);
      if (parts.length) txt += ` · sources: ${parts.join(", ")}`;
    }
    if (scanner.risk?.kill_switch_active) txt = "Kill switch active — daily loss limit hit (includes open P&L).";
    st.textContent = txt;
  }
  const uni = $("#scanner-universe");
  if (uni) {
    const list = scanner.universe || [];
    uni.textContent = list.length ? `Now watching: ${list.join(", ")}` : "";
  }
  const feed = $("#scanner-feed");
  if (feed) {
    const events = scanner.events || [];
    feed.innerHTML = events.length
      ? events.map((e) => `<div class="ev"><time>${esc(e.time)}</time><span class="kind">${esc(e.engine === "ai" ? "AI" : "signal")}:${esc(e.kind)}</span><span>${esc(e.message)}</span></div>`).join("")
      : '<div class="empty">Start the scanner — it checks for entries every 5 minutes during market hours.</div>';
  }
  const banner = $("#signals-banner");
  if (banner) banner.classList.toggle("hidden", !enabled);
}

function renderFeed(engine, journal, scanner) {
  const feed = $("#activity-feed");
  if (!feed) return;
  const events = [];
  (engine?.events || []).forEach((e) => events.push({ time: e.time, kind: e.kind, msg: e.message }));
  (scanner?.events || []).forEach((e) => events.push({ time: e.time, kind: `${e.engine === "ai" ? "AI" : "signal"}:${e.kind}`, msg: e.message }));
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

function renderAccount(user, risk) {
  if (!user) return;
  $("#acct-email").textContent = user.email;
  $("#acct-name").textContent = user.display_name || "—";
  $("#acct-tv").textContent = user.tradingview_username || "Not set";
  $("#acct-tv-status").textContent = user.tv_connected ? "Connected" : "Not connected";

  const cashInput = $("#acct-starting-cash");
  const riskInput = $("#acct-risk-pct");
  const portInput = $("#acct-portfolio-pct");
  if (cashInput && !cashInput.dataset.touched) cashInput.value = user.starting_cash ?? 100000;
  if (riskInput && !riskInput.dataset.touched) riskInput.value = user.risk_pct_per_trade ?? 10;
  if (portInput && !portInput.dataset.touched) portInput.value = user.max_portfolio_risk_pct ?? 50;

  const tiles = $("#account-risk-tiles");
  if (tiles && risk) {
    tiles.innerHTML = `
      <div class="tile"><div class="label">Per trade</div><div class="value">${fmtUsd(risk.trade_budget_usd)}</div><div class="sub">${risk.risk_pct_per_trade ?? "–"}% of equity</div></div>
      <div class="tile"><div class="label">Portfolio cap</div><div class="value">${fmtUsd(risk.portfolio_risk_cap_usd)}</div><div class="sub">${risk.max_portfolio_risk_pct ?? "–"}% max open</div></div>
      <div class="tile"><div class="label">Daily loss cap</div><div class="value">${fmtUsd(risk.daily_loss_cap_usd)}</div><div class="sub">kill switch</div></div>`;
  }
  const note = $("#acct-risk-note");
  if (note && risk) {
    note.textContent = `Each scanner trade risks up to ${fmtUsd(risk.trade_budget_usd)} (${risk.risk_pct_per_trade}% of your paper equity). Open positions are monitored every 5 minutes for profit target, stop loss, and expiry exits.`;
  }
}

async function refreshApp() {
  const s = await api("/api/state");
  window._state = s;
  renderOrders(s.orders);
  renderPositions(s.positions, s.orders);
  renderStats(s.stats);
  renderTiles(s.account);
  renderStrategies(s.engine);
  renderScanner(s.scanner);
  renderFeed(s.engine, s.journal, s.scanner);
  renderAccount(s.user, s.risk);
  $("#pill-engine")?.classList.toggle("on", s.engine?.running);
  $("#pill-market")?.classList.toggle("on", s.engine?.market_open);
  $("#pill-updated").textContent = `Updated ${new Date().toLocaleTimeString()}`;
}

async function refreshTradingView() {
  const setup = await api("/api/tradingview/setup");
  renderTradingView(setup, setup.user);
}

const TUTORIAL_KEY = "oa_tutorial_v1_done";

const TUTORIAL_STEPS = [
  {
    title: "Welcome",
    html: `
      <h3>Welcome to Options AI</h3>
      <p>This is a <strong>paper trading</strong> desk — fake money, real market data. Nothing here is financial advice.</p>
      <p>In about a minute you will know how to:</p>
      <ul>
        <li>Size trades to your risk budget</li>
        <li>Start the scanner (no TradingView needed)</li>
        <li>See why each trade happened</li>
      </ul>`,
    nav: null,
    nextLabel: "Let's go",
  },
  {
    title: "Set your risk",
    html: `
      <h3>1. Account tab — bankroll &amp; risk</h3>
      <p>Open <strong>Account</strong> in the bottom nav and set:</p>
      <ul>
        <li><strong>Starting capital</strong> — your paper bankroll (e.g. $100,000)</li>
        <li><strong>Risk per trade</strong> — % of equity risked on each trade (start with 1–2%)</li>
        <li><strong>Max portfolio risk</strong> — total open risk cap across all positions</li>
      </ul>
      <p>Every signal and AI trade is sized to these limits automatically.</p>`,
    nav: "account",
    nextLabel: "Open Account",
  },
  {
    title: "The scanner is already on",
    html: `
      <h3>2. Scanner tab — nothing to set up</h3>
      <p>The scanner starts <strong>on by default</strong> the moment you sign up — no watchlist, no toggle, no paid TradingView plan.</p>
      <ul>
        <li>Every 5 minutes during market hours it scans for entries <strong>automatically</strong>, and restarts itself on its own after any server restart</li>
        <li>It finds candidates across the whole market by itself: most active names, biggest movers, trending tickers</li>
        <li>Rule-based entry signals + the AI brain pick the best setups, sized to your risk % from Account</li>
      </ul>
      <p>Visit <strong>Scanner</strong> any time to pause it, or tap <strong>Scan now</strong> to test immediately.</p>`,
    nav: "scanner",
    nextLabel: "Open Scanner",
  },
  {
    title: "Watch & learn",
    html: `
      <h3>3. Home &amp; Orders — track everything</h3>
      <ul>
        <li><strong>Home</strong> — open/closed positions and live P&amp;L</li>
        <li>Tap <strong>Why?</strong> on any position to see the full reasoning</li>
        <li><strong>Orders</strong> — every trade attempt, including skipped ones</li>
      </ul>
      <p>Skipped trades show why risk, earnings, or regime rules blocked entry — that's intentional protection.</p>`,
    nav: "home",
    nextLabel: "Open Home",
  },
  {
    title: "Measure edge",
    html: `
      <h3>4. Stats tab — are you making money?</h3>
      <p>After some trades close, check <strong>Stats</strong> for:</p>
      <ul>
        <li><strong>Expectancy</strong> — average $ per trade (want positive)</li>
        <li><strong>Win rate</strong> and max drawdown</li>
        <li>P&amp;L broken down by source (signals vs AI) and mode (day vs swing)</li>
      </ul>
      <p>Re-open this guide anytime from Account → <strong>View quick start guide</strong>.</p>`,
    nav: "stats",
    nextLabel: "Get started",
  },
];

let _tutorialStep = 0;

function renderTutorialStep(idx) {
  const step = TUTORIAL_STEPS[idx];
  const body = $("#tutorial-body");
  const progress = $("#tutorial-progress");
  const back = $("#tutorial-back");
  const next = $("#tutorial-next");
  const title = $("#tutorial-title");
  if (!step || !body) return;
  _tutorialStep = idx;
  if (title) title.textContent = step.title;
  body.innerHTML = step.html;
  if (progress) progress.textContent = `${idx + 1} / ${TUTORIAL_STEPS.length}`;
  if (back) back.classList.toggle("hidden", idx === 0);
  if (next) next.textContent = step.nextLabel || (idx === TUTORIAL_STEPS.length - 1 ? "Done" : "Next");
}

function openTutorial(step = 0) {
  renderTutorialStep(step);
  const el = $("#tutorial");
  if (!el) return;
  el.classList.remove("hidden");
  document.body.classList.add("tutorial-open");
}

function closeTutorial(markDone = true) {
  $("#tutorial")?.classList.add("hidden");
  document.body.classList.remove("tutorial-open");
  if (markDone) {
    try { localStorage.setItem(TUTORIAL_KEY, "1"); } catch (_) { /* ignore */ }
  }
  try { sessionStorage.removeItem("oa_show_tutorial"); } catch (_) { /* ignore */ }
}

function shouldShowTutorialOnLogin() {
  try {
    if (sessionStorage.getItem("oa_show_tutorial") === "1") return true;
    return !localStorage.getItem(TUTORIAL_KEY);
  } catch (_) {
    return false;
  }
}

function maybeShowTutorialOnLogin() {
  if (shouldShowTutorialOnLogin()) openTutorial(0);
}

function showPanel(name) {
  $$(".panel").forEach((p) => p.classList.toggle("active", p.dataset.panel === name));
  $$(".nav-btn").forEach((b) => b.classList.toggle("active", b.dataset.nav === name));
  if (name === "tv") refreshTradingView();
  if (name === "account") renderAccount(window._state?.user, window._state?.risk);
}

async function initApp() {
  try {
    await api("/api/auth/me");
  } catch (_) {
    window.location.href = "/login";
    return;
  }
  if (sessionStorage.getItem("oa_auth_redirect")) {
    sessionStorage.removeItem("oa_auth_redirect");
    toast("Session expired — sign in again with your existing account");
  }
  showPanel("home");
  await refreshApp();
  checkPersistenceBanner();
  maybeShowTutorialOnLogin();
  if (!window._tvLoaded) {
    window._tvLoaded = true;
    try { await refreshTradingView(); } catch (_) { /* optional */ }
  }
  setInterval(refreshApp, 5000);
  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("/sw.js").then((reg) => {
      reg.update().catch(() => {});
    }).catch(() => {});
  }
}

function bindAppEvents() {
  $$(".nav-btn").forEach((btn) => btn.addEventListener("click", () => showPanel(btn.dataset.nav)));
  document.body.addEventListener("click", (e) => {
    const navLink = e.target.closest("[data-nav-link]");
    if (navLink) {
      e.preventDefault();
      showPanel(navLink.dataset.navLink);
    }
  });

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
    if (e.target.closest("[data-show-tutorial]")) {
      e.preventDefault();
      e.stopPropagation();
      openTutorial(0);
      return;
    }

    const whyBtn = e.target.closest("[data-why-position]");
    if (whyBtn) {
      const posId = whyBtn.dataset.whyPosition;
      const local = orderForPosition(posId);
      if (local) {
        showOrderDetail(local);
        return;
      }
      try {
        const r = await api(`/api/orders/${posId}`);
        showOrderDetail(r.order);
      } catch (ex) {
        toast(ex.message);
      }
      return;
    }

    const orderBtn = e.target.closest("[data-order]");
    if (orderBtn) {
      const id = orderBtn.dataset.order;
      const local = (window._orders || []).find((o) => o.id === id);
      if (local) {
        showOrderDetail(local);
        return;
      }
      try {
        const r = await api(`/api/orders/${id}`);
        showOrderDetail(r.order);
      } catch (ex) {
        toast(ex.message);
      }
      return;
    }
    const closeOrder = e.target.closest("[data-close-order]");
    if (closeOrder) {
      closeOrderDetail();
      return;
    }

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

  $("#btn-scanner-toggle")?.addEventListener("click", async () => {
    const r = await api("/api/scanner/toggle", { method: "POST" });
    toast(r.enabled ? "Scanner started — checking for entries every 5 minutes" : "Scanner stopped");
    refreshApp();
  });
  $("#btn-scanner-run")?.addEventListener("click", async () => {
    try {
      const r = await api("/api/scanner/run", { method: "POST" });
      toast(`Scanned ${r.tickers_scanned ?? "?"} tickers · day ${r.day_signals} · swing ${r.swing_signals}${r.ai_cycle_started ? " · AI cycle running" : ""}`);
      refreshApp();
    } catch (ex) {
      toast(ex.message || "Scan failed");
    }
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

  $("#tutorial-skip")?.addEventListener("click", (e) => {
    e.stopPropagation();
    closeTutorial(true);
  });
  $("#tutorial-back")?.addEventListener("click", (e) => {
    e.stopPropagation();
    if (_tutorialStep > 0) renderTutorialStep(_tutorialStep - 1);
  });
  $("#tutorial-next")?.addEventListener("click", (e) => {
    e.stopPropagation();
    const step = TUTORIAL_STEPS[_tutorialStep];
    if (step?.nav) showPanel(step.nav);
    if (_tutorialStep >= TUTORIAL_STEPS.length - 1) {
      closeTutorial(true);
      toast("You're set — start the Scanner to begin paper trading");
      return;
    }
    renderTutorialStep(_tutorialStep + 1);
  });
  $("#tutorial")?.querySelector(".tutorial-panel")?.addEventListener("click", (e) => {
    e.stopPropagation();
  });
  document.body.addEventListener("click", (e) => {
    if (e.target.closest("[data-close-tutorial]")) closeTutorial(true);
  });

  ["#acct-starting-cash", "#acct-risk-pct", "#acct-portfolio-pct"].forEach((sel) => {
    $(sel)?.addEventListener("input", (e) => { e.target.dataset.touched = "1"; });
  });

  $("#account-settings-form")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = new FormData(e.target);
    const resetPaper = !!f.get("reset_paper");
    if (resetPaper && !confirm("Reset paper account to starting capital? This clears positions and history.")) return;
    const body = {
      starting_cash: parseFloat(f.get("starting_cash")),
      risk_pct_per_trade: parseFloat(f.get("risk_pct_per_trade")),
      max_portfolio_risk_pct: parseFloat(f.get("max_portfolio_risk_pct")),
      reset_paper: resetPaper,
      clear_history: true,
    };
    await api("/api/account/settings", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    ["#acct-starting-cash", "#acct-risk-pct", "#acct-portfolio-pct"].forEach((sel) => {
      const el = $(sel);
      if (el) delete el.dataset.touched;
    });
    toast(resetPaper ? "Account reset and settings saved" : "Risk settings saved");
    refreshApp();
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
      const r = await api(kind === "signup" ? "/api/auth/signup" : "/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      try { localStorage.setItem("oa_last_email", body.email); } catch (_) { /* ignore */ }
      if (kind === "signup") {
        try { sessionStorage.setItem("oa_show_tutorial", "1"); } catch (_) { /* ignore */ }
      }
      window.location.href = "/app";
    } catch (ex) {
      err.textContent = ex.message;
    }
  });
  try {
    await api("/api/auth/me");
    window.location.href = "/app";
  } catch (_) { /* stay on auth page */ }
  try {
    const last = localStorage.getItem("oa_last_email");
    const email = $("#email");
    if (last && email && !email.value) email.value = last;
  } catch (_) { /* ignore */ }
}

window.App = { initApp, initAuthPage, bindAppEvents, api, refreshApp, openTutorial };
