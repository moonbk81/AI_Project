// backend/static/js/views/plm.js — 결함 선택이 사람 손을 이기지 않는지.
//
// The view opens the defect that owns the log shown at the top, which is for
// whoever arrives without searching PLM. It used to do that over an open
// defect too: the user opened A, reached for its attachments, and by the time
// they pressed the scan button the screen had gone back to the top log's B.
//
// The same shape bit the two PLM calls behind a selection -- whichever landed
// last wrote the detail and attachment panels, so the highlight could say B
// while the panels showed A. Run with `node --test tests/js/*.test.mjs`.

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";

import { autoSelectionStillApplies } from "../../backend/static/js/views/plm.js";

const source = await readFile(
  path.resolve(import.meta.dirname, "../../backend/static/js/views/plm.js"),
  "utf8",
);

test("an open defect is never traded for the active log's own", () => {
  // 사용자가 A 를 열어 뒀다. 상단 로그 주인이 B 라도 A 가 남는다 -- 기다리는
  // 사이에 눌렀든, 그리기 전부터 열려 있었든 마찬가지다.
  assert.equal(autoSelectionStillApplies("B", "A"), false);
  assert.equal(autoSelectionStillApplies("B", "B"), false);
});

test("the active log's defect opens when nothing is open", () => {
  assert.equal(autoSelectionStillApplies("B", null), true);
});

test("a log with no defect number leaves the screen alone", () => {
  // 직접 올린 로그는 짚을 결함이 없다.
  assert.equal(autoSelectionStillApplies("", null), false);
  assert.equal(autoSelectionStillApplies("", "A"), false);
});

test("the selection is read after the owner lookup, not before", () => {
  const block = source.slice(source.indexOf("state.autoDefectFor = sourceFile"));
  const await_ = block.indexOf("await api.filesWithOwners()");
  const guard = block.indexOf("autoSelectionStillApplies(");

  assert.ok(await_ >= 0 && guard > await_,
            "the list stays clickable through the lookup, so decide after it");
  assert.ok(!block.slice(0, await_).includes("state.selected"),
            "a selection read before the await would miss a click made during it");
});

test("a superseded selection stops drawing into the panels", () => {
  // 늦게 끝난 그리기가 화면에 손대면 하이라이트와 내용이 어긋난다.
  assert.match(source, /const selectDefect = async \(defect\) => \{\s*const token = \(selectionToken \+= 1\);/,
               "every selection takes a number");
  for (const drawer of ["drawDetail", "drawAttachments"]) {
    const body = source.slice(source.indexOf(`const ${drawer} = async (defect, token)`));
    assert.ok(body.slice(0, 600).includes("if (staleSelection(token)) return;"),
              `${drawer} must bail once a newer selection has taken over`);
  }
});
