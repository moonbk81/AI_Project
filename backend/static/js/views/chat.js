// 채팅 — 적재된 로그에 대해 묻고, 근거 로그와 함께 답을 읽는다.

import { api, rememberKnoxId, rememberedKnoxId } from "../api.js";
import { setMarkdown } from "../markdown.js";
import { el } from "../viz.js";

// The engine gets the tail of the conversation as context.
const HISTORY_TURNS = 5;

const QUICK_PROMPTS = [
  { key: "call_drop", label: "통화 끊김 확인" },
  { key: "data_network_issue", label: "데이터 연결 확인" },
  { key: "battery_crash", label: "배터리·Crash 확인" },
  { key: "network_oos", label: "망 등록/OOS 확인" },
  { key: "antenna_level_analysis", label: "Signal Level 확인" },
  { key: "volte_sip_analysis", label: "VoLTE/SIP 확인" },
  { key: "internet_stall_analysis", label: "인터넷 지연 확인" },
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

/**
 * Register this answer on the PLM defect the PLM tab has open.
 *
 * The comment body is built server-side: it carries the header this tool uses
 * to recognise its own comments later, so it must not be assembled here.
 */
function registerBlock(turn, ctx) {
  const defect = ctx.activeDefect;
  const fold = details("이 답변을 PLM 코멘트로 등록");

  if (!defect?.code) {
    fold.append(el("p", "card-note", "PLM 탭에서 결함을 먼저 선택하면 여기서 바로 등록할 수 있습니다."));
    return fold;
  }

  const knox = el("input", "text-input");
  knox.type = "text";
  knox.placeholder = "Knox ID";
  knox.value = rememberedKnoxId();

  const submit = el("button", "primary", "PLM Comment 등록");
  submit.type = "button";
  const note = el("p", "card-note");

  const preview = el("details", "fold");
  preview.append(el("summary", null, "등록될 내용 미리보기"),
                 el("p", "card-note", "맨 앞에 '💬 AI Chat 분석 결과' 머리말이 붙고, 줄바꿈은 PLM 화면에서도 유지됩니다."),
                 el("pre", null, turn.answer));

  submit.addEventListener("click", async () => {
    submit.disabled = true;
    note.textContent = "등록 중...";
    try {
      try {
        rememberKnoxId(knox.value);
      } catch (error) {
        /* private mode */
      }

      const body = await api.plmSubmitAnswer({
        division_code: defect.division,
        defect_code: defect.code,
        create_user: knox.value.trim(),
        answer: turn.answer,
      });
      note.textContent = body.success ? `${defect.code} 에 등록했습니다. ${body.message || ""}`.trim()
                                      : body.message || "등록 실패";
      turn.registered = body.success;
      if (body.success) ctx.invalidateDefect(defect.division, defect.code);
    } catch (error) {
      note.textContent = String(error.message || error);
    } finally {
      submit.disabled = false;
    }
  });

  fold.append(el("p", "card-note", `대상 결함: ${defect.code}${defect.title ? " · " + defect.title : ""}`),
              preview, knox, submit, note);
  return fold;
}

function assistantBubble(turn, ctx) {
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
  if (turn.answer) wrap.append(registerBlock(turn, ctx));
  return wrap;
}

function pendingBubble(question) {
  const wrap = el("div", "msg assistant pending");
  wrap.append(el("div", "msg-body", `"${question}" 에 대한 로그를 찾아보는 중입니다...`));
  return wrap;
}

/** Which model the answers come from — the chat is a conversation with it. */
function modelLine() {
  const line = el("p", "model-line", "모델 확인 중...");

  api.health()
    .then((health) => {
      line.replaceChildren();
      line.append(el("span", "chip active", health.model || "unknown"));

      // "vLLM/OpenAI-compatible - model @ http://host/api/v1" → provider, host
      const runtime = String(health.runtime || "");
      const provider = runtime.split(" - ")[0] || health.provider || "";
      const endpoint = runtime.includes("@") ? runtime.split("@").pop().trim() : "";

      line.append(el("span", "model-meta", [provider, endpoint].filter(Boolean).join(" · ")));
      if (health.engine_status && health.engine_status !== "loaded") {
        line.append(el("span", "model-meta", `검색 엔진: ${health.engine_status}`));
      }
    })
    .catch(() => {
      line.textContent = "모델 정보를 불러오지 못했습니다.";
    });

  return line;
}

export async function renderChat(mount, sourceFile, ctx) {
  const wrap = el("section", "band chat");
  wrap.append(el("h2", "band-title", `채팅 · ${sourceFile}`), modelLine());
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
      log.append(turn.pending ? pendingBubble(turn.question) : assistantBubble(turn, ctx));
    }
    log.scrollTop = log.scrollHeight;
  };

  // A question handed over from another view (a PLM defect, say) is already
  // in the conversation, waiting to be sent.
  const handedOver = turns.find((turn) => turn.autoSend);

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

  const ask = (question, existing) => {
    if (!question.trim() || busy) return;
    const turn = existing || { question, pending: true };
    if (!existing) turns.push(turn);
    delete turn.autoSend;
    redraw();
    setBusy(true);

    // 답을 기다리는 동안 다른 탭에 다녀오면 이 화면은 버려지고 새로 그려진다.
    // 진행 중인 요청을 turn 에 달아 두어야 새 화면이 다시 붙을 수 있다.
    turn.inflight = run(turn, question);
    return turn.inflight;
  };

  const run = async (turn, question) => {
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
        // Kept so the answer can be filed as an analysis case later.
        ids: body.ids,
        metas: body.metas,
        categories: ["Total_Report", ...new Set((body.metas || []).map((m) => m?.log_type).filter(Boolean))],
      });
    } catch (error) {
      console.error(error);
      Object.assign(turn, { pending: false, answer: `답변을 받지 못했습니다: ${error.message || error}` });
    } finally {
      delete turn.inflight;
      setBusy(false);
      redraw();
    }
  };

  // Not awaited: the chat log, the input box and a handed-over question must
  // not wait on /quick-prompts. Until it lands, a button asks its own label.
  let configuredPrompts = {};
  const promptsLoaded = api.quickPrompts()
    .then((body) => { configuredPrompts = body || {}; })
    .catch(() => {});

  for (const prompt of QUICK_PROMPTS) {
    const button = el("button", null, prompt.label);
    button.type = "button";
    button.addEventListener("click", async () => {
      await promptsLoaded;
      ask(configuredPrompts[prompt.key] || prompt.label);
    });
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

  // 다른 탭에 다녀온 사이에 답이 오고 있었다면, 그 요청이 끝나는 대로 이 화면을
  // 갱신한다. 답 자체는 대화에 그대로 남으므로 다시 물을 필요는 없다.
  const waiting = turns.find((turn) => turn.pending && turn.inflight);
  if (waiting) {
    setBusy(true);
    waiting.inflight.finally(() => {
      setBusy(false);
      redraw();
    });
  }

  if (handedOver) ask(handedOver.question, handedOver);
}
