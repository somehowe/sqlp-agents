"use strict";
const assert = require("node:assert/strict");
const {test} = require("node:test");
const fs = require("node:fs");
const path = require("node:path");
const viewer = require("../web/viewer.js");
const clone = value => JSON.parse(JSON.stringify(value));
const root = path.resolve(__dirname, "..");

const trace = {
  joins: [{role: "university", source: {table: "AUTHOR", alias: "a", column: "uni_id"},
           target: {table: "UNIVERSITY", alias: "j0", column: "uid"}}],
  paths: [{clause: "select", index: 0, root: "a", features: ["university", "name"], join_ids: [0],
           column: {table: "UNIVERSITY", alias: "j0", column: "name"}}]
};
function report() {
  return {version: 1, origin: "manual", scope: "development", ok: true, cases: [{
    id: "example", question: "Which university?", column_count: 1,
    expected_rows: [[{type: "string", value: "Waterloo"}]], runs: [
      {language: "sql", query: "SELECT name FROM UNIVERSITY;", sql: "SELECT name FROM UNIVERSITY;",
       status: "ok", correct: true, columns: ["name"], rows: [[{type: "string", value: "Waterloo"}]], parameters: [], trace: null, token_usage: null},
      {language: "sqlp", query: "SELECT a.university.name FROM AUTHOR a;", sql: "SELECT ...;",
       status: "ok", correct: true, columns: ["name"], rows: [[{type: "string", value: "Waterloo"}]], parameters: [], trace: clone(trace), token_usage: null}
    ]}]};
}
class FakeNode {
  constructor(tag) { this.tagName = tag; this.childNodes = []; this.events = {}; this.attributes = {}; this._text = ""; this.value = ""; this.hidden = false; }
  set textContent(value) { this._text = String(value); this.childNodes = []; }
  get textContent() { return this._text + this.childNodes.map(child => child.textContent).join(""); }
  appendChild(child) { this.childNodes.push(child); return child; }
  replaceChildren(...children) { this._text = ""; this.childNodes = children; }
  addEventListener(type, callback) { this.events[type] = callback; }
  setAttribute(key, value) { this.attributes[key] = value; }
  set innerHTML(_) { throw new Error("Untrusted markup must not be interpreted"); }
}
function fakeDocument() {
  const ids = [...fs.readFileSync(path.join(root, "web/index.html"), "utf8").matchAll(/\bid="([^"]+)"/g)].map(match => match[1]);
  assert.equal(ids.length, new Set(ids).size, "HTML IDs must be unique");
  const nodes = Object.fromEntries(ids.map(id => [id, new FakeNode("div")]));
  return {nodes, getElementById: id => { assert.ok(nodes[id], "Existing HTML ID: " + id); return nodes[id]; }, createElement: tag => new FakeNode(tag)};
}
function descendants(node) { return [node, ...node.childNodes.flatMap(descendants)]; }
function mappingFixture(name = "AUTHOR", source = "researcher_rows", role = "mentor") {
  const logical = {source_table: name, feature: role, source_column: "mentor_aid", target_table: name, target_column: "aid"};
  const physical = {...logical, source_table: source, source_column: "mentor_key", target_table: source, target_column: "person_key"};
  const columns = [{name: "aid", source_column: "person_key", type: "integer", key: true},
                   {name: "name", source_column: "display_name", type: "string", key: false},
                   {name: "mentor_aid", source_column: "mentor_key", type: "integer", key: false}];
  return {version: 1, ok: true, origin: "restricted_arm_mapping", data_checked: true,
    identity_semantics: "single_primary_key_within_source_table", entity_results_supported: false, entity_equality_supported: false,
    entities: [{name, source_table: source, columns, identification: {kind: "single_primary_key", column: "aid", source_column: "person_key", type: "integer"},
                references: [{feature: role, logical, physical, declared_foreign_key_id: 0}]}],
    views: [{name, sql: 'CREATE VIEW "' + name + '" AS SELECT * FROM "' + source + '";'}],
    catalog: {version: 1, tables: [{name, columns: columns.map(col => ({name: col.name, type: col.type, unique: col.key}))}], references: [logical]},
    hashes: {catalog_sha256: "3".repeat(64), mapping_sha256: "4".repeat(64)}, snapshot_sha256: "1".repeat(64)};
}
function requestReport(mapping = mappingFixture()) {
  const value = report(); value.origin = "replay"; value.completed = true;
  value.manifest = {catalog_sha256: "2".repeat(64), data_snapshot_sha256: mapping.snapshot_sha256};
  value.requests = value.cases[0].runs.map((run, index) => {
    run.request_sha256 = (index ? "b" : "a").repeat(64);
    run.token_usage = {input_tokens: 10, output_tokens: 2, total_tokens: 12};
    run.generation = {model: "offline-fixture", raw_response: {model: "offline-fixture", output: run.query,
      usage: {input_tokens: 10, output_tokens: 2, total_tokens: 12,
              input_tokens_details: {cached_tokens: 5}, output_tokens_details: {reasoning_tokens: 1}}}};
    return {case_id: value.cases[0].id, language: run.language, request_sha256: run.request_sha256,
      payload: {model: "offline-fixture", instructions: "Corresponding language guide", input: [{role: "user",
        content: JSON.stringify({question: value.cases[0].question, schema_and_relationships: mapping.catalog})}]}};
  });
  return value;
}

test("saved report records render their recorded matches and available paths", () => {
  // An optional CLI path exercises an actual runner report. The default uses
  // the same public record shape without spawning a backend or a browser.
  const source = process.argv[2] ? fs.readFileSync(process.argv[2], "utf8") : JSON.stringify(report());
  const value = viewer.parseReport(source);
  assert.ok(value.cases.length > 0);
  const document = fakeDocument();
  viewer.createViewer(document).loadReport(value, "academic.json");
  assert.equal(document.nodes["workspace"].hidden, false);
  for (const language of ["sql", "sqlp"]) {
    const runs = value.cases.flatMap(item => item.runs.filter(run => run.language === language));
    const scored = runs.filter(run => typeof run.correct === "boolean");
    const expected = scored.length ? scored.filter(run => run.correct).length + " / " + scored.length : "Unscored";
    const metric = document.nodes.summary.childNodes.find(node => node.childNodes[0].textContent === language.toUpperCase() + " recorded matches");
    assert.ok(metric);
    assert.equal(metric.childNodes[1].textContent, expected);
  }
  const originNotes = {manual: /Hand-written/, replay: /not a new model run/,
    dry_run: /Requests were planned/, live: /does not authenticate/};
  assert.match(document.nodes["origin-note"].textContent, originNotes[value.origin]);
  assert.equal(descendants(document.nodes.comparison).filter(node => node.tagName === "article").length, 2);
  const firstSqlp = value.cases[0].runs.find(run => run.language === "sqlp");
  const hasPaths = Boolean(firstSqlp && firstSqlp.trace && firstSqlp.trace.paths.length);
  assert.equal(descendants(document.nodes.comparison).some(node => node.className === "path-select"), hasPaths);
  // Actual generated reports exercise every question, not only the first row.
  for (let index = 0; index < value.cases.length; index++) {
    document.nodes["case-select"].value = String(index);
    document.nodes["case-select"].onchange();
    for (const run of value.cases[index].runs) {
      assert.ok(document.nodes.comparison.textContent.includes(run.query));
      if (run.generation && run.generation.raw_response) {
        assert.ok(viewer.requestFor(value, value.cases[index].id, run).request);
        const responseId = run.generation.raw_response.id;
        if (typeof responseId === "string") assert.ok(document.nodes.comparison.textContent.includes(responseId));
      }
    }
  }
});

test("all origins remain distinguishable and invalid origin cannot load", () => {
  for (const origin of ["manual", "replay", "dry_run", "live"]) {
    const value = report(); value.origin = origin;
    assert.equal(viewer.validateReport(value).origin, origin);
    const document = fakeDocument(); viewer.createViewer(document).loadReport(value);
    assert.ok(document.nodes["origin-badge"].textContent.length);
    if (origin === "replay") assert.match(document.nodes["origin-note"].textContent, /not a new model run/);
    if (origin === "live") assert.match(document.nodes["origin-note"].textContent, /does not authenticate/);
  }
  const value = report(); value.origin = "__proto__";
  assert.throws(() => viewer.validateReport(value), /recognized origin/);
});

test("deliberate demo mistakes are disclosed as text beside the origin", () => {
  const value = report(), document = fakeDocument();
  value.demo_note = 'Hand-written development queries with deliberately introduced mistakes. <img src=x onerror="alert(1)">';
  viewer.createViewer(document).loadReport(value);
  const note = document.nodes["origin-note"];
  assert.match(note.textContent, /Hand-written development queries with deliberately introduced mistakes/);
  assert.ok(note.textContent.includes('<img src=x onerror="alert(1)">'));
  assert.equal(note.childNodes.length, 0);
  for (const invalid of [42, String.fromCharCode(0xd800), "x".repeat(1000001)]) {
    const malformed = report(); malformed.demo_note = invalid;
    assert.throws(() => viewer.validateReport(malformed), /demo note/);
  }
});

test("dry plans and setup failures do not invent query attempts", () => {
  const document = fakeDocument(), ui = viewer.createViewer(document);
  ui.loadReport({version: 1, origin: "dry_run", scope: "development", ok: true, completed: true, cases: [],
    requests: ["sql", "sqlp"].map(language => ({case_id: "planned", language, request_sha256: "a".repeat(64), payload: {}}))});
  assert.match(document.nodes["empty-notice"].textContent, /2 planned requests/);
  assert.equal(document.nodes.comparison.childNodes.length, 0);
  ui.loadReport({version: 1, origin: "live", ok: false, completed: false, error: {stage: "provider", message: "Transport unavailable"}});
  assert.match(document.nodes["record-error"].textContent, /Transport unavailable/);
  assert.equal(document.nodes["record-scope"].textContent, "Scope unknown");
  const missingScope = report(); delete missingScope.scope;
  assert.throws(() => viewer.validateReport(missingScope), /scope/);
  assert.match(document.nodes["origin-note"].textContent, /did not complete/);
  assert.equal(document.nodes["question-section"].hidden, true);
});

test("unknown usage stays unknown while known zero remains zero", () => {
  const value = report(), document = fakeDocument();
  value.cases[0].runs[1].token_usage = {input_tokens: 0, output_tokens: 0, total_tokens: 0};
  viewer.createViewer(document).loadReport(value);
  const cards = document.nodes.comparison.childNodes;
  assert.match(cards[0].textContent, /Total tokensUnknown/);
  assert.match(cards[1].textContent, /Total tokens0/);
  assert.equal(viewer.sumUsage(value.cases[0].runs), null);
  assert.deepEqual(viewer.sumUsage([value.cases[0].runs[1]]), {input_tokens: 0n, output_tokens: 0n, total_tokens: 0n});
});

test("token totals are exact and malformed counters are rejected", () => {
  const usage = {input_tokens: Number.MAX_SAFE_INTEGER - 1, output_tokens: 1, total_tokens: Number.MAX_SAFE_INTEGER};
  assert.equal(viewer.sumUsage([{token_usage: usage}, {token_usage: usage}]).total_tokens, 18014398509481982n);
  for (const invalid of [{input_tokens: -1, output_tokens: 1, total_tokens: 0}, {input_tokens: 1, output_tokens: 2, total_tokens: 4}, {input_tokens: 9007199254740992, output_tokens: 0, total_tokens: 9007199254740992}]) {
    const value = report(); value.cases[0].runs[0].token_usage = invalid;
    assert.throws(() => viewer.validateReport(value), /token/);
  }
});

test("int64, text, NULL, real, and BLOB cells preserve their encoded values", () => {
  const cases = [["int64", "9223372036854775807", "9223372036854775807"], ["int64", "-9223372036854775808", "-9223372036854775808"], ["string", "001", "001"], ["null", null, "NULL"], ["real", "inf", "inf"], ["real", "-inf", "-inf"], ["real", "1.25e+100", "1.25e+100"], ["blob", "00ff", "x'00ff'"]];
  for (const [type, value, formatted] of cases) assert.equal(viewer.formatCell({type, value}), formatted);
  for (const invalid of [{type: "int64", value: 9007199254740993}, {type: "int64", value: "9223372036854775808"}, {type: "blob", value: "0ff"}, {type: "null", value: "null"}, {type: "real", value: "hello"}, {type: "int64", value: "0".repeat(100000)}]) {
    const value = report(); value.cases[0].runs[0].rows = [[invalid]];
    assert.throws(() => viewer.validateReport(value));
  }
});

test("quoted path components preserve role identity", () => {
  assert.equal(viewer.formatPath({root: "w", features: ["author.university", "name"]}), 'w."author.university".name');
  assert.equal(viewer.formatPath({root: "w", features: ["author", "university", "name"]}), "w.author.university.name");
  assert.equal(viewer.formatPath({root: "a", features: ['a"b', "select"]}), 'a."a""b"."select"');
});

test("path selection exposes only its ordered physical joins and final column", () => {
  const value = report();
  value.cases[0].runs[1].trace.paths.push({clause: "select", index: 1, root: "a", features: ["name"], join_ids: [], column: {table: "AUTHOR", alias: "a", column: "name"}});
  const document = fakeDocument(); viewer.createViewer(document).loadReport(value);
  const panel = descendants(document.nodes.comparison).find(node => node.className === "path-inspector");
  const select = descendants(panel).find(node => node.className === "path-select");
  assert.match(panel.textContent, /a\.uni_id = j0\.uid/);
  assert.match(panel.textContent, /Read j0\.name from UNIVERSITY/);
  select.value = "1"; select.events.change();
  const output = descendants(panel).find(node => node.className === "path-output");
  assert.match(output.textContent, /Direct column/);
  assert.match(output.textContent, /Read a\.name from AUTHOR/);
  assert.doesNotMatch(output.textContent, /uni_id/);
});

test("malformed trace indices, roles, chains, and terminals fail before rendering", () => {
  const mutations = [value => value.paths[0].join_ids[0] = 9, value => value.joins[0].role = "publisher", value => value.joins[0].source.alias = "other", value => value.paths[0].column.alias = "a", value => value.paths[0].column.column = "country"];
  for (const mutate of mutations) {
    const value = report(); mutate(value.cases[0].runs[1].trace);
    assert.throws(() => viewer.validateReport(value), /[Tt]race|join/);
  }
});

test("query, answer, question, column, and filename markup is rendered as text", () => {
  const attack = '<img src=x onerror="alert(1)">';
  const value = report(), document = fakeDocument();
  value.cases[0].question = attack;
  value.cases[0].runs[0].query = attack;
  value.cases[0].runs[0].columns = [attack];
  value.cases[0].runs[0].rows = [[{type: "string", value: attack}]];
  viewer.createViewer(document).loadReport(value, attack);
  assert.equal(document.nodes["question-text"].textContent, attack);
  assert.equal(document.nodes["record-name"].textContent, attack);
  const cells = descendants(document.nodes.comparison).filter(node => node.tagName === "td");
  assert.equal(cells[0].textContent, attack);
  assert.equal(cells[0].childNodes.length, 0);
});

test("empty answers and failed attempts remain distinct", () => {
  const value = report(), document = fakeDocument();
  value.cases[0].expected_rows = [];
  value.cases[0].runs[0].rows = [];
  Object.assign(value.cases[0].runs[1], {status: "error", correct: false, columns: null, rows: null, sql: null, trace: null,
    error: {stage: "compile", diagnostic: {stage: "parser", message: "Unexpected token"}}});
  viewer.createViewer(document).loadReport(value);
  assert.match(document.nodes.comparison.childNodes[0].textContent, /0 rows · Empty answer/);
  assert.match(document.nodes.comparison.childNodes[1].textContent, /No answer recorded/);
  assert.match(document.nodes.comparison.childNodes[1].textContent, /compile \/ parser/);
});

test("missing and unscored conditions are not labeled incorrect", () => {
  const value = report(), document = fakeDocument();
  value.cases[0].runs.pop(); value.cases[0].runs[0].correct = null;
  viewer.createViewer(document).loadReport(value);
  assert.match(document.nodes.comparison.textContent, /Unscored answer/);
  assert.match(document.nodes.comparison.textContent, /No attempt/);
  assert.match(document.nodes.summary.textContent, /Unscored/);
});

test("duplicate cases, conditions, inconsistent rows and impossible run states are rejected", () => {
  const mutations = [value => value.cases.push(clone(value.cases[0])), value => value.cases[0].runs[1].language = "sql", value => value.cases[0].runs[0].rows[0].push({type: "null", value: null}), value => value.cases[0].runs[0].status = "error", value => value.cases[0].runs[0].rows = null];
  for (const mutate of mutations) { const value = report(); mutate(value); assert.throws(() => viewer.validateReport(value)); }
});

test("JSON and resource bounds reject oversized or malformed records", () => {
  assert.throws(() => viewer.parseReport("not JSON"), /not valid JSON/);
  assert.throws(() => viewer.parseReport(" ".repeat(viewer.MAX_BYTES + 1)), /16 MiB/);
  const value = report(); value.cases[0].question = "x".repeat(1000001);
  assert.throws(() => viewer.validateReport(value), /question/);
  const unicode = report(); unicode.cases[0].question = "Invalid " + String.fromCharCode(0xd800);
  assert.throws(() => viewer.validateReport(unicode), /Unicode/);
  const valid = report(); valid.cases[0].question = "University 🌿";
  assert.equal(viewer.parseReport(JSON.stringify(valid)).cases[0].question, "University 🌿");
});

test("preview limits disclose that the complete recorded answer was scored", () => {
  const value = report(), document = fakeDocument();
  value.cases[0].runs[0].rows = Array.from({length: 101}, () => [{type: "string", value: "Waterloo"}]);
  viewer.createViewer(document).loadReport(value);
  assert.match(document.nodes.comparison.childNodes[0].textContent, /first 100 of 101 rows/);
  assert.match(document.nodes.comparison.childNodes[0].textContent, /runner scored the full recorded answer/);
});

test("browser file handling rejects invalid UTF-8 and keeps the previous record", async () => {
  const document = fakeDocument(); viewer.createViewer(document).loadReport(report(), "previous.json");
  const bytes = new Uint8Array([0x80]);
  await document.nodes["report-file"].events.change({target: {files: [{name: "bad.json", size: 1, arrayBuffer: async () => bytes.buffer}]}});
  assert.equal(document.nodes["load-error"].hidden, false);
  assert.equal(document.nodes["record-name"].textContent, "previous.json");
  assert.match(document.nodes["load-status"].textContent, /previous record/);
});

test("an earlier slow file read cannot overwrite a newer selection", async () => {
  const document = fakeDocument(); viewer.createViewer(document);
  let finish;
  const first = new Promise(resolve => { finish = resolve; });
  const handler = document.nodes["report-file"].events.change;
  const pending = handler({target: {files: [{name: "first.json", size: 10, arrayBuffer: () => first}]}});
  const bytes = new TextEncoder().encode(JSON.stringify(report()));
  await handler({target: {files: [{name: "second.json", size: bytes.length, arrayBuffer: async () => bytes.buffer}]}});
  finish(bytes.buffer); await pending;
  assert.equal(document.nodes["record-name"].textContent, "second.json");
});

test("condition drawers inspect the matching request and raw usage as inert JSON text", () => {
  const value = requestReport(), document = fakeDocument();
  const attack = '<img src=x onerror="alert(1)"> "quoted" 🌿';
  value.requests[0].payload.instructions = attack;
  value.cases[0].runs[0].generation.raw_response.extra = attack;
  viewer.createViewer(document).loadReport(value);
  const sql = document.nodes.comparison.childNodes[0];
  assert.match(sql.textContent, /Recorded request hashes agree/);
  assert.match(sql.textContent, /does not recompute the payload hash or authenticate/);
  const request = descendants(sql).find(node => node.className === "request-payload");
  assert.ok(request);
  assert.ok(request.textContent.includes(JSON.stringify(attack)));
  const raw = descendants(sql).find(node => node.className === "raw-response");
  assert.ok(raw.textContent.includes(JSON.stringify(attack)));
  for (const node of descendants(sql).filter(node => node.tagName === "pre")) assert.equal(node.childNodes.length, 0);
  const usage = descendants(sql).find(node => node.className === "code-block raw-usage");
  assert.deepEqual(JSON.parse(usage.textContent), value.cases[0].runs[0].generation.raw_response.usage);
  assert.match(sql.textContent, /can overlap totals/);
  assert.match(sql.textContent, /Total tokens12/);
  assert.equal(document.nodes["mapping-panel"].hidden, true);
});

test("missing, mismatched, duplicate, and cross-case requests are never associated with an attempt", () => {
  for (const [mutate, warning] of [
    [value => value.requests[0].request_sha256 = "c".repeat(64), /Request hash mismatch/],
    [value => value.requests.push(clone(value.requests[0])), /Ambiguous saved requests/],
    [value => value.requests[0].case_id = "another-question", /No saved request payload/],
    [value => delete value.cases[0].runs[0].request_sha256, /has no request hash/]
  ]) {
    const value = requestReport(), document = fakeDocument(); mutate(value);
    viewer.createViewer(document).loadReport(value);
    const sql = document.nodes.comparison.childNodes[0];
    assert.match(sql.textContent, warning);
    assert.equal(descendants(sql).some(node => node.className === "request-payload"), false);
    assert.equal(descendants(sql).some(node => node.className === "raw-response"), true);
  }
  const document = fakeDocument(); viewer.createViewer(document).loadReport(report());
  assert.match(document.nodes.comparison.textContent, /No saved request payload/);
  assert.match(document.nodes.comparison.textContent, /No raw provider response or raw usage was recorded/);
});

test("optional mappings show physical roles and DDL with honest structural and snapshot association", () => {
  const attack = '<img src=x onerror="alert(1)">';
  const mapping = mappingFixture("研究者🌿", 'source"table', 'mentor.' + attack), document = fakeDocument();
  const ui = viewer.createViewer(document);
  ui.loadMapping(mapping, attack);
  assert.match(document.nodes["mapping-association"].textContent, /not verified.*no comparison/);
  assert.equal(document.nodes["mapping-name"].textContent, attack);
  const panel = document.nodes["mapping-entities"];
  assert.ok(panel.textContent.includes(attack));
  assert.match(panel.textContent, /person_key/);
  assert.match(panel.textContent, /mentor_key/);
  const ddl = descendants(panel).find(node => node.className === "mapping-view");
  assert.equal(descendants(ddl).find(node => node.tagName === "pre").textContent, mapping.views[0].sql);
  assert.equal(descendants(ddl).find(node => node.tagName === "pre").childNodes.length, 0);
  // Python ensure_ascii=False (ARM) and ensure_ascii=True (pilot) hashes differ
  // for this same Unicode catalog. Compare facts, never these hash strings.
  const value = requestReport(mapping);
  assert.notEqual(value.manifest.catalog_sha256, mapping.hashes.catalog_sha256);
  ui.loadReport(value);
  assert.match(document.nodes["mapping-association"].textContent, /association is consistent/);
  assert.match(document.nodes["mapping-association"].textContent, /hash strings are displayed only/);
  value.manifest.data_snapshot_sha256 = "f".repeat(64); ui.loadReport(value);
  assert.match(document.nodes["mapping-association"].textContent, /snapshot hashes differ/);
  value.manifest.data_snapshot_sha256 = mapping.snapshot_sha256;
  const task = JSON.parse(value.requests[0].payload.input[0].content);
  task.schema_and_relationships.tables[0].columns[1].name = "different";
  value.requests[0].payload.input[0].content = JSON.stringify(task); ui.loadReport(value);
  assert.match(document.nodes["mapping-association"].textContent, /catalog facts differ/);
  ui.loadReport(report());
  assert.match(document.nodes["mapping-association"].textContent, /association not verified/);
  if (process.argv[3]) {
    const actual = viewer.parseMapping(fs.readFileSync(process.argv[3], "utf8"));
    ui.loadMapping(actual, "actual-mapping.json");
    assert.equal(document.nodes["mapping-entities"].childNodes.length, actual.entities.length);
    assert.match(document.nodes["mapping-association"].textContent, /not verified/);
  }
});

test("malformed mappings and deeply nested or unsafe raw metadata are rejected without replacing valid records", async () => {
  const document = fakeDocument(), ui = viewer.createViewer(document);
  ui.loadReport(report(), "previous-report.json"); ui.loadMapping(mappingFixture(), "previous-mapping.json");
  for (const mutate of [value => value.entities[0].identification.column = "name",
    value => value.entities[0].references[0].physical.target_column = "wrong",
    value => value.catalog.tables[0].columns[0].unique = false,
    value => value.views.push(clone(value.views[0])), value => value.hashes.catalog_sha256 = "unknown",
    value => value.entity_equality_supported = true]) {
    const malformed = mappingFixture(); mutate(malformed);
    assert.throws(() => ui.loadMapping(malformed), /[Mm]apping|[Mm]apped|view|ARM/);
    assert.equal(document.nodes["mapping-name"].textContent, "previous-mapping.json");
  }
  const value = requestReport();
  value.cases[0].runs[0].generation.raw_response.unsafe = 9007199254740992;
  assert.throws(() => ui.loadReport(value), /safe/);
  const nested = requestReport(); let tail = nested.cases[0].runs[0].generation.raw_response;
  for (let i = 0; i < 40; i++) { tail.child = {}; tail = tail.child; }
  assert.throws(() => ui.loadReport(nested), /nesting/);
  const bytes = new Uint8Array([0x80]);
  await document.nodes["mapping-file"].events.change({target: {files: [{name: "bad.json", size: 1, arrayBuffer: async () => bytes.buffer}]}});
  assert.equal(document.nodes["mapping-error"].hidden, false);
  assert.equal(document.nodes["mapping-name"].textContent, "previous-mapping.json");
  assert.equal(document.nodes["record-name"].textContent, "previous-report.json");
});

test("an earlier mapping read cannot overwrite a newer mapping or change the comparison", async () => {
  const document = fakeDocument(), ui = viewer.createViewer(document);
  ui.loadReport(report(), "comparison.json");
  let finish;
  const pendingBytes = new Promise(resolve => { finish = resolve; });
  const handler = document.nodes["mapping-file"].events.change;
  const pending = handler({target: {files: [{name: "old.json", size: 10, arrayBuffer: () => pendingBytes}]}});
  const bytes = new TextEncoder().encode(JSON.stringify(mappingFixture()));
  await handler({target: {files: [{name: "new.json", size: bytes.length, arrayBuffer: async () => bytes.buffer}]}});
  finish(bytes.buffer); await pending;
  assert.equal(document.nodes["mapping-name"].textContent, "new.json");
  assert.equal(document.nodes["record-name"].textContent, "comparison.json");
});
