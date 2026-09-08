// backend/static/js/views/files.js — 목록을 접는 규칙
//
// 적재된 로그와 분석 작업은 쓸수록 늘기만 해서, 카드가 화면을 넘겨 한눈에 안
// 들어왔다. 지금 볼 것만 펴 두는 게 이 규칙의 전부인데, 그 "지금 볼 것" 이
// 틀리면 접어 둔 것이 오히려 방해가 된다. Run with `node --test tests/js/`.

import { test } from "node:test";
import assert from "node:assert/strict";

import {
  INGESTED_PREVIEW,
  splitForDisplay,
  splitJobs,
} from "../../backend/static/js/views/files.js";

const ACTIVE = "radio_active.json";
const MINE = "radio_mine.json";

/** 보는 중 -> 내가 올린 것 -> 나머지. 화면이 쓰는 그 순위 함수. */
const rank = (file) => (file === ACTIVE ? 0 : file.startsWith("radio_") ? 1 : 2);

test("보는 중인 파일은 목록이 아무리 길어도 접히지 않는다", () => {
  const files = [...Array(30).keys()].map((index) => `other_${index}.json`).concat(ACTIVE);

  const { shown, folded } = splitForDisplay(files, rank);

  assert.equal(shown[0], ACTIVE);
  assert.equal(shown.length, INGESTED_PREVIEW);
  assert.equal(folded.length, files.length - INGESTED_PREVIEW);
  assert.ok(!folded.includes(ACTIVE));
});

test("내가 올린 것이 남의 것보다 먼저 펴진다", () => {
  const { shown } = splitForDisplay([ACTIVE, "other_a.json", MINE, "other_b.json"], rank);

  assert.deepEqual(shown, [ACTIVE, MINE, "other_a.json", "other_b.json"]);
});

test("같은 순위끼리는 이름순이라 목록이 늘어도 자리가 흔들리지 않는다", () => {
  const before = splitForDisplay(["other_c.json", "other_a.json"], rank).shown;
  const after = splitForDisplay(["other_c.json", "other_a.json", "other_b.json"], rank).shown;

  assert.deepEqual(before, ["other_a.json", "other_c.json"]);
  assert.deepEqual(after, ["other_a.json", "other_b.json", "other_c.json"]);
});

test("접을 것이 없으면 전부 편다", () => {
  const { shown, folded } = splitForDisplay([ACTIVE, MINE], rank);

  assert.equal(shown.length, 2);
  assert.deepEqual(folded, []);
});

test("돌고 있는 작업은 전부 펴고 끝난 것은 접는다", () => {
  const jobs = [
    { job_id: "a", status: "running" },
    { job_id: "b", status: "running" },
    { job_id: "c", status: "done" },
    { job_id: "d", status: "error" },
  ];

  const { shown, folded } = splitJobs(jobs);

  assert.deepEqual(shown.map((job) => job.job_id), ["a", "b"]);
  assert.deepEqual(folded.map((job) => job.job_id), ["c", "d"]);
});

test("돌고 있는 것이 없으면 마지막 결과 하나는 펴 둔다", () => {
  // 방금 끝난 분석까지 접히면 카드가 빈 것처럼 보인다.
  const jobs = [
    { job_id: "newest", status: "done" },
    { job_id: "older", status: "error" },
    { job_id: "oldest", status: "done" },
  ];

  const { shown, folded } = splitJobs(jobs);

  assert.deepEqual(shown.map((job) => job.job_id), ["newest"]);
  assert.deepEqual(folded.map((job) => job.job_id), ["older", "oldest"]);
});

test("작업은 들어온 순서를 그대로 둔다", () => {
  // 서버가 최신순으로 주므로, 여기서 다시 정렬하면 최신이 뒤로 밀린다.
  const jobs = [
    { job_id: "z_newest", status: "running" },
    { job_id: "a_older", status: "running" },
  ];

  assert.deepEqual(splitJobs(jobs).shown.map((job) => job.job_id), ["z_newest", "a_older"]);
});

test("작업이 하나도 없으면 펼 것도 접을 것도 없다", () => {
  const { shown, folded } = splitJobs([]);

  assert.deepEqual(shown, []);
  assert.deepEqual(folded, []);
});
