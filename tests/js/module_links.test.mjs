// Every named import under backend/static/js must name something the target
// module actually exports.
//
// The frontend has no build step, so nothing checks this. Moving a helper
// between files -- `panel` from views/plm.js to viz.js, say -- can leave behind
// an import that only fails when the browser reaches that line.
//
// The check is static on the importing side: the imports are read out of the
// source rather than executed. That matters for app.js, which calls boot() on
// load; importing it here would run DOM code and drown the real signal. Only
// the *target* modules get imported, and none of those self-execute.

import { test } from "node:test";
import assert from "node:assert/strict";
import { readdir, readFile } from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";

const JS_ROOT = path.resolve(import.meta.dirname, "../../backend/static/js");

async function jsFiles(dir) {
  const found = [];
  for (const entry of await readdir(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) found.push(...(await jsFiles(full)));
    else if (entry.name.endsWith(".js")) found.push(full);
  }
  return found.sort();
}

/** `import { a, b as c } from "./x.js"` -> [{ names: ["a", "b"], from: "./x.js" }] */
function namedImports(source) {
  const out = [];
  const pattern = /import\s*\{([\s\S]*?)\}\s*from\s*["']([^"']+)["']/g;
  for (const [, inner, from] of source.matchAll(pattern)) {
    const names = inner
      .split(",")
      .map((part) => part.trim().split(/\s+as\s+/)[0].trim())
      .filter(Boolean);
    out.push({ names, from });
  }
  return out;
}

test("every named frontend import resolves", async (t) => {
  const files = await jsFiles(JS_ROOT);
  assert.ok(files.length >= 10, `expected the frontend modules, found ${files.length}`);

  let checked = 0;
  for (const file of files) {
    const rel = path.relative(JS_ROOT, file);
    const source = await readFile(file, "utf8");
    const imports = namedImports(source).filter((entry) => entry.from.startsWith("."));

    await t.test(rel, async () => {
      for (const { names, from } of imports) {
        const target = path.resolve(path.dirname(file), from);
        const module = await import(pathToFileURL(target).href);
        for (const name of names) {
          assert.ok(
            name in module,
            `${rel} imports { ${name} } from "${from}", which does not export it`,
          );
          checked += 1;
        }
      }
    });
  }
  assert.ok(checked > 20, `expected to check a real number of imports, got ${checked}`);
});
