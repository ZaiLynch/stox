"use strict";

const $ = (id) => document.getElementById(id);
const SLOTS = 8;
const fmtEur = new Intl.NumberFormat(undefined, { style: "currency", currency: "EUR" });
const fmtPct = new Intl.NumberFormat(undefined, { style: "percent", minimumFractionDigits: 1, maximumFractionDigits: 1, signDisplay: "exceptZero" });
const fmtDate = new Intl.DateTimeFormat(undefined, { day: "numeric", month: "short", year: "numeric" });
const fmtWhen = new Intl.DateTimeFormat(undefined, { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
const fmtRel = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });

let index = [];
let colorOf = new Map(); // player -> series slot, stable across all games
let chart = null;
let current = null; // {game, prices, result}

async function getJson(path) {
  const resp = await fetch(path, { cache: "no-cache" });
  if (!resp.ok) throw new Error(`${path}: ${resp.status}`);
  return resp.json();
}

function css(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

function seriesColor(player) {
  return css(`--series-${(colorOf.get(player) % SLOTS) + 1}`);
}

function parseDate(s) {
  return new Date(`${s}T00:00:00`);
}

function relative(iso) {
  const minutes = Math.round((Date.parse(iso) - Date.now()) / 60000);
  if (Math.abs(minutes) < 60) return fmtRel.format(minutes, "minute");
  const hours = Math.round(minutes / 60);
  if (Math.abs(hours) < 48) return fmtRel.format(hours, "hour");
  return fmtRel.format(Math.round(hours / 24), "day");
}

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "style") Object.assign(node.style, v);
    else node.setAttribute(k, v);
  }
  node.append(...children);
  return node;
}

function swatch(player) {
  return el("span", { class: "swatch", style: { background: seriesColor(player) }, "aria-hidden": "true" });
}

function showMessage(text) {
  $("message").textContent = text;
  $("message").hidden = !text;
}

// --- game picker & hall of shame -------------------------------------------

function gameLabel(g) {
  if (g.status === "closed") return `${g.id} · ${g.losers.join(" & ")} paid`;
  return `${g.id} · ${g.status}`;
}

function renderPicker(selected) {
  const select = $("game");
  select.replaceChildren(...index.map((g) => el("option", { value: g.id }, gameLabel(g))));
  select.value = selected;
  select.disabled = index.length === 0;
}

function renderShame() {
  const counts = new Map();
  for (const g of index) for (const p of g.picks) if (!counts.has(p.player)) counts.set(p.player, []);
  for (const g of index) if (g.status === "closed") for (const p of g.losers) counts.get(p).push(g.id);
  const rows = [...counts].sort((a, b) => b[1].length - a[1].length || a[0].localeCompare(b[0]));
  const closed = index.some((g) => g.status === "closed");
  $("shame").replaceChildren(
    ...(closed
      ? rows.map(([player, games]) =>
          el("li", {},
            el("span", { class: "player" }, swatch(player), player),
            el("span", { class: "muted small" }, games.slice().reverse().join(", ")),
            el("span", { class: "count" }, games.length ? `🍕 × ${games.length}` : "–")))
      : [el("li", { class: "muted" }, "No finished games yet.")]),
  );
}

// --- one game ---------------------------------------------------------------

async function loadGame(id) {
  const entry = index.find((g) => g.id === id);
  const base = `games/${encodeURIComponent(id)}`;
  const [game, prices, result] = await Promise.all([
    getJson(`${base}/game.json`),
    entry.status === "upcoming" ? null : getJson(`${base}/prices.json`).catch(() => null),
    entry.status === "closed" ? getJson(`${base}/result.json`) : null,
  ]);
  current = { entry, game, prices, result };
  renderGame();
}

function renderGame() {
  const { entry, game, prices, result } = current;
  $("game-view").hidden = false;
  const icon = { running: "●", finished: "●", upcoming: "◷", closed: "✓" }[entry.status];
  const label = entry.status === "finished" ? "waiting for final prices" : entry.status;
  $("status").textContent = `${icon} ${label}`;
  $("dates").textContent = `${fmtDate.format(parseDate(game.start))} – ${fmtDate.format(parseDate(game.end))}`;
  $("updated").textContent =
    result ? `closed ${fmtDate.format(new Date(result.closed_at))}` :
    prices ? `updated ${relative(prices.updated)}` : "";
  $("checksum").hidden = !entry.checksum;
  $("checksum").textContent = entry.checksum ? `Frozen · SHA-256 ${entry.checksum}` : "";

  renderTable();
  const hasSeries = prices && Object.keys(prices.picks).length > 0;
  $("chart-wrap").hidden = !hasSeries;
  if (hasSeries) renderChart();
  else if (chart) { chart.destroy(); chart = null; }
}

function renderTable() {
  const { entry, game, prices, result } = current;
  const values = new Map(game.picks.map((p) => [p.player, prices?.picks[p.player]?.value ?? null]));
  if (result) for (const s of result.standings) values.set(s.player, s.value);
  const losers = new Set(result ? result.losers : entry.last ?? []);
  const rows = game.picks.slice().sort((a, b) => (values.get(b.player) ?? -Infinity) - (values.get(a.player) ?? -Infinity));

  $("standings").replaceChildren(...rows.map((p, i) => {
    const v = values.get(p.player);
    const pizza = losers.has(p.player) ? (result ? "🍕 paid" : "🍕 pays now") : "";
    return el("tr", {},
      el("td", {}, v == null ? "" : String(i + 1)),
      el("td", {}, el("span", { class: "player" }, swatch(p.player), p.player)),
      el("td", {}, p.name ? `${p.name} ` : "", el("span", { class: "ticker" }, p.ticker)),
      el("td", { class: "num" }, v == null ? "–" : fmtEur.format(v)),
      el("td", { class: "num" }, v == null ? "–" : fmtPct.format(v / game.invested_eur - 1)),
      el("td", { class: "pizza" }, pizza));
  }));
}

// --- chart ------------------------------------------------------------------

// Value of a series at time x: the last point at or before x.
function valueAt(data, x) {
  let lo = 0, hi = data.length - 1, found = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (data[mid].x <= x) { found = mid; lo = mid + 1; } else hi = mid - 1;
  }
  return found < 0 ? null : data[found];
}

// Player names next to the line ends, nudged apart so they don't overlap.
const endLabels = {
  id: "endLabels",
  afterDatasetsDraw(c) {
    const { ctx } = c;
    const labels = c.data.datasets
      .map((ds, i) => ({ ds, meta: c.getDatasetMeta(i) }))
      .filter(({ ds, meta }) => ds.player && !meta.hidden && meta.data.length)
      .map(({ ds, meta }) => ({ text: ds.player, x: meta.data.at(-1).x, y: meta.data.at(-1).y }))
      .sort((a, b) => a.y - b.y);
    for (let i = 1; i < labels.length; i++) labels[i].y = Math.max(labels[i].y, labels[i - 1].y + 14);
    ctx.save();
    ctx.font = "12px system-ui, sans-serif";
    ctx.fillStyle = css("--text-secondary");
    ctx.textBaseline = "middle";
    for (const l of labels) ctx.fillText(l.text, l.x + 6, l.y);
    ctx.restore();
  },
};

// Vertical crosshair with every player's value at that moment, in an HTML tooltip.
const crosshair = {
  id: "crosshair",
  afterEvent(c, args) {
    const { event } = args;
    const inside = event.x >= c.chartArea.left && event.x <= c.chartArea.right &&
      event.y >= c.chartArea.top && event.y <= c.chartArea.bottom;
    const x = inside && event.type !== "mouseout" ? c.scales.x.getValueForPixel(event.x) : null;
    if (x !== c.$hoverX) { c.$hoverX = x; args.changed = true; }
  },
  afterDraw(c) {
    const tip = $("tooltip");
    const x = c.$hoverX;
    if (x == null) { tip.hidden = true; return; }
    const points = c.data.datasets
      .filter((ds, i) => ds.player && !c.getDatasetMeta(i).hidden)
      .map((ds) => ({ ds, p: valueAt(ds.data, x) }))
      .filter(({ p }) => p);
    if (!points.length) { tip.hidden = true; return; }

    const px = c.scales.x.getPixelForValue(x);
    const { ctx } = c;
    ctx.save();
    ctx.strokeStyle = css("--baseline");
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(px, c.chartArea.top);
    ctx.lineTo(px, c.chartArea.bottom);
    ctx.stroke();
    for (const { ds, p } of points) {
      ctx.beginPath();
      ctx.arc(px, c.scales.y.getPixelForValue(p.y), 4, 0, 2 * Math.PI);
      ctx.fillStyle = ds.borderColor;
      ctx.strokeStyle = css("--surface");
      ctx.lineWidth = 2;
      ctx.fill();
      ctx.stroke();
    }
    ctx.restore();

    const invested = current.game.invested_eur;
    const latest = Math.max(...points.map(({ p }) => p.x));
    tip.replaceChildren(
      el("div", { class: "when" }, fmtWhen.format(new Date(latest))),
      ...points.sort((a, b) => b.p.y - a.p.y).map(({ ds, p }) =>
        el("div", { class: "row" },
          swatch(ds.player), ds.player,
          el("span", { class: "num" }, fmtEur.format(p.y)),
          el("span", { class: "num muted" }, fmtPct.format(p.y / invested - 1)))),
    );
    tip.hidden = false;
    const left = px > c.width / 2 ? px - tip.offsetWidth - 12 : px + 12;
    tip.style.left = `${Math.max(0, left)}px`;
  },
};

function renderChart() {
  const { game, prices } = current;
  const start = parseDate(game.start).getTime();
  const end = parseDate(game.end).getTime() + 86400000;
  const grid = css("--grid");
  const tick = css("--text-muted");

  const datasets = game.picks
    .filter((p) => prices.picks[p.player])
    .map((p) => ({
      player: p.player,
      label: p.name ? `${p.player} · ${p.name}` : p.player,
      data: prices.picks[p.player].series.map(([t, v]) => ({ x: Date.parse(t), y: v })),
      borderColor: seriesColor(p.player),
      backgroundColor: seriesColor(p.player),
      borderWidth: 2,
      pointRadius: 0,
      pointHoverRadius: 0,
      tension: 0,
    }));
  datasets.push({
    label: "invested",
    data: [{ x: start, y: game.invested_eur }, { x: end, y: game.invested_eur }],
    borderColor: css("--baseline"),
    borderWidth: 1,
    borderDash: [4, 4],
    pointRadius: 0,
    pointHoverRadius: 0,
  });

  if (chart) chart.destroy();
  chart = new Chart($("chart"), {
    type: "line",
    data: { datasets },
    plugins: [endLabels, crosshair],
    options: {
      maintainAspectRatio: false,
      animation: false,
      parsing: false,
      normalized: true,
      layout: { padding: { right: 56 } },
      events: ["mousemove", "mouseout", "touchstart", "touchmove"],
      plugins: {
        tooltip: { enabled: false },
        legend: {
          position: "top",
          align: "start",
          labels: {
            color: css("--text-secondary"),
            usePointStyle: true,
            pointStyle: "line",
            boxWidth: 20,
            filter: (item, data) => Boolean(data.datasets[item.datasetIndex].player),
          },
        },
      },
      scales: {
        x: {
          type: "time",
          min: start,
          max: end,
          time: { tooltipFormat: "PP" },
          grid: { color: grid },
          border: { color: grid },
          ticks: { color: tick, maxRotation: 0, autoSkipPadding: 16 },
        },
        y: {
          grace: "5%",
          grid: { color: grid },
          border: { display: false },
          ticks: { color: tick, callback: (v) => `${v} €` },
        },
      },
    },
  });
}

// --- start ------------------------------------------------------------------

function pickDefault() {
  const wanted = decodeURIComponent(location.hash.slice(1));
  if (index.some((g) => g.id === wanted)) return wanted;
  const live = index.find((g) => g.status === "running" || g.status === "finished");
  return (live ?? index[0]).id;
}

async function show(id) {
  try {
    showMessage("");
    await loadGame(id);
  } catch (e) {
    $("game-view").hidden = true;
    showMessage(`Could not load ${id}: ${e.message}`);
  }
}

async function init() {
  try {
    index = (await getJson("games/index.json")).games;
  } catch (e) {
    showMessage(`Could not load the games: ${e.message}`);
    return;
  }
  // Oldest game first, so a player keeps the same color forever.
  for (const g of index.slice().reverse()) for (const p of g.picks) if (!colorOf.has(p.player)) colorOf.set(p.player, colorOf.size);
  renderShame();
  if (!index.length) {
    renderPicker("");
    showMessage("No games yet. Add games/<id>/game.json to start one.");
    return;
  }
  const id = pickDefault();
  renderPicker(id);
  $("game").addEventListener("change", (e) => { location.hash = e.target.value; });
  window.addEventListener("hashchange", () => {
    const id = pickDefault();
    $("game").value = id;
    show(id);
  });
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
    renderShame();
    if (current) renderGame();
  });
  await show(id);
}

init();
