// backend/static/js/chats.js — 대화를 언제 버리는가
//
// 대화가 파일 이름으로 묶여 있는 것을 믿고 "파일이 바뀌면 초기화" 로 두었는데,
// PLM 첨부의 로그는 결함이 달라도 `act_dumpstate` 처럼 이름이 겹친다. 같은
// 이름으로 다시 적재하면 적재분은 교체되고 대화만 남아서, 없는 로그를 두고
// 이야기가 이어졌다. Run with `node --test tests/js/`.

import { test } from "node:test";
import assert from "node:assert/strict";

import { forgetChat, forgetMissingChats } from "../../backend/static/js/chats.js";

const A = "act_dumpstate__bongki.moon_payload.json";
const B = "dumpState_1788480510033__bongki.moon_payload.json";

const conversations = () => new Map([[A, [{ question: "왜 끊겼어?" }]], [B, [{ question: "부팅?" }]]]);

test("다시 적재된 파일의 대화만 버린다", () => {
  const chats = conversations();

  forgetChat(chats, A);

  assert.equal(chats.has(A), false);
  // 다른 파일의 대화는 그 로그가 그대로 있으니 지킨다.
  assert.equal(chats.get(B).length, 1);
});

test("파일 이름이 없으면 아무것도 버리지 않는다", () => {
  const chats = conversations();

  forgetChat(chats, null);
  forgetChat(chats, "");
  forgetChat(chats, undefined);

  assert.equal(chats.size, 2);
});

test("적재에서 사라진 파일의 대화는 청소한다", () => {
  const chats = conversations();

  forgetMissingChats(chats, [B]);

  assert.deepEqual([...chats.keys()], [B]);
});

test("DB 를 비웠으면 대화도 남지 않는다", () => {
  const chats = conversations();

  forgetMissingChats(chats, []);

  assert.equal(chats.size, 0);
});

test("목록을 못 받았을 때는 하나도 버리지 않는다", () => {
  // 목록을 못 받은 것과 목록이 빈 것은 다르다. 섞으면 잠깐 끊긴 요청 하나가
  // 물어본 것을 전부 지운다.
  const chats = conversations();

  forgetMissingChats(chats, null);
  forgetMissingChats(chats, undefined);

  assert.equal(chats.size, 2);
});

test("파일 없이 물어본 대화는 청소가 건드리지 않는다", () => {
  // 활성 파일이 없을 때의 대화는 "" 로 묶인다. 적재 목록에 있을 수 없는 키라,
  // 목록에 없다는 이유로 지우면 화면이 보고 있는 대화가 사라진다.
  const chats = new Map([["", [{ question: "무엇을 할 수 있어?" }]]]);

  forgetMissingChats(chats, [A]);

  assert.equal(chats.size, 1);
});
