// Shell: which view is on screen, which log it looks at, which theme.

import { api, rememberKnoxId, rememberedKnoxId } from "./api.js";
import { el } from "./viz.js";
import { renderDashboard } from "./views/dashboard.js?v=20260828-data-stall-flow";
import { renderBoot } from "./views/boot.js";
import { renderSatellite } from "./views/satellite.js";
import { renderChat } from "./views/chat.js";
import { renderKnowledge } from "./views/knowledge.js";
import { renderPlm } from "./views/plm.js";
import { renderFiles } from "./views/files.js";
import { defectCacheKey } from "./views/plm_data.js";

const VIEWS = [
  { id: "dashboard", label: "대시보드", render: renderDashboard, needsFile: true },
  { id: "boot", label: "시스템 진단", render: renderBoot, needsFile: true },
  { id: "satellite", label: "위성", render: renderSatellite, needsFile: true },
  { id: "chat", label: "채팅", render: renderChat, needsFile: true },
  { id: "knowledge", label: "분석 사례", render: renderKnowledge, needsFile: false },
  { id: "plm", label: "PLM", render: renderPlm, needsFile: false },
  { id: "files", label: "파일 · 분석", render: renderFiles, needsFile: false },
];

const state = {
  view: VIEWS[0].id,
  sourceFile: null,
  files: [],
  leaveHandlers: [],
  // Conversations, kept per file so switching views (or files and back) does
  // not throw away what was asked.
  chats: new Map(),
  // The PLM defect the chat can register its answer against.
  activeDefect: null,
  // Search results and the selected PLM defect should survive view switches.
  plmState: null,
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
  // 로그인 전에는 화면 자체가 로그인 창이라 탭도 필요 없다.
  if (!rememberedKnoxId()) return;

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

/**
 * 로그인이라 부르지만 비밀번호는 없다. 이름표를 정하는 자리다.
 *
 * 이 이름이 올린 로그와 남긴 글에 붙고, 없으면 쓰기 동작이 막힌다. 서버도
 * 이 값을 검증하지 않는다 — 사고를 막는 울타리이지 인증이 아니다.
 */
function drawUser() {
  const knox = rememberedKnoxId();
  nodes.user.replaceChildren();

  // 로그인 전에는 본문이 통째로 로그인 창이므로 여기는 비워 둔다.
  if (!knox) return;

  const name = el("span", "user-name", knox);
  const logout = el("button", null, "로그아웃");
  logout.type = "button";
  logout.addEventListener("click", () => {
    rememberKnoxId("");
    // 다음 사람이 앞사람의 파일 목록을 이어받지 않게 비운다.
    state.files = [];
    state.sourceFile = null;
    drawNav();
    drawFilePicker();
    drawUser();
    rerender();
  });
  nodes.user.append(name, logout);
}

function drawFilePicker() {
  nodes.file.replaceChildren();
  if (!rememberedKnoxId()) {
    nodes.file.append(new Option("로그인 후 표시됩니다", ""));
    nodes.file.disabled = true;
    return;
  }

  if (!state.files.length) {
    nodes.file.append(new Option("적재된 로그 없음", ""));
    nodes.file.disabled = true;
    return;
  }
  nodes.file.disabled = false;
  for (const file of state.files) nodes.file.append(new Option(file, file));
  if (state.sourceFile) nodes.file.value = state.sourceFile;
}

/** 로그인 전 첫 화면. 이름을 받기 전에는 적재된 로그도 보여 주지 않는다. */
function drawLoginGate() {
  const wrap = el("section", "login-gate");
  const card = el("div", "card login-card");

  card.append(el("h2", null, "로그 분석 시작하기"));
  card.append(el("p", "card-sub",
    "Knox ID 를 적어 주세요. 비밀번호는 없습니다 — 올린 로그와 남긴 글에 붙는 이름표입니다."));

  const input = el("input", "text-input");
  input.type = "text";
  input.placeholder = "예: bongki.moon";

  const go = el("button", "primary", "시작하기");
  go.type = "button";

  const note = el("p", "card-note",
    "이 이름으로 올린 로그를 구분하고, 다른 사람 것을 덮어쓰지 않게 합니다.");

  const finish = async () => {
    const value = input.value.trim();
    if (!value) {
      note.textContent = "Knox ID 를 입력해 주세요.";
      return;
    }
    go.disabled = true;
    rememberKnoxId(value);
    await loadFiles();
    drawNav();
    drawFilePicker();
    drawUser();
    rerender();
  };

  go.addEventListener("click", finish);
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter") finish();
  });

  card.append(input, go, note);
  wrap.append(card);
  nodes.main.append(wrap);
  input.focus();
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

  nodes.main.replaceChildren();

  if (!rememberedKnoxId()) {
    drawLoginGate();
    return;
  }

  const view = VIEWS.find((entry) => entry.id === state.view) || VIEWS[0];

  if (view.needsFile && !state.sourceFile) {
    nodes.main.append(el("p", "card-note", "적재된 로그가 없습니다. '파일 · 분석'에서 로그를 올려 분석하세요."));
    return;
  }

  const ctx = {
    get chat() {
      const key = state.sourceFile || "";
      if (!state.chats.has(key)) state.chats.set(key, []);
      return state.chats.get(key);
    },
    setSourceFile(file) {
      state.sourceFile = file;
      drawFilePicker();
      rerender();
    },
    /** What the newest answer drew on — a case is filed against those rows. */
    get lastRetrieval() {
      for (const conversation of state.chats.values()) {
        for (let index = conversation.length - 1; index >= 0; index -= 1) {
          if (conversation[index].ids?.length) return conversation[index];
        }
      }
      return null;
    },
    get activeDefect() {
      return state.activeDefect;
    },
    get plmState() {
      if (!state.plmState) {
        state.plmState = {
          division: "25",
          divisionName: "Mobile",
          status: "Open",
          // 로그인한 사람의 결함부터 보여 주는 것이 기본. 다른 방식은 옆 칸에 있다.
          method: "내 문제",
          group: "",
          user: "",
          defectCode: "",
          defects: [],
          selected: null,
          analysis: null,
          searchNote: "",
          attachmentJobs: {},
          // PLM responses keyed by `division:defectCode`. Re-entering the view
          // restores the selection, so without this every rerender() — a theme
          // toggle, a file-picker change — refires three calls at the PLM API.
          cache: {},
        };
      }
      return state.plmState;
    },
    setActiveDefect(defect) {
      state.activeDefect = defect;
    },
    /** Drop a defect's cached detail after writing to it — filing a comment
     *  changes the comment list the cache is holding. Shares the key helper
     *  with the cache itself so the two cannot drift apart. */
    invalidateDefect(division, code) {
      if (state.plmState) delete state.plmState.cache[defectCacheKey(division, code)];
    },
    /** Hand a ready-made question to the chat view and go there. */
    startChat(question) {
      const conversation = this.chat;
      conversation.push({ question, pending: true, autoSend: true });
      state.view = "chat";
      drawNav();
      rerender();
    },
    /**
      * A job finished, or the database was reset: refresh the file list and
      * optionally make one of them active — without redrawing the view. The
      * caller is *inside* that view; redrawing it would throw away what the
      * user is looking at (a PLM search, a half-filled form).
      */
    async filesChanged({ select, redraw = false } = {}) {
      await loadFiles();
      if (select && state.files.includes(select)) state.sourceFile = select;
      drawFilePicker();
      if (redraw) rerender();
      return state.sourceFile;
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
  nodes.user = document.getElementById("user");

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
  drawUser();
  if (rememberedKnoxId()) await loadFiles();
  drawFilePicker();
  rerender();

  // Plotly measures tick and legend text at draw time. If a webfont swaps in
  // after the first render, nudge the already-drawn plots without holding the
  // whole UI blank on first load.
  const fontsSettled = Promise.race([
    document.fonts?.ready ?? Promise.resolve(),
    new Promise((resolve) => setTimeout(resolve, 1500)),
  ]);
  fontsSettled.then(() => {
    if (window.Plotly) {
      document.querySelectorAll(".js-plotly-plot").forEach((plot) => Plotly.Plots.resize(plot));
    }
  });
}

boot();
