// "리포트 생성" 카드: 버튼 하나로 LLM 진단문을 받아 읽는다.

import { setMarkdown } from "./markdown.js";
import { el } from "./viz.js";

const WAIT_NOTE = "관련 이벤트와 지표를 정리하는 중입니다. 1~2분 걸릴 수 있습니다.";

// 리포트 한 편에 1~2분이 걸린다. 그동안 다른 탭에 다녀오면 화면은 버려지지만
// 요청은 계속 날아가고 있으므로, 진행 상황과 결과를 화면 밖에 둔다. 그러지
// 않으면 다 만든 리포트가 떼어진 DOM 에 그려지고 사라진다.
const reports = new Map();

/**
 * @param generate  () => Promise<{answer, thinking}>
 * @param key       리포트를 구분하는 이름. 같은 키면 다시 그려도 이어진다.
 */
export function reportCard(panel, label, generate, key = label) {
  const run = el("button", "primary", label);
  run.type = "button";
  const output = el("div", "stack");

  const draw = (report) => {
    run.disabled = Boolean(report.pending);

    if (report.pending) {
      output.replaceChildren(el("p", "card-note", WAIT_NOTE));
      return;
    }
    if (report.error) {
      output.replaceChildren(el("p", "card-note", `리포트를 만들지 못했습니다: ${report.error}`));
      return;
    }
    if (!report.body) {
      output.replaceChildren();
      return;
    }

    output.replaceChildren();
    if (report.body.thinking) {
      const fold = el("details", "fold");
      fold.append(el("summary", null, "처리 과정"), el("pre", null, report.body.thinking));
      output.append(fold);
    }
    output.append(setMarkdown(el("div", "answer"), report.body.answer));
  };

  run.addEventListener("click", () => {
    const report = { pending: true };
    reports.set(key, report);
    draw(report);

    report.promise = generate()
      .then((body) => { report.body = body; })
      .catch((error) => {
        console.error(error);
        report.error = String(error.message || error);
      })
      .finally(() => {
        report.pending = false;
        draw(report);
      });
  });

  const wrap = el("div", "stack");
  wrap.append(run, output);
  panel.content(wrap);

  // 이미 만들어 둔 리포트는 그대로 보여 주고, 만드는 중이면 끝나는 대로 잇는다.
  const existing = reports.get(key);
  if (existing) {
    draw(existing);
    if (existing.pending) existing.promise?.finally(() => draw(existing));
  }
}
