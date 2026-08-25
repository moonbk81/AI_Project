// Shell: which view is on screen, which log it looks at, which theme.

import { api } from "./api.js";
import { el } from "./viz.js";
import { renderDashboard } from "./views/dashboard.js";
import { renderBoot } from "./views/boot.js";
import { renderInternet } from "./views/internet.js";
import { renderSatellite } from "./views/satellite.js";
import { renderFiles } from "./views/files.js";

const VIEWS = [
  { id: "dashboard", label: "대시보드", render: renderDashboard, needsFile: true },
  { id: "boot", label: "부팅", render: renderBoot, needsFile: true },
  { id: "internet", label: "인터넷 품질", render: renderInternet, needsFile: true },
  { id: "satellite", label: "위성", render: renderSatellite, needsFile: true },
  { id: "files", label: "파일 · 분석", render: renderFiles, needsFile: false },
];

const state = {
  view: VIEWS[0].id,
  sourceFile: null,
  files: [],
  leaveHandlers: [],
};

const nodes = {};

function setTheme(next) {
  document.documentElement.dataset.theme = next;
  try {
    localStorage.setItem("theme", next);
  } catch (error) {
    /* private mode: the toggle still works for this session */
  }
  rerender();
}

function currentTheme() {
  if (document.documentElement.dataset.theme) return document.documentElement.dataset.theme;
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function drawNav() {
  nodes.nav.replaceChildren();
  for (const view of VIEWS) {
    const button = el("button", "nav-item" + (view.id === state.view ? " active" : ""), view.label);
    button.type = "button";
    button.addEventListener("click", () => {
      if (state.view === view.id) return;
      state.view = view.id;
      drawNav();
      rerender();
    });
    nodes.nav.append(button);
  }
}

function drawFilePicker() {
  nodes.file.replaceChildren();
  if (!state.files.length) {
    nodes.file.append(new Option("적재된 로그 없음", ""));
    nodes.file.disabled = true;
    return;
  }
  nodes.file.disabled = false;
  for (const file of state.files) nodes.file.append(new Option(file, file));
  if (state.sourceFile) nodes.file.value = state.sourceFile;
}

function rerender() {
  for (const handler of state.leaveHandlers) {
    try {
      handler();
    } catch (error) {
      console.error("leave handler", error);
    }
  }
  state.leaveHandlers = [];

  const view = VIEWS.find((entry) => entry.id === state.view) || VIEWS[0];
  nodes.main.replaceChildren();

  if (view.needsFile && !state.sourceFile) {
    nodes.main.append(el("p", "card-note", "적재된 로그가 없습니다. '파일 · 분석'에서 로그를 올려 분석하세요."));
    return;
  }

  const ctx = {
    setSourceFile(file) {
      state.sourceFile = file;
      drawFilePicker();
      rerender();
    },
    async onFilesChanged() {
      await loadFiles();
      drawFilePicker();
      rerender();
    },
    onLeave(handler) {
      state.leaveHandlers.push(handler);
    },
  };

  Promise.resolve(view.render(nodes.main, state.sourceFile, ctx)).catch((error) => {
    console.error(view.id, error);
    nodes.main.append(el("p", "card-note", "화면을 그리는 중 오류가 발생했습니다. 콘솔을 확인하세요."));
  });
}

async function loadFiles() {
  state.files = await api.files().catch(() => []);
  if (!state.files.includes(state.sourceFile)) state.sourceFile = state.files[0] || null;
}

async function boot() {
  nodes.nav = document.getElementById("nav");
  nodes.file = document.getElementById("file");
  nodes.main = document.getElementById("main");
  nodes.theme = document.getElementById("theme");

  try {
    const saved = localStorage.getItem("theme");
    if (saved) document.documentElement.dataset.theme = saved;
  } catch (error) {
    /* ignore */
  }

  nodes.theme.addEventListener("click", () => setTheme(currentTheme() === "dark" ? "light" : "dark"));
  nodes.file.addEventListener("change", () => {
    state.sourceFile = nodes.file.value || null;
    rerender();
  });

  drawNav();
  await loadFiles();
  drawFilePicker();
  rerender();
}

boot();
