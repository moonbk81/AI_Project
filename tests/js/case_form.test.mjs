// backend/static/js/views/case_form.js — 답변 하나를 분석 사례로 남기는 칸
//
// 이 폼은 두 화면이 같이 쓴다. 사례 탭은 마지막 답변 하나만 집어 오지만, 채팅은
// 대화 중 어느 답변에서든 연다 -- 그래서 "지금 이 답변이 읽은 로그" 를 근거로
// 보내는지가 핵심이다. 엉뚱한 턴의 ids 가 실리면 사례는 남지만 다른 로그에
// 걸리고, 화면에는 성공으로 보인다. Run with `node --test tests/js/`.

import { test } from "node:test";
import assert from "node:assert/strict";

/** 폼이 실제로 쓰는 만큼의 DOM. createElement, append, addEventListener 뿐이다. */
function fakeNode(tag) {
  return {
    tagName: tag,
    className: "",
    textContent: "",
    value: "",
    children: [],
    handlers: {},
    append(...kids) {
      for (const kid of kids) {
        this.children.push(kid);
        // A select reports its first option until something else is chosen.
        if (this.tagName === "select" && kid.tagName === "option" && !this.value) this.value = kid.value;
      }
    },
    addEventListener(type, fn) {
      (this.handlers[type] ||= []).push(fn);
    },
  };
}

globalThis.document = { createElement: fakeNode };
globalThis.Option = function Option(text, value) {
  const node = fakeNode("option");
  node.textContent = text;
  node.value = value === undefined ? text : value;
  return node;
};

/** 이벤트 하나를 흘려 보낸다. 핸들러가 비동기면 끝날 때까지 기다린다. */
const fire = async (node, type) => {
  for (const handler of node.handlers[type] || []) await handler();
};

function find(node, match) {
  if (match(node)) return node;
  for (const kid of node.children || []) {
    const hit = find(kid, match);
    if (hit) return hit;
  }
  return null;
}

const textarea = (form) => find(form, (node) => node.tagName === "textarea");
const button = (form) => find(form, (node) => node.tagName === "button");
const selects = (form) => {
  const found = [];
  const walk = (node) => {
    if (node.tagName === "select") found.push(node);
    for (const kid of node.children || []) walk(kid);
  };
  walk(form);
  return found;
};

/** 보낸 요청을 기록하고, 경로마다 정해 둔 답을 돌려주는 백엔드. */
function fakeBackend(answers = {}) {
  const calls = [];
  globalThis.fetch = async (path, options) => {
    calls.push({ path, body: JSON.parse(options.body) });
    return { ok: true, json: async () => answers[path] ?? { success: true } };
  };
  return calls;
}

const turn = (extra) => ({
  question: "통화가 왜 끊겼어?",
  answer: "MNR 발생 후 CP crash. Radio 펌웨어 업데이트 필요.",
  ids: ["log-1", "log-2"],
  metas: [{ log_type: "Radio" }],
  categories: ["Total_Report", "Radio"],
  ...extra,
});

const { caseForm } = await import("../../backend/static/js/views/case_form.js");

test("채팅에서 연 칸에는 답변이 초안으로 들어가 있다", async () => {
  fakeBackend();
  const answer = turn();

  const form = caseForm(answer, { draft: answer.answer });

  // 옮겨 적지 않아도 되고, 그대로 두지 않고 고칠 수도 있다.
  assert.equal(textarea(form).value, answer.answer);
});

test("등록은 그 답변이 읽은 로그를 근거로 간다", async () => {
  const calls = fakeBackend();
  const older = turn({ ids: ["old-1"], metas: [{ log_type: "Power" }] });
  const newer = turn();

  const form = caseForm(older, { draft: older.answer });
  caseForm(newer, { draft: newer.answer });
  await fire(button(form), "click");

  const saved = calls.find((call) => call.path === "/knowledge");
  assert.deepEqual(saved.body.ids, ["old-1"], "열어 둔 답변의 로그여야 한다");
  assert.deepEqual(saved.body.metas, [{ log_type: "Power" }]);
  assert.equal(saved.body.feedback, older.answer);
  assert.ok(older.filed, "남긴 답변은 남긴 줄 알아야 다시 열었을 때 표시된다");
});

test("분석 내용이 비어 있으면 보내지 않는다", async () => {
  const calls = fakeBackend();
  const form = caseForm(turn());

  await fire(button(form), "click");

  assert.equal(calls.filter((call) => call.path === "/knowledge").length, 0);
  assert.match(find(form, (node) => node.className === "card-note" && node.textContent.includes("입력")).textContent,
               /분석 내용을 입력하세요/);
});

test("초안을 받아 왔으면 분류를 미리 추천해 둔다", async () => {
  const calls = fakeBackend({ "/knowledge/recommend-category": { category: "Radio" } });
  const answer = turn();

  const form = caseForm(answer, { draft: answer.answer });
  await new Promise((resolve) => setTimeout(resolve, 0));

  const [category] = selects(form);
  assert.equal(category.value, "Radio", "사람이 손대기 전에 이미 읽을 문장이 있다");
  assert.equal(calls[0].path, "/knowledge/recommend-category");
});

test("사람이 고른 분류는 추천이 덮지 않는다", async () => {
  fakeBackend({ "/knowledge/recommend-category": { category: "Radio" } });
  const form = caseForm(turn());
  const [category] = selects(form);

  category.value = "Total_Report";
  await fire(category, "change");
  textarea(form).value = "MNR 발생";
  await fire(textarea(form), "change");

  assert.equal(category.value, "Total_Report");
});

test("쓰던 문장은 칸 바깥으로 흘러 나간다", async () => {
  // 질문을 하나 더 보내면 채팅 화면은 통째로 다시 그려진다. 이 값을 받아 두는
  // 쪽이 없으면 쓰다 만 코멘트가 그때 사라진다.
  fakeBackend();
  const answer = turn();
  const drafts = [];

  const form = caseForm(answer, { draft: answer.answer, onDraft: (value) => drafts.push(value) });
  textarea(form).value = "MNR 이후 CP crash. 펌웨어 업데이트 요청함.";
  await fire(textarea(form), "input");

  assert.deepEqual(drafts, ["MNR 이후 CP crash. 펌웨어 업데이트 요청함."]);
});

test("등록을 마치면 쓰던 문장도 함께 접힌다", async () => {
  fakeBackend();
  const answer = turn();
  const drafts = [];

  const form = caseForm(answer, { draft: answer.answer, onDraft: (value) => drafts.push(value) });
  textarea(form).value = "수정한 코멘트";
  await fire(textarea(form), "input");
  await fire(button(form), "click");

  // 보관해 둔 초안이 등록 전 상태로 돌아가야, 다시 열었을 때 이미 남긴 문장을
  // 또 쓰고 있는 것처럼 보이지 않는다.
  assert.equal(drafts.at(-1), answer.answer);
  assert.equal(textarea(form).value, answer.answer);
});
