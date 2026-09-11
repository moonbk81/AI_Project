// 파일별 대화의 보관 규칙.
//
// 대화는 파일 이름으로 묶여 있어서, 파일을 바꿨다 돌아와도 물어본 것이 남아
// 있다. 문제는 이름이 같은 다른 로그다: PLM 첨부의 로그는 `act_dumpstate`,
// `dumpstate` 처럼 결함이 달라도 이름이 겹치고, 같은 이름으로 다시 적재하면
// 적재분은 통째로 교체된다. 그때 옛 대화를 그대로 두면 없는 로그를 두고 이야기가
// 이어지고, 그 대화가 다음 질문의 history 로 올라가 LLM 까지 물들인다.
//
// 대화는 서버의 `<base>_chat.json` 에도 남는다. 화면을 새로 열어도, 다른 PC 에서
// 열어도 그 로그를 두고 물어본 것이 그대로 있어야 하기 때문이다. 여기의 Map 은
// 그 파일 앞에 놓인 사본이고, `loaded` 는 어느 파일을 서버에서 이미 가져왔는지다.
//
// 규칙 자체는 app.js 가 들고 있는 Map 을 다루는 몇 줄이지만, app.js 는 불러오는
// 순간 boot() 를 돌려서 테스트가 손대지 못한다. 그래서 여기 따로 둔다.

/** 서버에 남기는 턴의 최대 개수. 백엔드도 같은 수로 자른다. */
export const CHAT_HISTORY_TURNS = 50;

/** 그 파일의 대화만 버린다. 같은 이름으로 로그가 다시 적재됐을 때. */
export function forgetChat(chats, loaded, file) {
  if (!file) return;
  chats.delete(file);
  // 가져온 적 없는 상태로 되돌린다. 다음에 그 파일을 열면 서버에 다시 묻고,
  // 서버 쪽 기록도 분석이 지웠으므로 빈 대화로 시작한다.
  loaded.delete(file);
}

/**
 * 적재 목록에 없는 파일의 대화를 버린다. DB 초기화나 개별 삭제 뒤의 청소.
 *
 * `files` 가 배열이 아니면(목록을 못 받았으면) 아무것도 버리지 않는다. 목록을
 * 못 받은 것과 목록이 빈 것은 다르다 -- 섞으면 잠깐 끊긴 요청 하나가 기록을
 * 전부 지운다.
 */
export function forgetMissingChats(chats, loaded, files) {
  if (!Array.isArray(files)) return;
  for (const file of [...chats.keys()]) {
    if (file && !files.includes(file)) chats.delete(file);
  }
  for (const file of [...loaded]) {
    if (file && !files.includes(file)) loaded.delete(file);
  }
}

/**
 * 서버에 보낼 모양으로 고른다.
 *
 * 답이 없는 턴은 기록이 아니다(물어보다 만 것, 넘겨받아 대기 중인 질문). 진행
 * 상태(`pending`)와 요청 핸들(`inflight`)은 이 화면의 사정이라 빼는데, 특히
 * `inflight` 는 Promise 라서 그대로 두면 빈 객체로 저장돼 다음에 되살릴 때
 * 답을 기다리는 턴처럼 보인다.
 *
 * 사례로 쓰다 만 초안(`caseDraft`)과 그 칸을 펴 뒀는지(`caseOpen`)도 같은 부류다.
 * 이 기록은 그 로그를 여는 사람이 다 같이 보는 것이라, 남의 화면에 내가 쓰다 만
 * 문장이 들어앉으면 안 된다. 등록을 마쳤다는 `filed` 는 남는다 -- 그건 사실이다.
 */
export function storableTurns(turns, limit = CHAT_HISTORY_TURNS) {
  return (turns || [])
    .filter((turn) => turn && turn.answer && !turn.pending)
    .slice(-limit)
    .map(({ pending, autoSend, inflight, caseDraft, caseOpen, ...rest }) => rest);
}

/**
 * 서버에서 가져온 턴을 대화에 얹는다.
 *
 * 화면이 이미 물어본 것이 있으면(PLM 에서 넘겨받은 질문) 그 앞에 놓는다 --
 * 시간순이 뒤집히면 다음 질문의 history 도 뒤집힌다.
 */
export function restoreTurns(conversation, turns) {
  const restored = (turns || []).filter((turn) => turn && turn.answer);
  if (restored.length) conversation.unshift(...restored);
  return conversation;
}
