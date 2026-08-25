// "리포트 생성" 카드: 버튼 하나로 LLM 진단문을 받아 읽는다.

import { setMarkdown } from "./markdown.js";
import { el } from "./viz.js";

/**
 * @param generate  () => Promise<{answer, thinking}>
 */
export function reportCard(panel, label, generate) {
  const run = el("button", "primary", label);
  run.type = "button";
  const output = el("div", "stack");

  run.addEventListener("click", async () => {
    run.disabled = true;
    output.replaceChildren(el("p", "card-note", "관련 이벤트와 지표를 정리하는 중입니다. 1~2분 걸릴 수 있습니다."));
    try {
      const body = await generate();

      output.replaceChildren();
      if (body.thinking) {
        const fold = el("details", "fold");
        fold.append(el("summary", null, "처리 과정"), el("pre", null, body.thinking));
        output.append(fold);
      }
      output.append(setMarkdown(el("div", "answer"), body.answer));
    } catch (error) {
      console.error(error);
      output.replaceChildren(el("p", "card-note", `리포트를 만들지 못했습니다: ${error.message || error}`));
    } finally {
      run.disabled = false;
    }
  });

  const wrap = el("div", "stack");
  wrap.append(run, output);
  panel.content(wrap);
}
