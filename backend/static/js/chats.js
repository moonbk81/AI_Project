// 파일별 대화의 보관 규칙.
//
// 대화는 파일 이름으로 묶여 있어서, 파일을 바꿨다 돌아와도 물어본 것이 남아
// 있다. 문제는 이름이 같은 다른 로그다: PLM 첨부의 로그는 `act_dumpstate`,
// `dumpstate` 처럼 결함이 달라도 이름이 겹치고, 같은 이름으로 다시 적재하면
// 적재분은 통째로 교체된다. 그때 옛 대화를 그대로 두면 없는 로그를 두고 이야기가
// 이어지고, 그 대화가 다음 질문의 history 로 올라가 LLM 까지 물들인다.
//
// 규칙 자체는 app.js 가 들고 있는 Map 을 다루는 두 줄이지만, app.js 는 불러오는
// 순간 boot() 를 돌려서 테스트가 손대지 못한다. 그래서 여기 따로 둔다.

/** 그 파일의 대화만 버린다. 같은 이름으로 로그가 다시 적재됐을 때. */
export function forgetChat(chats, file) {
  if (file) chats.delete(file);
}

/**
 * 적재 목록에 없는 파일의 대화를 버린다. DB 초기화나 개별 삭제 뒤의 청소.
 *
 * `files` 가 배열이 아니면(목록을 못 받았으면) 아무것도 버리지 않는다. 목록을
 * 못 받은 것과 목록이 빈 것은 다르다 -- 섞으면 잠깐 끊긴 요청 하나가 기록을
 * 전부 지운다.
 */
export function forgetMissingChats(chats, files) {
  if (!Array.isArray(files)) return;
  for (const file of [...chats.keys()]) {
    if (file && !files.includes(file)) chats.delete(file);
  }
}
