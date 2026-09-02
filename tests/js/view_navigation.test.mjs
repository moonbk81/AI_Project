// backend/static/js/app.js — ctx.goTo, and the views that call it.
//
// `goTo` ignores an unknown view id, so a typo ("dashbord") does not throw --
// the screen simply never changes and the analysis looks stuck on 100%. The ctx
// object is duck-typed and handed to views at render time, so nothing else
// checks either the method or the ids. Read both sides out of the source.
// Run with `node --test tests/js/`.

import { test } from "node:test";
import assert from "node:assert/strict";
import { readdir, readFile } from "node:fs/promises";
import path from "node:path";

const JS_ROOT = path.resolve(import.meta.dirname, "../../backend/static/js");

const appSource = await readFile(path.join(JS_ROOT, "app.js"), "utf8");
const viewIds = [...appSource.matchAll(/\{\s*id:\s*"([\w-]+)"/g)].map((match) => match[1]);

async function viewSources() {
  const dir = path.join(JS_ROOT, "views");
  const files = (await readdir(dir)).filter((name) => name.endsWith(".js"));
  return Promise.all(
    files.map(async (name) => [name, await readFile(path.join(dir, name), "utf8")]),
  );
}

test("app.js knows its views and offers a way to move between them", () => {
  assert.ok(viewIds.includes("dashboard"), "the dashboard view id must exist");
  assert.match(appSource, /goTo\(viewId\)/, "ctx.goTo is the views' way to navigate");
});

test("every view id a screen navigates to is a real view", async () => {
  const calls = [];
  for (const [name, source] of await viewSources()) {
    for (const match of source.matchAll(/ctx\.goTo\(\s*"([\w-]+)"\s*\)/g)) {
      calls.push([name, match[1]]);
    }
  }

  assert.ok(calls.length, "expected at least the files view to navigate on a finished analysis");
  for (const [name, id] of calls) {
    assert.ok(viewIds.includes(id), `${name} navigates to unknown view "${id}"`);
  }
});

test("a finished analysis in the files view moves to the dashboard", async () => {
  const sources = Object.fromEntries(await viewSources());

  assert.match(sources["files.js"], /ctx\.goTo\("dashboard"\)/);
  // PLM 탭은 분석이 끝나도 그 화면에 머문다 -- 결함에 코멘트를 남기는 흐름이 이어진다.
  assert.doesNotMatch(sources["plm.js"], /ctx\.goTo\(/);
});
