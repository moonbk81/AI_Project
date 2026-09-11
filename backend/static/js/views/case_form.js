// 사례 등록 폼 — 채팅 답변 옆에서도, 분석 사례 탭에서도 같은 칸을 쓴다.

import { api } from "../api.js";
import { el, field } from "../viz.js";

const SEVERITIES = ["Critical", "Major", "Minor", "Info"];

const PLACEHOLDER =
  "예) RIL 에서 Modem Not Responding(MNR) 발생 후 Force CP CRASH. Radio 펌웨어 업데이트 필요.";

/**
 * 답변 하나를 분석 사례로 남기는 폼.
 *
 * `turn` 은 사례가 근거로 삼을 대화의 한 턴이다 -- 그 답변이 읽은 로그(ids/metas)가
 * 사례에 함께 걸린다. `draft` 는 분석 내용의 초안으로, 채팅에서는 답변 본문을
 * 미리 채워 넘기고 사례 탭에서는 비워 둔 채 사람이 쓴다. 어느 쪽이든 고칠 수 있다 --
 * 사람이 다듬어 남긴 문장이 사례의 값어치다.
 *
 * `onDraft` 는 쓰는 족족 불린다. 화면이 다시 그려져도 쓰던 문장이 남으려면 이 칸
 * 바깥에 -- 대화를 들고 있는 쪽에 -- 얹어 두어야 한다.
 */
export function caseForm(turn, { draft = "", onSaved, onDraft } = {}) {
  const wrap = el("div", "stack");

  const note = el("textarea", "text-input");
  note.rows = 8;
  note.placeholder = PLACEHOLDER;
  note.value = draft;
  note.addEventListener("input", () => onDraft?.(note.value));

  const category = el("select");
  for (const value of turn.categories || []) category.append(new Option(value, value));

  const severity = el("select");
  for (const value of SEVERITIES) severity.append(new Option(value, value));

  // The wording decides where the case is filed; the user can override.
  let categoryTouched = false;
  category.addEventListener("change", () => (categoryTouched = true));

  const recommend = async () => {
    if (categoryTouched || !note.value.trim()) return;
    const { category: recommended } = await api
      .recommendCategory(note.value, turn.categories || [])
      .catch(() => ({}));
    if (recommended) category.value = recommended;
  };
  note.addEventListener("change", recommend);
  // 초안을 받아 왔으면 사람이 손대기 전에 이미 읽을 문장이 있다 -- 지금 추천한다.
  if (draft.trim()) recommend();

  const save = el("button", "primary", "사례 등록");
  save.type = "button";
  const status = el("p", "card-note");

  save.addEventListener("click", async () => {
    if (!note.value.trim()) {
      status.textContent = "분석 내용을 입력하세요.";
      return;
    }
    save.disabled = true;
    status.textContent = "등록 중...";
    try {
      const body = await api.saveKnowledge({
        feedback: note.value,
        severity: severity.value,
        category: category.value,
        ids: turn.ids,
        metas: turn.metas,
      });
      status.textContent = body.success
        ? `[${category.value}] 분류에 ${severity.value} 등급으로 등록했습니다.`
        : "사례 등록에 실패했습니다.";
      if (body.success) {
        turn.filed = true;
        note.value = draft;
        onDraft?.(draft);
        onSaved?.();
      }
    } catch (error) {
      status.textContent = String(error.message || error);
    } finally {
      save.disabled = false;
    }
  });

  wrap.append(
    el("p", "card-note", `이 답변이 참조한 로그 ${turn.ids.length}건을 근거로 등록합니다.`),
    field("분석 내용 및 조치", note),
    field("분류", category),
    field("중요도", severity),
    save,
    status,
  );
  return wrap;
}
