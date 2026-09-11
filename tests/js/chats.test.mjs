// backend/static/js/chats.js — 대화를 언제 버리는가
//
// 대화가 파일 이름으로 묶여 있는 것을 믿고 "파일이 바뀌면 초기화" 로 두었는데,
// PLM 첨부의 로그는 결함이 달라도 `act_dumpstate` 처럼 이름이 겹친다. 같은
// 이름으로 다시 적재하면 적재분은 교체되고 대화만 남아서, 없는 로그를 두고
// 이야기가 이어졌다. Run with `node --test tests/js/`.

import { test } from "node:test";
import assert from "node:assert/strict";

import {
  CHAT_HISTORY_TURNS,
  forgetChat,
  forgetMissingChats,
  restoreTurns,
  storableTurns,
} from "../../backend/static/js/chats.js";

const A = "act_dumpstate__bongki.moon_payload.json";
const B = "dumpState_1788480510033__bongki.moon_payload.json";

const conversations = () => new Map([[A, [{ question: "왜 끊겼어?" }]], [B, [{ question: "부팅?" }]]]);
const loadedFrom = (...files) => new Set(files);

test("다시 적재된 파일의 대화만 버린다", () => {
  const chats = conversations();

  const loaded = loadedFrom(A, B);

  forgetChat(chats, loaded, A);

  assert.equal(chats.has(A), false);
  // 가져온 적 없는 상태로 되돌려야 다음에 열 때 서버에 다시 묻는다.
  assert.equal(loaded.has(A), false);
  assert.equal(loaded.has(B), true);
  // 다른 파일의 대화는 그 로그가 그대로 있으니 지킨다.
  assert.equal(chats.get(B).length, 1);
});

test("파일 이름이 없으면 아무것도 버리지 않는다", () => {
  const chats = conversations();

  forgetChat(chats, new Set(), null);
  forgetChat(chats, new Set(), "");
  forgetChat(chats, new Set(), undefined);

  assert.equal(chats.size, 2);
});

test("적재에서 사라진 파일의 대화는 청소한다", () => {
  const chats = conversations();

  const loaded = loadedFrom(A, B);

  forgetMissingChats(chats, loaded, [B]);

  assert.deepEqual([...chats.keys()], [B]);
  assert.deepEqual([...loaded], [B]);
});

test("DB 를 비웠으면 대화도 남지 않는다", () => {
  const chats = conversations();

  const loaded = loadedFrom(A, B);

  forgetMissingChats(chats, loaded, []);

  assert.equal(chats.size, 0);
  assert.equal(loaded.size, 0);
});

test("목록을 못 받았을 때는 하나도 버리지 않는다", () => {
  // 목록을 못 받은 것과 목록이 빈 것은 다르다. 섞으면 잠깐 끊긴 요청 하나가
  // 물어본 것을 전부 지운다.
  const chats = conversations();

  const loaded = loadedFrom(A, B);

  forgetMissingChats(chats, loaded, null);
  forgetMissingChats(chats, loaded, undefined);

  assert.equal(chats.size, 2);
  assert.equal(loaded.size, 2);
});

test("파일 없이 물어본 대화는 청소가 건드리지 않는다", () => {
  // 활성 파일이 없을 때의 대화는 "" 로 묶인다. 적재 목록에 있을 수 없는 키라,
  // 목록에 없다는 이유로 지우면 화면이 보고 있는 대화가 사라진다.
  const chats = new Map([["", [{ question: "무엇을 할 수 있어?" }]]]);

  forgetMissingChats(chats, new Set([""]), [A]);

  assert.equal(chats.size, 1);
});


// ------------------------------------------------------- 서버에 남기는 모양


test("답이 없는 턴은 기록이 아니다", () => {
  const turns = [
    { question: "왜 끊겼어?", answer: "RST 가 반복됩니다." },
    { question: "묻다 만 것", pending: true },
    { question: "넘겨받아 대기 중", autoSend: true },
  ];

  assert.deepEqual(storableTurns(turns), [
    { question: "왜 끊겼어?", answer: "RST 가 반복됩니다." },
  ]);
});

test("진행 상태와 요청 핸들은 저장하지 않는다", () => {
  // inflight 는 Promise 라, 그대로 두면 빈 객체로 저장돼 되살릴 때 답을
  // 기다리는 턴처럼 보인다.
  const turns = [{
    question: "왜 끊겼어?",
    answer: "RST 가 반복됩니다.",
    thinking: "패킷을 먼저 봤다",
    ids: ["row-1"],
    pending: false,
    autoSend: false,
    inflight: Promise.resolve(),
  }];

  const [saved] = storableTurns(turns);

  assert.deepEqual(Object.keys(saved).sort(), ["answer", "ids", "question", "thinking"]);
});

test("사례로 쓰다 만 초안은 저장하지 않는다", () => {
  // 대화 기록은 그 로그를 여는 사람이 다 같이 본다. 등록을 마쳤다는 사실은
  // 남아야 하지만, 내가 쓰다 만 문장이 남의 화면에 들어앉으면 안 된다.
  const turns = [{
    question: "왜 끊겼어?",
    answer: "RST 가 반복됩니다.",
    filed: true,
    caseDraft: "쓰다 만 분석 코멘트",
    caseOpen: true,
  }];

  const [saved] = storableTurns(turns);

  assert.deepEqual(Object.keys(saved).sort(), ["answer", "filed", "question"]);
});

test("오래된 턴은 잘라서 보낸다", () => {
  const turns = [...Array(CHAT_HISTORY_TURNS + 10).keys()]
    .map((index) => ({ question: `q${index}`, answer: `a${index}` }));

  const saved = storableTurns(turns);

  assert.equal(saved.length, CHAT_HISTORY_TURNS);
  // 자르는 쪽은 앞이다. 방금 물어본 것이 남아야 한다.
  assert.equal(saved.at(-1).question, `q${CHAT_HISTORY_TURNS + 9}`);
});

test("가져온 기록은 화면이 이미 가진 질문 앞에 놓인다", () => {
  // PLM 에서 넘겨받은 질문이 먼저 들어와 있을 수 있다. 시간순이 뒤집히면
  // 다음 질문의 history 도 뒤집힌다.
  const conversation = [{ question: "이 결함 분석해줘", pending: true, autoSend: true }];

  restoreTurns(conversation, [{ question: "어제 물어본 것", answer: "어제 답" }]);

  assert.deepEqual(conversation.map((turn) => turn.question), [
    "어제 물어본 것",
    "이 결함 분석해줘",
  ]);
});

test("답 없는 기록은 되살리지 않는다", () => {
  const conversation = [];

  restoreTurns(conversation, [{ question: "답이 없던 것" }, null, { question: "q", answer: "a" }]);

  assert.deepEqual(conversation, [{ question: "q", answer: "a" }]);
});

test("가져올 기록이 없으면 대화를 건드리지 않는다", () => {
  const conversation = [{ question: "지금 것", answer: "답" }];

  restoreTurns(conversation, []);
  restoreTurns(conversation, null);

  assert.equal(conversation.length, 1);
});
