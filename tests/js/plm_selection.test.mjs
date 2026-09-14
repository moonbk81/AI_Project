// backend/static/js/views/plm.js — 결함 선택이 사람 손을 이기지 않는지.
//
// The view opens the defect that owns the active log, but that lookup is a
// network round trip and the result list is clickable the whole time. Clicking
// a defect in that window used to be silently swapped for an unrelated one:
// the user opened A, reached for its attachments, and the screen was on B.
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

test("a defect opened while the owner lookup ran is left alone", () => {
  // 사용자가 기다리는 사이 A 를 열었다. 활성 로그 주인이 B 라도 A 가 이긴다.
  assert.equal(autoSelectionStillApplies("B", null, "A"), false);
  assert.equal(autoSelectionStillApplies("B", "C", "A"), false);
});

test("the active log's defect opens when the user picked nothing", () => {
  assert.equal(autoSelectionStillApplies("B", null, null), true);
  assert.equal(autoSelectionStillApplies("B", "A", "A"), true);
});

test("nothing happens without an owner, or when it is already open", () => {
  // 직접 올린 로그는 결함 번호가 없다.
  assert.equal(autoSelectionStillApplies("", null, null), false);
  // 이미 그 결함을 보고 있으면 다시 그릴 이유가 없다.
  assert.equal(autoSelectionStillApplies("B", "B", "B"), false);
});

test("the owner lookup compares the selection across its await", () => {
  const block = source.slice(source.indexOf("state.autoDefectFor = sourceFile"));
  const before = block.indexOf("const before =");
  const await_ = block.indexOf("await api.filesWithOwners()");
  const guard = block.indexOf("autoSelectionStillApplies(");

  assert.ok(before >= 0, "the pre-await selection has to be captured");
  assert.ok(before < await_ && await_ < guard,
            "capture before the await, decide after it");
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
