// backend/static/js/viz.js — drawPlot
//
// A card can draw more than once: the network-timeline metric picker redraws on
// every change. drawPlot used to clear the host first, which tore out the svg
// plotly.react was about to update — plotly still saw its own state on the node,
// took the update path, and patched a tree no longer in the document. The card
// went blank on every redraw after the first.
//
// The fake below mirrors plotly's own branch (react only touches a plot it still
// owns) so the test fails if the clearing comes back. Run with `node --test tests/js/`.

import { test } from "node:test";
import assert from "node:assert/strict";

import { drawPlot } from "../../backend/static/js/viz.js";

/** Just enough of a div for the two operations drawPlot performs on the host. */
function fakeHost() {
  return {
    children: [],
    replaceChildren(...kids) {
      this.children = kids;
    },
    querySelector(selector) {
      const wanted = selector.slice(1);
      return this.children.find((kid) => kid.className === wanted) || null;
    },
  };
}

function fakePlotly() {
  const calls = [];
  const Plotly = {
    calls,
    newPlot(node, traces) {
      node._fullLayout = {};
      node.replaceChildren({ className: "plot-container", traces });
      calls.push("newPlot");
    },
    react(node, traces) {
      const container = node.querySelector(".plot-container");
      // Without its container plotly draws into the svg it remembers, which is
      // no longer attached to the node — the visible plot never changes.
      if (container) container.traces = traces;
      calls.push(container ? "react" : "react-into-nothing");
    },
    Plots: { resize() {} },
  };
  globalThis.window = { Plotly };
  return Plotly;
}

const shown = (host) => host.querySelector(".plot-container")?.traces;
const settle = () => new Promise((resolve) => setTimeout(resolve, 0));

test("a redraw reaches the plot instead of a detached tree", async () => {
  const Plotly = fakePlotly();
  const host = fakeHost();
  host.replaceChildren({ className: "empty", text: "불러오는 중..." });

  drawPlot(host, ["dns_avg"], {});
  await settle();
  assert.deepEqual(shown(host), ["dns_avg"], "첫 그리기가 안 됐다");

  drawPlot(host, ["tcp_avg_loss"], {});
  await settle();

  assert.deepEqual(shown(host), ["tcp_avg_loss"], "picker 를 바꿔도 이전 지표가 남아 있다");
  assert.deepEqual(Plotly.calls, ["newPlot", "react"]);
});

test("a host emptied by note() gets a fresh plot, not an update", async () => {
  const Plotly = fakePlotly();
  const host = fakeHost();

  drawPlot(host, ["dns_avg"], {});
  await settle();

  // panel.note()/empty()/content() replace the plot with their own node.
  host.replaceChildren({ className: "empty", text: "해당 데이터가 없습니다." });

  drawPlot(host, ["dns_max"], {});
  await settle();

  assert.deepEqual(shown(host), ["dns_max"]);
  assert.deepEqual(Plotly.calls, ["newPlot", "newPlot"]);
});
