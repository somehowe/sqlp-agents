"use strict";

// Run the browser source against a small DOM and queued, local-only transport.
// No backend, browser package, model service, or credentials are used here.
const assert = require("node:assert/strict");
const {test} = require("node:test");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const root = path.resolve(__dirname, "..");
const source = fs.readFileSync(path.join(root, "web/lab.js"), "utf8");
const html = fs.readFileSync(path.join(root, "web/lab.html"), "utf8");
const clone = value => JSON.parse(JSON.stringify(value));
const settle = () => new Promise(resolve => setImmediate(resolve));

class Element {
  constructor(tag) {
    this.tagName = tag;
    this.children = [];
    this.attributes = {};
    this.listeners = {};
    this._text = "";
    this._value = undefined;
    this.className = "";
    this.hidden = false;
    this.disabled = false;
    this.classList = {toggle: (name, active) => {
      const classes = new Set(this.className.split(/\s+/).filter(Boolean));
      if (active === undefined ? !classes.has(name) : active) classes.add(name);
      else classes.delete(name);
      this.className = [...classes].join(" ");
    }};
  }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return this._text + this.children.map(child => child.textContent).join(""); }
  set value(value) { this._value = String(value); }
  get value() { return this._value === undefined ? (this.tagName === "select" ? this.children[0]?.value || "" : "") : this._value; }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this._text = ""; this.children = children; if (this.tagName === "select") this._value = undefined; }
  setAttribute(key, value) { this.attributes[key] = String(value); }
  addEventListener(type, listener) { (this.listeners[type] ||= []).push(listener); }
  fire(type, event = {}) {
    for (const listener of this.listeners[type] || []) listener(event);
    if (typeof this["on" + type] === "function") this["on" + type](event);
  }
  set innerHTML(_) { throw new Error("Untrusted markup must stay inert text"); }
}

function nodesWithClass(element, className) {
  return [element, ...element.children.flatMap(child => nodesWithClass(child, className))]
    .filter(child => child.className.split(/\s+/).includes(className));
}

function scenario(id = "waterloo") {
  return {
    id, question: "Which university does the author's colleague work at?",
    sql: "SELECT name FROM UNIVERSITY;",
    sqlp: "SELECT a.university.name FROM AUTHOR a;",
    snapshots: [{id:"original",label:"Original",expected_count:1},{id:"contrast1",label:"Counterexample",expected_count:2}],
    saved: {label:"Recorded response",model:"recorded-model",sql:{token_usage:{input_tokens:90,output_tokens:10,total_tokens:100}},
      sqlp:{token_usage:{input_tokens:120,output_tokens:10,total_tokens:130}}}
  };
}

function result(value = "Waterloo", overrides = {}) {
  const rows = [[{type:"string",value}]];
  return {
    run:{status:"ok",correct:true,sql:"SELECT checked_name FROM UNIVERSITY;",parameters:[],columns:["name"],rows,
      trace:{joins:[{role:"university",source:{table:"AUTHOR",alias:"a",column:"university_id"},target:{table:"UNIVERSITY",alias:"j0",column:"id"}}],
        paths:[{root:"a",features:["university","name"],clause:"select",index:0,join_ids:[0],column:{table:"UNIVERSITY",alias:"j0",column:"name"}}]}},
    baseline:{rows},expected_rows:rows,changed_from_baseline:false,snapshot_label:"Original",...overrides
  };
}

function group(id = "single", overrides = {}) {
  return {id,label:"Recorded " + id,model:"recorded-model",dataset:"Development data",protocol:"Paired generation",batch_size:1,
    question_count:12,repetitions:3,
    sql:{attempts:36,correct:36,input_tokens:60003,output_tokens:2386,total_tokens:62389},
    sqlp:{attempts:36,correct:35,input_tokens:61551,output_tokens:2005,total_tokens:63556},...overrides};
}

async function lab({scenarios = [scenario(),scenario("other")],experiments,beforeLoad} = {}) {
  const elements = {}, created = [], calls = [];
  for (const match of html.matchAll(/<([a-z][\w-]*)\b[^>]*\bid="([^"]+)"[^>]*>/g)) {
    assert.equal(elements[match[2]], undefined, "HTML IDs are unique");
    const element = new Element(match[1]);
    element.hidden = /\bhidden\b/.test(match[0]);
    element.disabled = /\bdisabled\b/.test(match[0]);
    elements[match[2]] = element;
  }
  const document = {
    getElementById: id => { assert.ok(elements[id], "Browser source references existing HTML ID: " + id); return elements[id]; },
    createElement: tag => { const element = new Element(tag); created.push(element); return element; },
    createElementNS: (_, tag) => document.createElement(tag)
  };
  const fetch = (url, options) => {
    assert.ok(url === "/api/scenarios" || url === "/api/run", "Only registered local API routes may be requested");
    let resolve, reject;
    const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
    calls.push({url,options,resolve,reject});
    return promise;
  };
  vm.runInNewContext(source,{document,fetch,console},{filename:"web/lab.js"});
  assert.equal(calls.length,1);
  assert.equal(calls[0].url,"/api/scenarios");
  if (beforeLoad) beforeLoad(elements,calls);
  calls[0].resolve({ok:true,json:async () => clone({scenarios,experiments})});
  await settle();
  assert.equal(calls.length,2,"Loading the first scenario starts one local query");
  return {
    elements,created,calls,
    async respond(index,value,ok = true) { calls[index].resolve({ok,json:async () => clone(value)}); await settle(); },
    async finish(value = result()) { await this.respond(calls.length - 1,value); },
    edit(query) { elements.query.value = query; elements.query.fire("input"); }
  };
}

test("a local run renders its checked answer, trace, context, and unchanged recorded usage",async () => {
  const ui = await lab(), e = ui.elements;
  const request = JSON.parse(ui.calls[1].options.body);
  assert.equal(request.scenario,"waterloo");
  assert.equal(request.language,"sqlp");
  assert.equal(request.snapshot,"original");
  assert.equal(ui.calls[1].options.method,"POST");
  await ui.finish();
  assert.match(e["actual-answer"].textContent,/Waterloo/);
  assert.match(e["join-details"].textContent,/a\.university_id = j0\.id/);
  assert.match(e["result-context"].textContent,/waterloo · SQLP · Original/);
  assert.match(e["answer-verdict"].textContent,/Matches snapshot/);
  assert.match(e["run-state"].textContent,/No AI calls/);
  assert.match(e["recorded-usage"].textContent,/130/);
  assert.equal(e.run.disabled,false);
});

test("choosing SQL before scenario loading finishes waits for the registered scenario",async () => {
  const ui = await lab({beforeLoad:(elements,calls) => {
    elements["sql-tab"].fire("click");
    assert.equal(calls.length,1,"An unregistered query cannot run before metadata loads");
  }});
  assert.equal(JSON.parse(ui.calls[1].options.body).language,"sql");
  assert.equal(ui.elements.query.value,scenario().sql);
  await ui.finish();
});

test("editing a completed query visibly marks it unrun while retaining the identified last answer",async () => {
  const ui = await lab(), e = ui.elements;
  await ui.finish();
  const oldAnswer = e["actual-answer"].textContent, oldSQL = e.compiled.textContent, usage = e["recorded-usage"].textContent;
  ui.edit("SELECT a.mentor.university.name FROM AUTHOR a;");
  assert.match(e["run-state"].textContent,/Edits not run/);
  assert.match(e["run-state"].textContent,/SQL, paths, and answers belong to the last completed run/);
  assert.equal(e["answer-verdict"].textContent,"Edits not run");
  assert.doesNotMatch(e["answer-verdict"].className,/good/);
  assert.equal(e["actual-answer"].textContent,oldAnswer);
  assert.equal(e.compiled.textContent,oldSQL);
  assert.equal(e["recorded-usage"].textContent,usage);
  assert.equal(ui.calls.length,2,"Typing cannot execute queries or call a model");
  assert.equal(e.run.disabled,false);
});

test("editing during a request invalidates its late success and releases Run for the next query",async () => {
  const ui = await lab(), e = ui.elements;
  assert.equal(e.run.disabled,true);
  ui.edit("SELECT a.name FROM AUTHOR a;");
  assert.equal(e.run.disabled,false,"An invalidated request cannot keep Run stuck");
  await ui.respond(1,result("STALE"));
  assert.doesNotMatch(e["actual-answer"].textContent,/STALE/);
  assert.match(e["run-state"].textContent,/Edits not run/);
  assert.equal(e.run.disabled,false);
  e.run.fire("click");
  assert.equal(JSON.parse(ui.calls[2].options.body).query,"SELECT a.name FROM AUTHOR a;");
  await ui.finish(result("CURRENT"));
  assert.match(e["actual-answer"].textContent,/CURRENT/);
  assert.match(e["run-state"].textContent,/completed/);
});

test("older responses cannot overwrite a newer scenario, language, or snapshot",async t => {
  for (const change of ["scenario","language","snapshot"]) {
    for (const oldFirst of [true,false]) await t.test(change + (oldFirst ? " old response first" : " old response last"),async () => {
      const ui = await lab(), e = ui.elements;
      if (change === "scenario") { e.scenario.value = "other"; e.scenario.fire("change"); }
      if (change === "language") e["sql-tab"].fire("click");
      if (change === "snapshot") { e.snapshot.value = "contrast1"; e.snapshot.fire("change"); }
      assert.equal(ui.calls.length,3);
      if (oldFirst) {
        await ui.respond(1,result("STALE"));
        assert.equal(e.run.disabled,true,"An old request must not enable Run during the current request");
        assert.match(e["run-state"].textContent,/Running/);
      }
      await ui.respond(2,result("CURRENT",{snapshot_label:change === "snapshot" ? "Counterexample" : "Original"}));
      if (!oldFirst) await ui.respond(1,result("STALE"));
      assert.match(e["actual-answer"].textContent,/CURRENT/);
      assert.doesNotMatch(e["actual-answer"].textContent,/STALE/);
      assert.equal(e.run.disabled,false);
      assert.match(e["result-context"].textContent,change === "scenario" ? /other/ : change === "language" ? /SQL ·/ : /Counterexample/);
    });
  }
});

test("an invalidated transport failure cannot replace dirty state or a newer answer",async () => {
  const ui = await lab(), e = ui.elements;
  ui.edit("SELECT a.name FROM AUTHOR a;");
  e.run.fire("click");
  await ui.respond(2,result("CURRENT"));
  ui.calls[1].reject(new Error("OLD FAILURE"));
  await settle();
  assert.equal(e["load-error"].hidden,true);
  assert.match(e["actual-answer"].textContent,/CURRENT/);
  assert.match(e["run-state"].textContent,/completed/);
  assert.equal(e.run.disabled,false);
});

test("a current transport failure identifies stale output and leaves a usable Run button",async () => {
  const ui = await lab(), e = ui.elements;
  await ui.finish();
  ui.edit("BROKEN REQUEST");
  e.run.fire("click");
  await ui.respond(2,{error:"Read-only request rejected"},false);
  assert.match(e["load-error"].textContent,/Read-only request rejected/);
  assert.match(e["run-state"].textContent,/current query has not been scored/);
  assert.match(e["run-state"].textContent,/last completed run/);
  assert.match(e["actual-answer"].textContent,/Waterloo/);
  assert.equal(e["answer-verdict"].textContent,"Run not completed");
  assert.equal(e.run.disabled,false);
});

test("reset and keyboard execution capture the current editor without any model call",async () => {
  const ui = await lab(), e = ui.elements;
  await ui.finish();
  ui.edit("SELECT a.name FROM AUTHOR a;");
  let prevented = false;
  e.query.fire("keydown",{ctrlKey:true,key:"Enter",preventDefault:() => { prevented = true; }});
  assert.equal(prevented,true);
  assert.equal(JSON.parse(ui.calls[2].options.body).query,e.query.value);
  await ui.finish();
  e.reset.fire("click");
  assert.equal(e.query.value,scenario().sqlp);
  assert.equal(JSON.parse(ui.calls[3].options.body).query,scenario().sqlp);
  assert.ok(ui.calls.every(call => ["/api/scenarios","/api/run"].includes(call.url)));
});

test("recorded presets show complete cohort totals, correlated repetitions, and arithmetic amortization",async () => {
  const ui = await lab({experiments:{groups:[group()]}}), e = ui.elements;
  await ui.finish();
  const text = e.experiments.textContent;
  assert.match(text,/62,389/);
  assert.match(text,/63,556/);
  assert.match(text,/60,003/);
  assert.match(text,/2,386/);
  assert.match(text,/36 \/ 36 oracle matches/);
  assert.match(text,/35 \/ 36 oracle matches/);
  assert.match(text,/\+1\.87% \(1,167 tokens\)/);
  assert.match(text,/1,733 tokens \/ attempted question \(amortized\)/);
  assert.match(text,/Repeated questions are correlated/);
  assert.match(text,/not measured usage for an individual query/);
  assert.equal(e["experiment-controls"].hidden,false);
  const svg = e.experiments.children.flatMap(child => child.children).find(child => child.tagName === "svg");
  assert.ok(svg);
  assert.match(svg.attributes["aria-label"],/62389.*63556/);
});

test("switching recorded model presets changes only measurements, even while a local run is pending",async () => {
  const second = group("batch",{batch_size:12,sql:{attempts:36,correct:36,input_tokens:8000,output_tokens:2000,total_tokens:10000},
    sqlp:{attempts:36,correct:36,input_tokens:6000,output_tokens:2000,total_tokens:8000}});
  const ui = await lab({experiments:{groups:[group(),second]}}), e = ui.elements;
  await ui.finish();
  ui.edit("SELECT a.name FROM AUTHOR a;");
  e.run.fire("click");
  const calls = ui.calls.length, query = e.query.value, score = e["answer-verdict"].textContent,
    answer = e["actual-answer"].textContent, state = e["run-state"].textContent, usage = e["recorded-usage"].textContent;
  e["experiment-select"].value = "batch"; e["experiment-select"].fire("change");
  assert.match(e.experiments.textContent,/-20\.00%/);
  assert.match(e.experiments.textContent,/Batch size 12/);
  assert.equal(ui.calls.length,calls,"Preset selection must make zero network requests");
  assert.equal(e.query.value,query);
  assert.equal(e["answer-verdict"].textContent,score);
  assert.equal(e["actual-answer"].textContent,answer);
  assert.equal(e["run-state"].textContent,state);
  assert.equal(e["recorded-usage"].textContent,usage);
  assert.equal(e.run.disabled,true);
});

test("unknown, partial, negative, unsafe, and inconsistent usage never become fabricated zero totals",async t => {
  const invalid = [undefined,{}, {input_tokens:1,output_tokens:2}, {input_tokens:1,output_tokens:2,total_tokens:4},
    {input_tokens:-1,output_tokens:2,total_tokens:1},
    {input_tokens:Number.MAX_SAFE_INTEGER,output_tokens:1,total_tokens:Number.MAX_SAFE_INTEGER + 1}];
  for (const [index,data] of invalid.entries()) await t.test("unknown usage " + index,async () => {
    const sqlp = {...data,attempts:36,correct:36};
    const ui = await lab({experiments:{groups:[group("unknown",{sqlp})]}});
    const card = nodesWithClass(ui.elements.experiments,"measurement-card")[1];
    assert.equal(nodesWithClass(card,"measurement-total")[0].textContent,"Unknown");
    assert.match(card.textContent,/Unknown tokens \/ attempted question/);
    assert.equal(nodesWithClass(ui.elements.experiments,"measurement-comparison").length,0);
  });
});

test("known zero usage remains zero and a zero SQL denominator does not invent a percentage",async () => {
  const empty = {attempts:0,correct:0,input_tokens:0,output_tokens:0,total_tokens:0};
  const ui = await lab({experiments:{groups:[group("zero",{sql:empty,sqlp:empty})]}}), e = ui.elements;
  assert.deepEqual(nodesWithClass(e.experiments,"measurement-total").map(node => node.textContent),["0","0"]);
  assert.match(e.experiments.textContent,/percentage is undefined/);
  assert.doesNotMatch(e.experiments.textContent,/NaN|Infinity|0\.00%/);
  assert.match(e.experiments.textContent,/Unknown tokens \/ attempted question/);
});

test("different or impossible attempt counts cannot be presented as a matched percentage",async () => {
  for (const sqlp of [{...group().sqlp,attempts:35,correct:35},{...group().sqlp,correct:37}]) {
    const ui = await lab({experiments:{groups:[group("unmatched",{sqlp})]}});
    assert.match(ui.elements.experiments.textContent,/Attempt counts differ or are unknown/);
    assert.doesNotMatch(ui.elements.experiments.textContent,/\+1\.87%/);
  }
});

test("questions, queries, diagnoses, result cells, and recorded metadata remain inert text",async () => {
  const attack = '<img src=x onerror="alert(1)">';
  const first = scenario(); first.question = attack; first.sqlp = attack; first.saved.label = attack;
  const ui = await lab({scenarios:[first],experiments:{groups:[group("markup",{label:attack,model:attack})],note:attack}}), e = ui.elements;
  const response = result(attack);
  response.run.sql = attack;
  response.run.columns = [attack];
  response.run.error = {message:attack};
  response.run.trace.joins[0].role = attack;
  await ui.finish(response);
  assert.equal(e["question-title"].textContent,attack);
  assert.equal(e.query.value,attack);
  assert.equal(e["saved-label"].textContent,attack);
  assert.equal(e.compiled.textContent,attack);
  assert.equal(e["compile-error"].textContent,attack);
  assert.match(e["actual-answer"].textContent,/<img/);
  assert.match(e["join-details"].textContent,/<img/);
  assert.match(e.experiments.textContent,/<img/);
  assert.equal(e["experiment-note"].textContent,attack);
  assert.ok(ui.created.every(node => !["img","script","iframe"].includes(node.tagName)));
});

test("legacy tables and invalid preset data retain an inert readable fallback",async () => {
  const attack = "<script>throw 1</script>";
  const legacy = await lab({experiments:{columns:[attack],rows:[[attack]],note:attack}});
  assert.equal(legacy.elements["experiment-controls"].hidden,true);
  assert.equal(legacy.elements["experiment-note"].textContent,attack);
  assert.match(legacy.elements.experiments.textContent,/<script>/);
  const invalid = await lab({experiments:{groups:[group("duplicate"),group("duplicate")]}});
  assert.equal(invalid.elements["experiment-controls"].hidden,true);
  assert.match(invalid.elements.experiments.textContent,/"groups"/);
});
