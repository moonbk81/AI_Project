// backend/static/js/views/plm.js + app.js — PLM 검색 주기 갱신.
//
// The same search runs again on a timer so a group's new defects show up
// without anyone pressing anything. Two things make or break that: the search
// has to be reproducible from plmState alone (the timer lives in the shell,
// where no PLM control is on screen), and a refresh must not disturb the
// defect someone is reading. Run with `node --test tests/js/*.test.mjs`.

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";

import {
  REFRESH_CHOICES,
  REFRESH_LABELS,
  applyPlmSearch,
  runPlmSearch,
} from "../../backend/static/js/views/plm.js";

const appSource = await readFile(
  path.resolve(import.meta.dirname, "../../backend/static/js/app.js"),
  "utf8",
);

/** Records what the search asked the backend for. */
function fakeApi() {
  const calls = [];
  return {
    calls,
    plmQuickSearch(body) {
      calls.push(["quick", body]);
      return Promise.resolve({ success: true, defects: [] });
    },
    plmDefectDetails(division, codes) {
      calls.push(["details", division, codes]);
      return Promise.resolve({ success: true, defects: [] });
    },
    plmGroupUsers(group) {
      calls.push(["group", group]);
      return Promise.resolve({ users: ["a.kim", "b.lee"] });
    },
  };
}

const defect = (code) => ({ defectCode: code, plmTitle: code });

test("a group search is reproducible from plmState with no controls on screen", async () => {
  const api = fakeApi();
  await runPlmSearch(
    { division: "25", method: "그룹", group: "RIL2", status: "Open" },
    api,
  );

  assert.deepEqual(api.calls[0], ["group", "RIL2"]);
  assert.deepEqual(api.calls[1], ["quick", {
    division_code: "25",
    main_owner_id: "a.kim,b.lee",
    status: "open",
  }]);
});

test("the other search methods survive the same trip through plmState", async () => {
  const byUser = fakeApi();
  await runPlmSearch({ division: "25", method: "사용자 ID", user: " bongki.moon " }, byUser);
  assert.equal(byUser.calls[0][1].main_owner_id, "bongki.moon");

  const byCode = fakeApi();
  await runPlmSearch({ division: "25", method: "PLM 번호", defectCode: "P1, P2" }, byCode);
  assert.deepEqual(byCode.calls[0], ["details", "25", ["P1", "P2"]]);
});

test("a search with nothing to search for never reaches the PLM API", async () => {
  const api = fakeApi();
  const empty = await runPlmSearch({ division: "25", method: "PLM 번호", defectCode: "  " }, api);
  const noOwner = await runPlmSearch({ division: "25", method: "사용자 ID", user: "" }, api);

  assert.equal(empty.success, false);
  assert.equal(noOwner.success, false);
  assert.equal(api.calls.length, 0, "an empty box is not a reason to call PLM");
});

test("a refresh counts what the user has not seen; their own search does not", () => {
  const state = { seenCodes: [], newCodes: [] };

  // 사람이 누른 검색 — 결과가 눈앞에 있으니 새것이 없다.
  applyPlmSearch(state, { success: true, defects: [defect("A")] }, { seen: true });
  assert.deepEqual(state.newCodes, []);

  // 주기 갱신이 B 를 물어 왔다.
  const fresh = applyPlmSearch(state, { success: true, defects: [defect("A"), defect("B")] }, { seen: false });
  assert.deepEqual(fresh, ["B"]);
  assert.deepEqual(state.newCodes, ["B"]);

  // 다음 주기에 C 가 더 붙으면 배지는 쌓인다.
  applyPlmSearch(state, { success: true, defects: [defect("A"), defect("B"), defect("C")] }, { seen: false });
  assert.deepEqual(state.newCodes, ["B", "C"]);
});

test("a defect that left the search leaves the badge with it", () => {
  const state = { seenCodes: ["A"], newCodes: [] };
  applyPlmSearch(state, { success: true, defects: [defect("A"), defect("B")] }, { seen: false });
  assert.deepEqual(state.newCodes, ["B"]);

  // B 가 닫혀 검색에서 빠졌다. 세어 둘 이유도 사라진다.
  applyPlmSearch(state, { success: true, defects: [defect("A")] }, { seen: false });
  assert.deepEqual(state.newCodes, []);
});

test("a refresh leaves the open defect and the analysis alone", () => {
  const state = {
    seenCodes: ["A"], newCodes: [],
    selected: { defectCode: "A" }, analysis: { kept: true },
  };
  applyPlmSearch(state, { success: true, defects: [defect("B")] }, { seen: false });

  assert.deepEqual(state.selected, { defectCode: "A" },
                   "the timer must not pull a defect out from under a reader");
  assert.deepEqual(state.analysis, { kept: true });
});

test("a failed refresh says so instead of emptying the list silently", () => {
  const state = { seenCodes: [], newCodes: [] };
  applyPlmSearch(state, { success: false, message: "PLM 에 닿지 못했습니다." }, { seen: false });
  assert.equal(state.searchNote, "PLM 에 닿지 못했습니다.");
});

test("the interval choices are the ones the picker offers", () => {
  assert.deepEqual(REFRESH_CHOICES, ["0", "15", "30", "60"]);
  for (const choice of REFRESH_CHOICES) {
    assert.ok(REFRESH_LABELS[choice], `${choice} 분에 붙일 이름이 있어야 한다`);
  }
  assert.equal(REFRESH_LABELS[0], "끔", "끄는 선택지가 있어야 한다");
});

test("the timer lives in the shell, so it keeps running off the PLM tab", () => {
  assert.match(appSource, /let plmRefreshTimer = null;/,
               "a view-scoped timer dies with the view on every tab switch");
  assert.match(appSource, /function schedulePlmRefresh\(\)/);
  // setInterval would stack runs when a PLM search outlasts the interval.
  assert.ok(!/setInterval\s*\(/.test(appSource), "re-arm with setTimeout, not setInterval");
  assert.match(appSource, /clearTimeout\(plmRefreshTimer\)/, "re-arming must not leave two timers");
});

test("the tab badge counts the unseen and the PLM tab clears it", () => {
  assert.match(appSource, /state\.plmState\?\.newCodes\?\.length/,
               "the nav reads the unseen count");
  assert.match(appSource, /view\.id === "plm"[\s\S]{0,200}newCodes = \[\]/,
               "opening the PLM view marks them seen");
});
