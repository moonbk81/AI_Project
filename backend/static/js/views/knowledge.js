// 지식 베이스 — 기록된 분석 사례를 찾아보고, 방금 받은 답변을 사례로 남긴다.

import { api } from "../api.js";
import { el, field, fmt, panel, tile, tileRow } from "../viz.js";
import { caseForm } from "./case_form.js";

const ANY = "전체";

function picker(values) {
  const node = el("select");
  for (const value of [ANY, ...values]) node.append(new Option(value, value));
  return node;
}

function caseBlock(item) {
  const summary = (item.note || "").slice(0, 60).replace(/\n/g, " ");
  const fold = el("details", "fold");
  fold.append(el("summary", null, `[${item.severity}] [${item.model_name}] ${summary}...`));

  fold.append(el("h4", "sub-head", "분석 코멘트 / 조치 내용"), el("pre", null, item.note));
  fold.append(tileRow([
    tile("단말 모델", item.model_name),
    tile("AP (HW)", item.hardware),
    tile("OS (SDK)", item.android_sdk),
    tile("중요도", item.severity),
  ]));
  fold.append(el("p", "card-note", `Radio: ${item.radio} · Kernel: ${item.kernel}`));
  fold.append(el("p", "card-note", `참조 로그 ID: ${item.target_ids} · 사례 ID: ${item.short_id}`));
  return fold;
}

function searchCard(body, cases, filters) {
  const pickers = {
    model_name: picker(filters.model_name),
    hardware: picker(filters.hardware),
    android_sdk: picker(filters.android_sdk),
    severity: picker(filters.severity),
  };

  const row = el("div", "kpi-row");
  row.append(field("단말 모델", pickers.model_name), field("AP(Hardware)", pickers.hardware),
             field("Android SDK", pickers.android_sdk), field("중요도", pickers.severity));

  const count = el("p", "card-note");
  const list = el("div", "stack");

  const draw = () => {
    const matching = cases.filter((item) =>
      Object.entries(pickers).every(([key, node]) => node.value === ANY || String(item[key]) === node.value));

    count.textContent = `조회 결과: ${fmt.count(matching.length)}건`;
    list.replaceChildren();
    if (!matching.length) {
      list.append(el("div", "empty", "조건에 맞는 사례가 없습니다."));
      return;
    }
    for (const item of matching) list.append(caseBlock(item));
  };

  for (const node of Object.values(pickers)) node.addEventListener("change", draw);
  body.append(row, count, list);
  draw();
}

function registerCard(body, retrieval, onSaved) {
  if (!retrieval?.ids?.length) {
    body.append(el("div", "empty", "채팅에서 답변을 하나 받은 뒤에 그 결과를 사례로 등록할 수 있습니다."));
    return;
  }
  body.append(caseForm(retrieval, { onSaved }));
}

export async function renderKnowledge(mount, sourceFile, ctx) {
  const wrap = el("section", "band");
  wrap.append(el("h2", "band-title", "분석 사례"));
  const grid = el("div", "grid");
  wrap.append(grid);
  mount.append(wrap);

  const search = panel("사례 조회", "등록된 장애 분석과 조치 내용");
  const register = panel("사례 등록", "채팅의 마지막 답변을 사례로 남깁니다. 다른 답변은 채팅에서 바로 등록하세요.");
  grid.append(search.section, register.section);

  const draw = async () => {
    search.body.replaceChildren(el("div", "empty", "불러오는 중..."));
    register.body.replaceChildren();

    try {
      const body = await api.knowledge();
      search.body.replaceChildren();
      if (!body.cases.length) {
        search.body.append(el("div", "empty", "등록된 분석 사례가 없습니다."));
      } else {
        searchCard(search.body, body.cases, body.filters);
      }
    } catch (error) {
      search.body.replaceChildren(el("div", "empty", "사례를 불러오지 못했습니다."));
    }

    registerCard(register.body, ctx.lastRetrieval, draw);
  };

  await draw();
}
