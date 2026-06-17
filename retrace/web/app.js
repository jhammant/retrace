// Retrace web UI — vanilla ES module, fully offline.

// ---------- helpers ----------
const $ = (sel, root = document) => root.querySelector(sel);
const view = $("#view");

function h(html) {
  const t = document.createElement("template");
  t.innerHTML = html.trim();
  return t.content.firstElementChild;
}

function esc(s) {
  return (s ?? "").toString()
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

async function api(path, opts = {}) {
  const res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
  if (!res.ok) {
    const err = new Error(`HTTP ${res.status}`);
    err.status = res.status;
    throw err;
  }
  const ct = res.headers.get("content-type") || "";
  return ct.includes("application/json") ? res.json() : res.text();
}

let toastTimer;
function toast(msg, isErr = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.toggle("err", isErr);
  t.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove("show"), 2600);
}

function relTime(iso) {
  if (!iso) return "—";
  const d = new Date(iso), now = Date.now();
  const s = Math.round((now - d.getTime()) / 1000);
  if (s < 0) return "soon";
  if (s < 45) return "just now";
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  return d.toLocaleDateString([], { month: "short", day: "numeric" });
}
const clock = (iso) => iso ? new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "";
const absTime = (iso) => iso ? new Date(iso).toLocaleString([], { dateStyle: "medium", timeStyle: "short" }) : "";
const dayLabel = (iso) => {
  const d = new Date(iso), today = new Date();
  const isToday = d.toDateString() === today.toDateString();
  const y = new Date(today); y.setDate(y.getDate() - 1);
  const isYest = d.toDateString() === y.toDateString();
  if (isToday) return "Today";
  if (isYest) return "Yesterday";
  return d.toLocaleDateString([], { weekday: "long", month: "long", day: "numeric" });
};

function fmtDur(seconds) {
  const s = Math.round(seconds || 0);
  if (s < 60) return `${s}s`;
  const m = Math.round(s / 60);
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  return `${h}h ${m % 60}m`;
}

function avatar(name) {
  const n = (name || "?").trim();
  let hash = 0;
  for (let i = 0; i < n.length; i++) hash = (hash * 31 + n.charCodeAt(i)) >>> 0;
  const hue = hash % 360;
  return { bg: `linear-gradient(135deg, hsl(${hue} 58% 58%), hsl(${(hue + 40) % 360} 60% 48%))`, letter: n[0]?.toUpperCase() || "?" };
}

function highlight(text, q) {
  if (!q) return esc(text);
  const terms = q.split(/\s+/).filter((t) => t.length > 1).map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  if (!terms.length) return esc(text);
  let out = esc(text);
  for (const t of terms) out = out.replace(new RegExp(`(${t})`, "ig"), "<mark>$1</mark>");
  return out;
}

function isSnoozed(until) {
  if (!until) return false;
  if (until === "indefinite") return true;
  const t = new Date(until).getTime();
  return Number.isFinite(t) && t > Date.now();
}

// ---------- status strip ----------
async function refreshStatus() {
  let snap;
  try { snap = await api("/capture/status"); } catch { return; }
  const c = snap.counters || {};
  const on = !!snap.enabled;
  const p = snap.presence || {};
  const hidden = isSnoozed(snap.snooze_until);
  let state;
  if (!on) state = "○ PAUSED";
  else if (hidden) state = "🙈 HIDDEN";
  else if (p.screen_locked) state = "◍ LOCKED";
  else if (p.display_asleep) state = "◍ DISPLAY OFF";
  else if (p.present === false) state = "◌ AWAY";
  else state = "● RECORDING";
  $("#rd-state").textContent = state;
  const hb = $("#hide-btn");
  hb.classList.toggle("on", hidden);
  hb.textContent = hidden ? "🙈 Resume recording" : "🙈 Hidden mode";
  $("#rd-last").textContent = relTime(snap.last_capture_at);
  $("#rd-stored").textContent = c.stored ?? 0;
  $("#rd-skipped").textContent = (c.skipped_dupe ?? 0) + (c.skipped_denylist ?? 0) + (c.skipped_gated ?? 0);
  $("#rd-app").textContent = snap.last_app || "—";
  const rec = $("#rec-toggle");
  rec.dataset.on = String(on);
  $(".rec-label", rec).textContent = on ? "ON" : "OFF";
}

$("#rec-toggle").addEventListener("click", async () => {
  const on = $("#rec-toggle").dataset.on === "true";
  try {
    await api(on ? "/capture/stop" : "/capture/start", { method: "POST" });
    toast(on ? "Capture paused" : "Capture enabled");
    refreshStatus();
  } catch { toast("Could not toggle capture", true); }
});

$("#hide-btn").addEventListener("click", async () => {
  const snap = await api("/capture/status").catch(() => ({}));
  const hidden = isSnoozed(snap.snooze_until);
  try {
    await api(hidden ? "/capture/resume" : "/capture/pause", { method: "POST" });
    toast(hidden ? "Recording resumed" : "Hidden mode on — not recording");
    refreshStatus();
  } catch { toast("Could not toggle hidden mode", true); }
});

$("#tick-btn").addEventListener("click", async () => {
  const btn = $("#tick-btn");
  btn.disabled = true; btn.textContent = "Capturing…";
  try {
    const r = await api("/capture/tick", { method: "POST" });
    if (r.status === "stored") toast(`Captured · ${r.app || "screen"}`);
    else if (r.status === "skipped") toast(`Skipped: ${r.reason}`);
    else toast(`Capture failed: ${r.reason || r.error || "error"}`, true);
    refreshStatus();
    if (currentRoute() === "now" || currentRoute() === "timeline") render();
  } catch { toast("Capture request failed", true); }
  finally { btn.disabled = false; btn.textContent = "＋ Capture now"; }
});

// ---------- lightbox ----------
const lb = $("#lightbox"), lbImg = $("#lightbox-img");
function openLightbox(src) { lbImg.src = src; lb.hidden = false; }
lb.addEventListener("click", () => { lb.hidden = true; lbImg.src = ""; });
document.addEventListener("keydown", (e) => { if (e.key === "Escape") { lb.hidden = true; lbImg.src = ""; } });

// ---------- card rendering (shared by timeline + search) ----------
function cardHTML(c, q = "") {
  const av = avatar(c.app_name);
  const win = c.window_title ? `<span class="card-win" title="${esc(c.window_title)}">${esc(c.window_title)}</span>` : "";
  return `
    <article class="card" data-id="${c.id}">
      <div class="card-top">
        <span class="av" style="background:${av.bg}">${esc(av.letter)}</span>
        <span class="card-app">${esc(c.app_name || "Unknown")}</span>
        ${win}
      </div>
      ${c.caption ? `<p class="card-cap">${highlight(c.caption, q)}</p>` : ""}
      ${c.snippet ? `<div class="card-snip">${highlight(c.snippet, q)}</div>` : ""}
      <div class="card-full" data-loaded="0"></div>
    </article>`;
}

async function toggleCard(cardEl, q = "") {
  const open = cardEl.classList.toggle("open");
  const full = $(".card-full", cardEl);
  if (open && full.dataset.loaded === "0") {
    full.dataset.loaded = "1";
    full.innerHTML = `<div class="spinner"></div>`;
    try {
      const c = await api(`/capture/${cardEl.dataset.id}`);
      const img = c.has_thumb ? `<img src="${c.image_url}" alt="thumbnail" loading="lazy" />` : `<div></div>`;
      const meta = [
        c.url ? `URL · ${esc(c.url)}` : null,
        c.doc_path ? `DOC · ${esc(c.doc_path)}` : null,
        `SOURCE · ${esc(c.text_source)} · ${c.text_len} chars`,
        c.calendar_event ? `EVENT · ${esc(c.calendar_event)}` : null,
        `TIME · ${absTime(c.captured_at)}`,
      ].filter(Boolean).join("\n");
      const srcLink = c.has_html
        ? `<a class="src-link" href="/capture/${c.id}/html" target="_blank" rel="noreferrer">view page source ↗</a>\n`
        : "";
      full.innerHTML = `${img}<div class="card-text">${srcLink}${highlight(meta + "\n\n" + (c.text || "(no text)"), q)}</div>`;
    } catch { full.innerHTML = `<div class="card-text">Could not load capture.</div>`; }
  }
}

view.addEventListener("click", (e) => {
  const img = e.target.closest(".card-full img, .cell img");
  if (img) { openLightbox(img.src); return; }
  const card = e.target.closest(".card");
  if (card) toggleCard(card, searchState.q);
});

// ---------- NOW ----------
async function renderNow() {
  view.innerHTML = `<div class="spinner"></div>`;
  let latest;
  try { latest = (await api("/capture/recent?limit=1")).captures[0]; } catch { latest = null; }

  const head = `<header class="view-head"><div class="eyebrow">Right now</div>
    <h1 class="view-title">What you're doing</h1></header>`;

  if (!latest) {
    view.innerHTML = head + `<div class="empty-state"><div class="big">No captures yet</div>
      Press <b>＋ Capture now</b> above, or enable capture in the rail, to record your first frame.</div>`;
    return;
  }
  let full = latest;
  try { full = await api(`/capture/${latest.id}`); } catch {}

  const cell = full.has_thumb
    ? `<div class="cell"><img src="${full.image_url}?t=${full.id}" alt="latest capture" />
        <div class="ticks"><span></span><span></span><span></span><span></span></div></div>`
    : `<div class="cell empty">NO THUMBNAIL RETAINED</div>`;

  const srcClass = full.text_source === "ocr" ? "ocr" : full.text_source === "mixed" ? "mixed" : "";
  const av = avatar(full.app_name);
  const urlRow = full.url ? `<div class="meta-row"><span class="mk">URL</span><span class="mv"><a href="${esc(full.url)}" target="_blank" rel="noreferrer">${esc(full.url)}</a></span></div>` : "";
  const evRow = full.calendar_event ? `<div class="meta-row"><span class="mk">Event</span><span class="mv">${esc(full.calendar_event)}</span></div>` : "";

  view.innerHTML = head + `<div class="now-grid stagger">
    ${cell}
    <div class="now-side">
      <p class="now-caption">${esc(full.caption || "—")}</p>
      <div>
        <span class="chip"><span class="av" style="background:${av.bg};width:16px;height:16px">${esc(av.letter)}</span>${esc(full.app_name || "Unknown")}</span>
        <span class="chip"><span class="src-tag ${srcClass}">${esc(full.text_source)}</span></span>
        <span class="chip">${clock(full.captured_at)} · ${relTime(full.captured_at)}</span>
      </div>
      <div class="now-meta">
        ${full.window_title ? `<div class="meta-row"><span class="mk">Window</span><span class="mv">${esc(full.window_title)}</span></div>` : ""}
        ${urlRow}${evRow}
        <div class="meta-row"><span class="mk">Text</span><span class="mv">${full.text_len} characters extracted</span></div>
      </div>
    </div></div>`;
}

// ---------- TIMELINE ----------
const tl = { items: [], cursor: null, app: null, done: false, loading: false, lastDay: null };

async function renderTimeline() {
  Object.assign(tl, { items: [], cursor: null, done: false, loading: false, lastDay: null });
  view.innerHTML = `<header class="view-head"><div class="eyebrow">Rewind</div>
    <h1 class="view-title">Timeline</h1>
    <p class="view-sub">Every captured moment, newest first. Click any entry to expand its full text and thumbnail.</p>
    </header><div class="tl" id="tl"></div><div id="tl-sentinel" style="height:40px"></div>`;
  await loadMoreTimeline();
  const sentinel = $("#tl-sentinel");
  const io = new IntersectionObserver((entries) => {
    if (entries[0].isIntersecting && !tl.done && !tl.loading) loadMoreTimeline();
  });
  io.observe(sentinel);
}

async function loadMoreTimeline() {
  if (tl.loading || tl.done) return;
  tl.loading = true;
  const container = $("#tl");
  let url = `/capture/recent?limit=40`;
  if (tl.cursor) url += `&before=${encodeURIComponent(tl.cursor)}`;
  let data;
  try { data = await api(url); } catch { tl.loading = false; return; }
  const rows = data.captures || [];
  if (rows.length < 40) tl.done = true;
  if (!rows.length && !tl.items.length) {
    container.innerHTML = `<div class="empty-state"><div class="big">Nothing recorded yet</div>Enable capture to start building your timeline.</div>`;
    tl.loading = false; return;
  }
  for (const c of rows) {
    const dl = dayLabel(c.captured_at);
    if (dl !== tl.lastDay) {
      tl.lastDay = dl;
      container.appendChild(h(`<div class="tl-item" style="padding-top:8px"><div class="eyebrow" style="margin-left:-12px">${esc(dl)}</div></div>`));
    }
    const item = h(`<div class="tl-item"><span class="tl-time">${clock(c.captured_at)}</span>${cardHTML(c)}</div>`);
    container.appendChild(item);
  }
  tl.items.push(...rows);
  tl.cursor = rows.length ? rows[rows.length - 1].captured_at : tl.cursor;
  tl.loading = false;
}

// ---------- SEARCH ----------
const searchState = { q: "", mode: "hybrid", app: "" };

function renderSearch() {
  view.innerHTML = `<header class="view-head"><div class="eyebrow">Find</div>
    <h1 class="view-title">Search your memory</h1></header>
    <div class="search-bar">
      <label class="search-input">
        <svg viewBox="0 0 24 24"><circle cx="11" cy="11" r="6"/><path d="M20 20l-4.5-4.5"/></svg>
        <input id="q" type="search" placeholder="Search captured text, captions, windows…" value="${esc(searchState.q)}" autocomplete="off" />
      </label>
      <div class="seg" id="mode">
        ${["text", "semantic", "hybrid"].map((m) => `<button data-mode="${m}" class="${searchState.mode === m ? "on" : ""}">${m.toUpperCase()}</button>`).join("")}
      </div>
      <input class="filter-input" id="app-filter" placeholder="app filter" value="${esc(searchState.app)}" />
    </div>
    <div id="results"></div>`;

  const qEl = $("#q");
  qEl.focus();
  let debounce;
  qEl.addEventListener("input", () => { clearTimeout(debounce); debounce = setTimeout(doSearch, 280); });
  qEl.addEventListener("keydown", (e) => { if (e.key === "Enter") { clearTimeout(debounce); doSearch(); } });
  $("#app-filter").addEventListener("input", (e) => { searchState.app = e.target.value; clearTimeout(debounce); debounce = setTimeout(doSearch, 280); });
  $("#mode").addEventListener("click", (e) => {
    const b = e.target.closest("button"); if (!b) return;
    searchState.mode = b.dataset.mode;
    $$("#mode button").forEach((x) => x.classList.toggle("on", x === b));
    if (searchState.q) doSearch();
  });
  if (searchState.q) doSearch();
}
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

async function doSearch() {
  searchState.q = $("#q").value.trim();
  const results = $("#results");
  if (!searchState.q) { results.innerHTML = `<div class="empty-state">Type to search across everything you've seen.</div>`; return; }
  results.innerHTML = `<div class="spinner"></div>`;
  const params = new URLSearchParams({ q: searchState.q, mode: searchState.mode, limit: "60" });
  if (searchState.app) params.set("app", searchState.app);
  try {
    const data = await api(`/search?${params}`);
    const rows = data.results || [];
    if (!rows.length) { results.innerHTML = `<div class="empty-state">No matches for “${esc(searchState.q)}”.</div>`; return; }
    results.innerHTML = `<div class="eyebrow" style="margin-bottom:14px">${rows.length} result${rows.length === 1 ? "" : "s"} · ${esc(data.mode || searchState.mode)}</div>`
      + rows.map((c) => `<div style="margin-bottom:12px">${cardHTML(c, searchState.q)}</div>`).join("");
  } catch (e) {
    if (e.status === 404) results.innerHTML = `<div class="empty-state"><div class="big">Search activates soon</div>The search endpoint isn't available yet.</div>`;
    else results.innerHTML = `<div class="empty-state">Search failed.</div>`;
  }
}

// ---------- STATS ----------
async function renderStats() {
  view.innerHTML = `<header class="view-head"><div class="eyebrow">Insight</div>
    <h1 class="view-title">Your time</h1></header><div id="stats-body"><div class="spinner"></div></div>`;
  const body = $("#stats-body");
  const end = new Date(); const start = new Date(); start.setDate(start.getDate() - 7);
  const params = new URLSearchParams({ start: start.toISOString().slice(0, 10), end: end.toISOString().slice(0, 10) });
  try {
    const top = await api(`/stats/top?${params}`);
    body.innerHTML = renderStatsBody(top);
    requestAnimationFrame(() => $$(".bar-fill", body).forEach((b) => (b.style.width = b.dataset.w)));
  } catch (e) {
    if (e.status === 404) body.innerHTML = `<div class="empty-state"><div class="big">Stats activate soon</div>Time analytics will appear here once activity ingest is built.</div>`;
    else body.innerHTML = `<div class="empty-state">Could not load stats.</div>`;
  }
}

function barRows(items, key, valFn, { cyan = false, weightFn = (i) => i.seconds } = {}) {
  const max = Math.max(1, ...items.map(weightFn));
  return items.slice(0, 10).map((i) => `
    <div class="bar-row">
      <span class="bar-label">${esc(i[key] || "—")}</span>
      <div class="bar-track"><div class="bar-fill ${cyan ? "cyan" : ""}" data-w="${(weightFn(i) / max * 100).toFixed(1)}%" style="width:0"></div></div>
      <span class="bar-val">${valFn(i)}</span>
    </div>`).join("");
}

function renderStatsBody(top) {
  const apps = top.apps || [], domains = top.domains || [];
  const total = top.total_seconds || 0;
  const totalVisits = domains.reduce((a, d) => a + (d.visits || 0), 0);
  const domainLabel = (d) => d.seconds > 0 ? `${d.visits}× · ${fmtDur(d.seconds)}` : `${d.visits}×`;
  return `
    <div class="cards-row">
      <div class="stat-card"><div class="stat-k">Tracked · 7 days</div><div class="stat-v">${fmtDur(total)}</div></div>
      <div class="stat-card"><div class="stat-k">Apps</div><div class="stat-v">${apps.length}</div></div>
      <div class="stat-card"><div class="stat-k">Page visits</div><div class="stat-v">${totalVisits}</div></div>
    </div>
    <div class="panel"><h3>Time by app</h3>${apps.length ? barRows(apps, "app", (i) => fmtDur(i.seconds)) : '<div class="empty-state">No app activity yet — enable capture, or grant Full Disk Access for focus history.</div>'}</div>
    <div class="panel"><h3>Top domains</h3>${domains.length ? barRows(domains, "domain", domainLabel, { cyan: true, weightFn: (i) => i.visits }) : '<div class="empty-state">No browsing activity yet.</div>'}</div>`;
}

// ---------- SETTINGS ----------
async function renderSettings() {
  view.innerHTML = `<header class="view-head"><div class="eyebrow">Control</div>
    <h1 class="view-title">Settings</h1></header><div id="set-body"><div class="spinner"></div></div>`;
  const body = $("#set-body");
  let cfg = {}, perms = {}, status = {};
  try { [cfg, perms, status] = await Promise.all([api("/config"), api("/permissions"), api("/capture/status")]); } catch {}
  const c = cfg.config || {};

  body.innerHTML = `
    <div class="set-grid">
      <div class="set-row">
        <div><div class="label">Capture</div><div class="desc">Off by default. When on, Retrace records your screen on context switches and on a periodic tick.</div></div>
        <label class="toggle"><input type="checkbox" id="t-enabled" ${status.enabled ? "checked" : ""}/><span class="track"></span></label>
      </div>
      <div class="set-row">
        <div><div class="label">Semantic search</div><div class="desc">Compute on-device embeddings so you can search by meaning, not just keywords.</div></div>
        <label class="toggle"><input type="checkbox" id="t-sem" ${c.enable_semantic_search ? "checked" : ""}/><span class="track"></span></label>
      </div>
      <div class="set-row">
        <div><div class="label">Captions</div><div class="desc">Summarize each capture into a sentence with the on-device language model.</div></div>
        <label class="toggle"><input type="checkbox" id="t-cap" ${c.enable_caption ? "checked" : ""}/><span class="track"></span></label>
      </div>
      <div class="set-row">
        <div><div class="label">Pause when away</div><div class="desc">When you're idle, the screen is locked, or the display is asleep, Retrace stops capturing <b>and</b> stops counting time — so a machine left on and logged in doesn't look "active".</div></div>
        <label class="toggle"><input type="checkbox" id="t-away" ${c.pause_when_away !== false ? "checked" : ""}/><span class="track"></span></label>
      </div>
      <div class="set-row">
        <div><div class="label">Away after</div><div class="desc">How long with no keyboard/mouse input before you're considered away.</div></div>
        <div><input class="num-input" id="n-idle" type="number" min="15" max="3600" value="${Math.round(c.idle_threshold_s ?? 120)}"/> <span class="desc">sec</span></div>
      </div>
      <div class="set-row">
        <div><div class="label">Retention</div><div class="desc">Captures and thumbnails older than this are purged automatically.</div></div>
        <div><input class="num-input" id="n-ret" type="number" min="1" max="3650" value="${c.retention_days ?? 30}"/> <span class="desc">days</span></div>
      </div>
      <div class="set-row">
        <div><div class="label">Capture interval</div><div class="desc">Fallback periodic tick while you're present (event-driven captures happen regardless).</div></div>
        <div><input class="num-input" id="n-int" type="number" min="5" max="3600" value="${Math.round(c.capture_interval_s ?? 45)}"/> <span class="desc">sec</span></div>
      </div>

      <div class="panel">
        <h3>Sensitive content</h3>
        <div class="set-row" style="background:transparent;border:0;padding:8px 0">
          <div><div class="label">Block adult / sensitive sites</div><div class="desc">Skip capture when the URL, domain, or title matches your sensitive list.</div></div>
          <label class="toggle"><input type="checkbox" id="t-sens" ${c.block_sensitive_content !== false ? "checked" : ""}/><span class="track"></span></label>
        </div>
        <div class="set-row" style="background:transparent;border:0;padding:8px 0">
          <div><div class="label">On-device image analysis</div><div class="desc">Scan each frame with Apple's Sensitive Content Analysis; drop anything flagged. Requires "Sensitive Content Warning" enabled in System Settings.</div></div>
          <label class="toggle"><input type="checkbox" id="t-sensimg" ${c.block_sensitive_images !== false ? "checked" : ""}/><span class="track"></span></label>
        </div>
        <div class="desc" style="margin-top:6px">Blocked keywords (matched on URL / title)</div>
        <div class="chips-edit" id="kw" style="margin-top:8px">
          ${(c.sensitive_keywords || []).map((k) => `<span class="chip chip-del" data-kw="${esc(k)}" title="click to remove">${esc(k)} ✕</span>`).join("")}
        </div>
        <div class="btn-row" style="margin-top:12px">
          <input class="filter-input" id="kw-new" placeholder="keyword" style="min-width:180px"/>
          <button class="btn" id="kw-add">Add keyword</button>
        </div>
        <div class="desc" style="margin-top:16px">Blocked domains</div>
        <div class="chips-edit" id="sdom" style="margin-top:8px">
          ${(c.sensitive_domains || []).map((d) => `<span class="chip chip-del" data-dom="${esc(d)}" title="click to remove">${esc(d)} ✕</span>`).join("") || '<span class="desc">none</span>'}
        </div>
        <div class="btn-row" style="margin-top:12px">
          <input class="filter-input" id="sdom-new" placeholder="example.com" style="min-width:200px"/>
          <button class="btn" id="sdom-add">Add domain</button>
        </div>
      </div>

      <div class="panel">
        <h3>Full page capture <span class="chip" style="margin-left:8px">browsers</span></h3>
        <div class="set-row" style="background:transparent;border:0;padding:8px 0">
          <div><div class="label">Capture full page text</div><div class="desc">Store the whole page's text (incl. off-screen) for richer search — not just what's visible. Needs "Allow JavaScript from Apple Events" in your browser.</div></div>
          <label class="toggle"><input type="checkbox" id="t-pagetext" ${c.capture_page_text ? "checked" : ""}/><span class="track"></span></label>
        </div>
        <div class="set-row" style="background:transparent;border:0;padding:8px 0">
          <div><div class="label">Also store raw HTML</div><div class="desc">Keep the page's HTML source (stored compressed, never shown in the timeline — available via "view source" on a capture).</div></div>
          <label class="toggle"><input type="checkbox" id="t-pagehtml" ${c.capture_page_html ? "checked" : ""}/><span class="track"></span></label>
        </div>
      </div>

      <div class="panel">
        <h3>Denylisted apps — never captured</h3>
        <div class="chips-edit" id="deny">
          ${(c.denylist_bundle_ids || []).map((b) => `<span class="chip chip-del" data-bid="${esc(b)}" title="click to remove">${esc(b)} ✕</span>`).join("")}
        </div>
        <div class="btn-row" style="margin-top:14px">
          <input class="filter-input" id="deny-new" placeholder="com.example.bundle.id" style="min-width:240px"/>
          <button class="btn" id="deny-add">Add</button>
        </div>
      </div>

      <div class="panel">
        <h3>Permissions</h3>
        <div class="perm-grid">
          ${Object.entries(perms.permissions || {}).map(([k, p]) => `
            <div class="perm">
              <span class="dot ${p.state}"></span>
              <div><div class="pname">${esc(k.replace(/_/g, " "))}${p.required ? " ·required" : ""}</div>
                ${p.state !== "granted" ? `<div class="pguide">${esc(p.guidance)}</div>` : `<div class="pguide">${esc(p.detail || "")}</div>`}</div>
              <span class="pstate ${p.state}">${p.state}</span>
            </div>`).join("")}
        </div>
      </div>

      <div class="panel">
        <h3>Maintenance</h3>
        <div class="btn-row">
          <button class="btn" id="m-scan">Scan activity</button>
          <button class="btn" id="m-collect">Collect app history</button>
          <button class="btn ghost" id="m-purge">Purge old data now</button>
        </div>
      </div>
    </div>`;

  // wire up
  $("#t-enabled").addEventListener("change", async (e) => {
    await api(e.target.checked ? "/capture/start" : "/capture/stop", { method: "POST" });
    toast(e.target.checked ? "Capture enabled" : "Capture paused"); refreshStatus();
  });
  const saveCfg = async (patch) => {
    try { await api("/config", { method: "POST", body: JSON.stringify(patch) }); toast("Saved"); }
    catch { toast("Could not save", true); }
  };
  $("#t-sem").addEventListener("change", (e) => saveCfg({ enable_semantic_search: e.target.checked }));
  $("#t-cap").addEventListener("change", (e) => saveCfg({ enable_caption: e.target.checked }));
  $("#t-away").addEventListener("change", (e) => saveCfg({ pause_when_away: e.target.checked }));
  $("#n-idle").addEventListener("change", (e) => saveCfg({ idle_threshold_s: parseFloat(e.target.value) }));
  $("#n-ret").addEventListener("change", (e) => saveCfg({ retention_days: parseInt(e.target.value, 10) }));
  $("#n-int").addEventListener("change", (e) => saveCfg({ capture_interval_s: parseFloat(e.target.value) }));

  $("#t-sens").addEventListener("change", (e) => saveCfg({ block_sensitive_content: e.target.checked }));
  $("#t-sensimg").addEventListener("change", (e) => saveCfg({ block_sensitive_images: e.target.checked }));
  $("#t-pagetext").addEventListener("change", (e) => saveCfg({ capture_page_text: e.target.checked }));
  $("#t-pagehtml").addEventListener("change", (e) => saveCfg({ capture_page_html: e.target.checked }));

  $("#deny").addEventListener("click", async (e) => {
    const chip = e.target.closest(".chip-del"); if (!chip) return;
    const list = (c.denylist_bundle_ids || []).filter((b) => b !== chip.dataset.bid);
    c.denylist_bundle_ids = list;
    await saveCfg({ denylist_bundle_ids: list }); renderSettings();
  });
  $("#kw").addEventListener("click", async (e) => {
    const chip = e.target.closest(".chip-del"); if (!chip) return;
    const list = (c.sensitive_keywords || []).filter((k) => k !== chip.dataset.kw);
    c.sensitive_keywords = list; await saveCfg({ sensitive_keywords: list }); renderSettings();
  });
  $("#kw-add").addEventListener("click", async () => {
    const v = $("#kw-new").value.trim().toLowerCase(); if (!v) return;
    const list = [...new Set([...(c.sensitive_keywords || []), v])];
    await saveCfg({ sensitive_keywords: list }); renderSettings();
  });
  $("#sdom").addEventListener("click", async (e) => {
    const chip = e.target.closest(".chip-del"); if (!chip) return;
    const list = (c.sensitive_domains || []).filter((d) => d !== chip.dataset.dom);
    c.sensitive_domains = list; await saveCfg({ sensitive_domains: list }); renderSettings();
  });
  $("#sdom-add").addEventListener("click", async () => {
    const v = $("#sdom-new").value.trim().toLowerCase(); if (!v) return;
    const list = [...new Set([...(c.sensitive_domains || []), v])];
    await saveCfg({ sensitive_domains: list }); renderSettings();
  });
  $("#deny-add").addEventListener("click", async () => {
    const v = $("#deny-new").value.trim(); if (!v) return;
    const list = [...(c.denylist_bundle_ids || []), v];
    await saveCfg({ denylist_bundle_ids: list }); renderSettings();
  });
  $("#m-scan").addEventListener("click", async () => {
    try { const r = await api("/activity/scan", { method: "POST" }); toast(`Scanned · ${r.upserted ?? 0} events`); }
    catch (e) { toast(e.status === 404 ? "Activity scan not available yet" : "Scan failed", true); }
  });
  $("#m-collect").addEventListener("click", async () => {
    try {
      const r = await api("/plugins/collect", { method: "POST" });
      const n = (r.results || []).reduce((a, x) => a + (x.ingested || 0), 0);
      toast(`Collected ${n} item${n === 1 ? "" : "s"} from app plugins`);
    } catch (e) { toast(e.status === 404 ? "Plugins not available yet" : "Collect failed", true); }
  });
  $("#m-purge").addEventListener("click", async () => {
    try { const r = await api("/capture/purge", { method: "POST" }); toast(`Purged ${r.captures_deleted} captures`); }
    catch { toast("Purge failed", true); }
  });
}

// ---------- router ----------
const routes = { now: renderNow, timeline: renderTimeline, search: renderSearch, stats: renderStats, settings: renderSettings };
function currentRoute() { return (location.hash.replace(/^#\//, "") || "now").split("?")[0]; }

function render() {
  const r = currentRoute();
  $$(".nav-item").forEach((n) => n.classList.toggle("active", n.dataset.nav === r));
  (routes[r] || renderNow)();
}

window.addEventListener("hashchange", render);

// ---------- boot ----------
refreshStatus();
setInterval(refreshStatus, 5000);
if (!location.hash) location.hash = "#/now";
render();
