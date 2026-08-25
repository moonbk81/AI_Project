// 채팅 — 적재된 로그에 대해 묻고, 근거 로그와 함께 답을 읽는다.

import { api } from "../api.js";
import { setMarkdown } from "../markdown.js";
import { el } from "../viz.js";

// The engine gets the tail of the conversation as context, matching what the
// Streamlit tab sends.
const HISTORY_TURNS = 5;

const QUICK_PROMPTS = [
  "통화 끊김 확인",
  "데이터 연결 확인",
  "배터리·Crash 확인",
  "망 등록/OOS 확인",
  "Signal Level 확인",
  "VoLTE/SIP 확인",
  "인터넷 지연 확인",
];

const EXAMPLES = [
  "이 로그에서 통화가 끊긴 원인이 뭐야?",
  "OOS 가 발생한 시점과 그 직전 상황을 정리해줘",
  "데이터가 안 되는 구간이 있었는지, 있었다면 왜인지",
  "배터리를 많이 쓴 앱과 그 이유",
];

function details(summary, open = false) {
  const wrap = el("details", "fold");
  if (open) wrap.open = true;
  wrap.append(el("summary", null, summary));
  return wrap;
}

function referenceBlock(block) {
  const wrap = el("div", "reference");
  const title = `자료 ${block.index} · ${block.time} · Slot ${block.slot}`;
  wrap.append(el("h4", null, title + (block.known_solution ? "  [과거 해결 사례]" : "")));

  if (block.known_solution) wrap.append(el("p", "known", block.known_solution));

  if (block.raw_logs.length) {
    wrap.append(el("pre", null, block.raw_logs.join("\n")));
    if (block.truncated) {
      wrap.append(el("p", "card-note", `... 총 ${block.raw_log_total} 라인 중 앞부분만 표시`));
    }
  }

  if (block.raw_request || block.raw_response) {
    const pair = [];
    if (block.raw_request) pair.push(`[REQ]  ${block.raw_request}`);
    if (block.raw_response) pair.push(`[RESP] ${block.raw_response}`);
    wrap.append(el("pre", null, pair.join("\n")));
  }
  return wrap;
}

function userBubble(text) {
  const wrap = el("div", "msg user");
  wrap.append(el("div", "msg-body", text));
  return wrap;
}

function assistantBubble(turn) {
  const wrap = el("div", "msg assistant");

  if (turn.references?.length) {
    const fold = details(`근거 로그 ${turn.references.length}건`, true);
    for (const block of turn.references) fold.append(referenceBlock(block));
    wrap.append(fold);
  }

  if (turn.thinking) {
    const fold = details("처리 과정");
    fold.append(el("pre", null, turn.thinking));
    wrap.append(fold);
  }

  wrap.append(setMarkdown(el("div", "msg-body answer"), turn.answer));
  return wrap;
}

function pendingBubble(question) {
  const wrap = el("div", "msg assistant pending");
  wrap.append(el("div", "msg-body", `"${question}" 에 대한 로그를 찾아보는 중입니다...`));
  return wrap;
}

export async function renderChat(mount, sourceFile, ctx) {
  const wrap = el("section", "band chat");
  wrap.append(el("h2", "band-title", `채팅 · ${sourceFile}`));
  mount.append(wrap);

  const guide = details("질문 예시");
  const list = el("ul");
  for (const example of EXAMPLES) list.append(el("li", null, example));
  guide.append(list);
  wrap.append(guide);

  const log = el("div", "chat-log");
  wrap.append(log);

  // The conversation lives in the shell, so switching views does not lose it.
  const turns = ctx.chat;
  const redraw = () => {
    log.replaceChildren();
    for (const turn of turns) {
      log.append(userBubble(turn.question));
      log.append(turn.pending ? pendingBubble(turn.question) : assistantBubble(turn));
    }
    log.scrollTop = log.scrollHeight;
  };

  const quick = el("div", "quick");
  const form = el("form", "chat-form");
  const input = el("input", "chat-input");
  input.type = "text";
  input.placeholder = "이 로그에 대해 물어보세요";
  const send = el("button", "primary", "질문");
  send.type = "submit";
  form.append(input, send);

  let busy = false;
  const setBusy = (value) => {
    busy = value;
    send.disabled = value;
    input.disabled = value;
    for (const button of quick.children) button.disabled = value;
  };

  const ask = async (question) => {
    if (!question.trim() || busy) return;
    const turn = { question, pending: true };
    turns.push(turn);
    redraw();
    setBusy(true);

    try {
      const history = turns
        .slice(0, -1)
        .flatMap((past) => [
          { role: "user", content: past.question },
          { role: "assistant", content: past.answer || "" },
        ])
        .slice(-HISTORY_TURNS);

      const body = await api.ask(question, sourceFile, history);
      Object.assign(turn, {
        pending: false,
        answer: body.answer,
        thinking: body.thinking,
        references: body.references,
      });
    } catch (error) {
      console.error(error);
      Object.assign(turn, { pending: false, answer: `답변을 받지 못했습니다: ${error.message || error}` });
    } finally {
      setBusy(false);
      redraw();
    }
  };

  for (const prompt of QUICK_PROMPTS) {
    const button = el("button", null, prompt);
    button.type = "button";
    button.addEventListener("click", () => ask(prompt));
    quick.append(button);
  }

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const question = input.value;
    input.value = "";
    ask(question);
  });

  wrap.append(quick, form);
  redraw();
  input.focus?.();
}
