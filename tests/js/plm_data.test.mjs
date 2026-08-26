// backend/static/js/views/plm_data.js
//
// The cache exists to keep a theme toggle from hammering the corporate PLM API,
// so the tests that matter are about *when it does not call*: on a hit, and on a
// failure it must not remember. Run with `node --test tests/js/`.

import { test } from "node:test";
import assert from "node:assert/strict";

import {
  createDefectCache,
  defectCacheKey,
} from "../../backend/static/js/views/plm_data.js";

const DEFECT = { defectCode: "P190404-00007" };

/** Records every call so a test can assert the cache stopped making them. */
function fakeApi(overrides = {}) {
  const calls = [];
  const api = {
    calls,
    plmDefectDetails(division, codes) {
      calls.push(["details", division, codes]);
      return overrides.details ? overrides.details() : Promise.resolve({ defects: [{ defectCode: codes[0] }] });
    },
    plmHumanComments(division, code) {
      calls.push(["comments", division, code]);
      return overrides.comments ? overrides.comments() : Promise.resolve({ comments: [{ comment: "hi" }] });
    },
    plmFiles(division, code) {
      calls.push(["files", division, code]);
      return overrides.files ? overrides.files() : Promise.resolve({ files: [{ title: "log.zip" }] });
    },
  };
  return api;
}

const countOf = (api, kind) => api.calls.filter(([k]) => k === kind).length;

test("defectCacheKey joins division and code", () => {
  assert.equal(defectCacheKey("25", "P1"), "25:P1");
});

test("detail is fetched once, then served from the store", async () => {
  const api = fakeApi();
  const store = {};
  const cache = createDefectCache(api, store);

  const first = await cache.detail("25", DEFECT);
  const second = await cache.detail("25", DEFECT);

  assert.equal(countOf(api, "details"), 1, "second call must not hit the API");
  assert.equal(countOf(api, "comments"), 1);
  assert.deepEqual(second, first);
  assert.ok(store[defectCacheKey("25", DEFECT.defectCode)].detail);
});

test("attachments are fetched once, then served from the store", async () => {
  const api = fakeApi();
  const cache = createDefectCache(api, {});

  await cache.attachments("25", DEFECT);
  const again = await cache.attachments("25", DEFECT);

  assert.equal(countOf(api, "files"), 1);
  assert.deepEqual(again.files, [{ title: "log.zip" }]);
});

test("the same code under a different division is a separate entry", async () => {
  const api = fakeApi();
  const cache = createDefectCache(api, {});

  await cache.detail("25", DEFECT);
  await cache.detail("26", DEFECT);

  assert.equal(countOf(api, "details"), 2, "division must be part of the key");
});

test("a failed detail call is not cached, so the next visit retries", async () => {
  let attempt = 0;
  const api = fakeApi({
    details: () => {
      attempt += 1;
      return attempt === 1 ? Promise.reject(new Error("PLM down")) : Promise.resolve({ defects: [] });
    },
  });
  const store = {};
  const cache = createDefectCache(api, store);

  const failed = await cache.detail("25", DEFECT);
  assert.deepEqual(failed.details, { defects: [] },
    "the failed half comes back empty rather than throwing");
  assert.deepEqual(store, {}, "nothing may be written on failure");

  await cache.detail("25", DEFECT);
  assert.equal(countOf(api, "details"), 2, "must retry after a failure");
});

test("half a detail response is not cached", async () => {
  // Caching this would serve a detail pane whose comments are permanently gone.
  const api = fakeApi({ comments: () => Promise.reject(new Error("timeout")) });
  const store = {};
  const cache = createDefectCache(api, store);

  const entry = await cache.detail("25", DEFECT);
  assert.deepEqual(entry.comments, { comments: [] });
  assert.ok(entry.details.defects.length, "the half that succeeded is still returned");
  assert.deepEqual(store, {});
});

test("a failed attachment call is not cached", async () => {
  const api = fakeApi({ files: () => Promise.reject(new Error("PLM down")) });
  const store = {};
  const cache = createDefectCache(api, store);

  assert.deepEqual(await cache.attachments("25", DEFECT), { files: [] });
  assert.deepEqual(store, {});
});

test("invalidate drops only the named defect", async () => {
  const api = fakeApi();
  const store = {};
  const cache = createDefectCache(api, store);

  await cache.detail("25", DEFECT);
  await cache.detail("25", { defectCode: "OTHER-1" });
  cache.invalidate("25", DEFECT.defectCode);

  assert.equal(store[defectCacheKey("25", DEFECT.defectCode)], undefined);
  assert.ok(store[defectCacheKey("25", "OTHER-1")], "the other defect survives");

  await cache.detail("25", DEFECT);
  assert.equal(countOf(api, "details"), 3, "the dropped defect is refetched");
});

test("detail and attachments share one entry without clobbering each other", async () => {
  const api = fakeApi();
  const store = {};
  const cache = createDefectCache(api, store);

  await cache.detail("25", DEFECT);
  await cache.attachments("25", DEFECT);

  const entry = store[defectCacheKey("25", DEFECT.defectCode)];
  assert.ok(entry.detail, "attachments must not overwrite the detail half");
  assert.ok(entry.files);
});
