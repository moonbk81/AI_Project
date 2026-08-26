// Chart chrome shared by every view: tokens, plotly defaults, cards, tables.
//
// Mark specs follow this project's data-viz guidance — 2px lines, ≥8px markers
// ringed in the surface color, 4px rounded bar ends with a 2px surface gap,
// hairline recessive grid, legend for ≥2 series with selective direct labels.

export const token = (name) =>
  getComputedStyle(document.body).getPropertyValue(name).trim() || "#000000";

export const seriesColors = () =>
  ["--series-1", "--series-2", "--series-3", "--series-4"].map(token);

export const sequentialRamp = () => ["--seq-250", "--seq-350", "--seq-450", "--seq-550"].map(token);

export const PLOT_CONFIG = { displayModeBar: false, responsive: true };

/**
 * Draw into a container and keep the drawing the container's size.
 *
 * Cards are appended one at a time, so the first card measures a grid that is
 * still one column wide and plotly freezes that width — the chart then hangs
 * out over its neighbours once the grid reflows. Plotly's own `responsive`
 * only watches the window, so the container needs watching directly.
 */
export function drawPlot(node, traces, layout) {
  node.replaceChildren();  // drop the loading placeholder plotly would keep
  Plotly.react(node, traces, layout, PLOT_CONFIG);

  if (!node._sizeWatcher && typeof ResizeObserver !== "undefined") {
    node._sizeWatcher = new ResizeObserver(() => Plotly.Plots.resize(node));
    node._sizeWatcher.observe(node);
  }
  return node;
}

// The builders' own status values, translated once for every view.
const EMPTY_TEXT = {
  unavailable: "이 세션에는 로그 메타데이터가 없습니다.",
  no_data: "해당 데이터가 없습니다.",
  no_events: "이벤트가 감지되지 않았습니다.",
  no_changes: "표시할 상태 변화가 없습니다.",
  no_errors: "실패/차단 기록이 없습니다 (정상).",
  no_calls: "통화 세션 로그가 없습니다.",
  no_ntn_events: "NTN 관련 이벤트가 없습니다.",
  no_signal_history: "RF 신호 이력이 부족해 타임라인을 만들 수 없습니다.",
  unparsable_time: "시간 형식을 해석할 수 없어 시계열을 만들지 못했습니다.",
  clean: "Crash / ANR / Binder 이벤트가 감지되지 않았습니다.",
  load_failed: "데이터를 불러오지 못했습니다.",
};

export const emptyText = (status) => EMPTY_TEXT[status] || `표시할 데이터가 없습니다 (${status}).`;

export function axis(extra) {
  return Object.assign(
    {
      gridcolor: token("--grid"),
      linecolor: token("--baseline"),
      zeroline: false,
      ticks: "",
      automargin: true,
    },
    extra || {},
  );
}

export function baseLayout(extra) {
  return Object.assign(
    {
      paper_bgcolor: "rgba(0,0,0,0)",
      plot_bgcolor: "rgba(0,0,0,0)",
      font: {
        family: '"Pretendard Variable", -apple-system, "Segoe UI", "Liberation Sans", Arial, "Malgun Gothic", "Noto Sans CJK KR", sans-serif',
        size: 12,
        color: token("--text-muted"),
      },
      margin: { l: 64, r: 24, t: 8, b: 44 },
      xaxis: axis(),
      yaxis: axis(),
      hoverlabel: {
        bgcolor: token("--surface-1"),
        bordercolor: token("--border"),
        font: { color: token("--text-primary"), size: 12 },
      },
      legend: { orientation: "h", y: 1.16, x: 0, font: { color: token("--text-secondary") } },
      showlegend: false,
    },
    extra || {},
  );
}

export function lineTrace(name, x, y, color, extra) {
  return Object.assign(
    {
      type: "scatter",
      mode: "lines+markers",
      name,
      x,
      y,
      line: { width: 2, color },
      marker: { size: 8, color, line: { width: 2, color: token("--surface-1") } },
    },
    extra || {},
  );
}

export function barTrace(name, x, y, color, extra) {
  return Object.assign(
    {
      type: "bar",
      name,
      x,
      y,
      marker: {
        color,
        cornerradius: 4,
        line: { width: 2, color: token("--surface-1") },
      },
    },
    extra || {},
  );
}

// A line's own last point carries its name, so identity never rests on color.
export function endLabels(traces) {
  return traces
    .filter((trace) => trace.x.length)
    .map((trace) => ({
      x: trace.x[trace.x.length - 1],
      y: trace.y[trace.y.length - 1],
      text: trace.name,
      showarrow: false,
      xanchor: "left",
      xshift: 10,
      font: { size: 11, color: token("--text-secondary") },
    }));
}

export function groupBy(rows, keyOf) {
  const groups = new Map();
  for (const row of rows || []) {
    const key = keyOf(row);
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(row);
  }
  return groups;
}

export const stepColor = (value, max, ramp) =>
  ramp[Math.min(ramp.length - 1, Math.max(0, Math.floor((value / (max || 1)) * ramp.length)))];

// ------------------------------------------------------------------ elements

export function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

export function tile(label, value, unit, note, noteClass) {
  const wrap = el("div", "tile");
  wrap.append(el("div", "tile-label", label));

  const valueEl = el("div", "tile-value", value);
  if (unit) valueEl.append(el("span", "tile-unit", unit));
  wrap.append(valueEl);

  if (note) wrap.append(el("div", "tile-note" + (noteClass ? " " + noteClass : ""), note));
  return wrap;
}

export function tileRow(tiles) {
  const row = el("div", "kpi-row");
  row.append(...tiles);
  return row;
}

// Labels come from log files and API responses — always inserted as text.
export function table(columns, rows, limit = 300) {
  const wrap = el("div", "table-wrap");
  const node = el("table");

  const head = node.createTHead().insertRow();
  for (const column of columns) head.append(el("th", null, column));

  const body = node.createTBody();
  for (const row of (rows || []).slice(0, limit)) {
    const tr = body.insertRow();
    for (const cell of row) {
      tr.insertCell().textContent = cell === null || cell === undefined ? "-" : String(cell);
    }
  }

  wrap.append(node);
  if ((rows || []).length > limit) {
    wrap.append(el("p", "table-note", `상위 ${limit}건만 표시합니다. 전체 ${rows.length}건`));
  }
  return wrap;
}

export function frameTable(rows, columns, limit) {
  const keys = columns || Object.keys((rows && rows[0]) || {});
  return table(keys, (rows || []).map((row) => keys.map((key) => row[key])), limit);
}

/**
 * A titled panel. `draw()` plots and keeps a table view behind a toggle, so the
 * numbers stay reachable without hovering — which is also the relief the light
 * palette's lower-contrast hues require.
 */
export function card(title, subtitle) {
  const section = el("section", "card");
  const head = el("div", "card-head");
  head.append(el("h2", null, title), el("span", "grow"));

  const toggle = el("button", null, "표");
  toggle.type = "button";
  toggle.classList.add("hidden");
  head.append(toggle);
  section.append(head);
  if (subtitle) section.append(el("p", "card-sub", subtitle));

  const body = el("div", "card-body");
  section.append(body);

  const plotHost = el("div", "plot");
  // Charts arrive one request at a time; an empty box reads as "broken".
  plotHost.append(el("div", "empty", "불러오는 중..."));
  const tableHost = el("div", "hidden");
  body.append(plotHost, tableHost);

  toggle.addEventListener("click", () => {
    const showTable = tableHost.classList.contains("hidden");
    tableHost.classList.toggle("hidden", !showTable);
    plotHost.classList.toggle("hidden", showTable);
    toggle.textContent = showTable ? "차트" : "표";
  });

  return {
    section,
    body,
    /** Extra content above the plot (KPI rows, notes, pickers). */
    prepend(node) {
      body.insertBefore(node, plotHost);
    },
    empty(status) {
      plotHost.replaceChildren(el("div", "empty", emptyText(status)));
      toggle.classList.add("hidden");
    },
    note(text) {
      plotHost.replaceChildren(el("div", "empty", text));
      toggle.classList.add("hidden");
    },
    draw(traces, layout, tableView) {
      plotHost.classList.remove("hidden");
      drawPlot(plotHost, traces, layout);
      if (tableView) {
        tableHost.replaceChildren(tableView);
        toggle.classList.remove("hidden");
      }
    },
    /** A card whose content is a table (or any node) rather than a plot. */
    content(node) {
      plotHost.classList.add("content-host");
      plotHost.replaceChildren(node);
      toggle.classList.add("hidden");
    },
  };
}

export function section(title) {
  const wrap = el("section", "band");
  wrap.append(el("h2", "band-title", title));
  const grid = el("div", "grid");
  wrap.append(grid);
  return { wrap, grid };
}

export const fmt = {
  ms: (value) => (value === null || value === undefined ? "N/A" : `${Math.round(value).toLocaleString()} ms`),
  mb: (value) => `${Number(value || 0).toFixed(1)} MB`,
  count: (value) => Number(value || 0).toLocaleString(),
  fixed: (value, digits = 1) => Number(value || 0).toFixed(digits),
};
