"use strict";

const $ = id => document.getElementById(id);
let catalog, selected, language = "sqlp", requestNumber = 0, completedRun = null;
const node = (tag, text, className) => {
  const element = document.createElement(tag);
  if (text !== undefined) element.textContent = String(text);
  if (className) element.className = className;
  return element;
};
const showError = message => { $("load-error").textContent = message; $("load-error").hidden = !message; };
const startQuery = () => selected ? selected[language] : "";

function runState(state) {
  const previous = completedRun ? "Displayed SQL, paths, and answers belong to the last completed run." : "No completed result is displayed yet.";
  const messages = {
    dirty: "Edits not run. Compile or execute to check the current query. " + previous,
    pending: "Running locally… " + previous,
    failed: "This request did not complete. The current query has not been scored. " + previous,
    completed: "Latest local attempt completed. No AI calls were made."
  };
  $("run-state").textContent = messages[state];
  $("run-state").className = "run-state " + state;
  if (state !== "completed") {
    $("answer-verdict").textContent = {dirty:"Edits not run",pending:"Running",failed:"Run not completed"}[state];
    $("answer-verdict").className = "pill";
  }
}

function queryEdited() {
  // An older request may still finish, but it no longer describes this editor.
  ++requestNumber;
  $("run").disabled = !selected;
  showError("");
  runState("dirty");
}

function setLanguage(value) {
  language = value;
  for (const candidate of ["sql", "sqlp"]) {
    $(candidate + "-tab").classList.toggle("active", candidate === language);
    $(candidate + "-tab").setAttribute("aria-pressed", String(candidate === language));
  }
  $("run").textContent = language === "sqlp" ? "Compile & run ↗" : "Execute SQL ↗";
  $("query").value = startQuery();
  renderStarting();
  execute();
}

function renderStarting() {
  $("starting-query").textContent = startQuery();
  const saved = selected?.saved || {};
  $("saved-label").textContent = saved.label || (saved.model ? "Recorded model: " + saved.model : "Prepared starting query; no model usage is recorded.");
  const usage = saved[language]?.token_usage || saved.token_usage?.[language] || saved.token_usage;
  $("recorded-usage").replaceChildren();
  if (usage && typeof usage === "object" && ["input_tokens", "output_tokens", "total_tokens"].every(k => Number.isSafeInteger(usage[k]))) {
    for (const key of ["input_tokens", "output_tokens", "total_tokens"]) {
      const item = node("div"); item.append(node("span", "Recorded " + key.replace("_tokens", "")), node("b", usage[key].toLocaleString()));
      $("recorded-usage").append(item);
    }
  } else $("recorded-usage").append(node("p", "Usage unknown. Local edits do not create model tokens.", "small muted"));
}

function selectScenario() {
  selected = catalog.scenarios.find(s => s.id === $("scenario").value);
  $("question-title").textContent = selected.question;
  $("snapshot").replaceChildren();
  for (const snapshot of selected.snapshots) {
    const option = node("option", snapshot.label + " · " + snapshot.expected_count + " expected rows"); option.value = snapshot.id; $("snapshot").append(option);
  }
  $("query").value = startQuery(); renderStarting(); execute();
}

function renderTable(target, columns, rows) {
  target.replaceChildren();
  if (!rows) { target.append(node("p", "Execution did not produce a scored answer.")); return; }
  if (!rows.length) { target.append(node("p", "Empty answer · 0 rows")); return; }
  const table = node("table"), header = node("tr");
  for (const column of columns) header.append(node("th", column));
  const head = node("thead"); head.append(header); table.append(head);
  const body = node("tbody");
  for (const row of rows.slice(0, 20)) {
    const tr = node("tr");
    for (const cell of row) tr.append(node("td", cell.type === "null" ? "NULL" : cell.value));
    body.append(tr);
  }
  table.append(body); target.append(table);
  if (rows.length > 20) target.append(node("p", "Showing 20 of " + rows.length + " rows; all were scored."));
}

function svgNode(tag, attrs, text) {
  const element = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [key, value] of Object.entries(attrs)) element.setAttribute(key, String(value));
  if (text !== undefined) element.textContent = String(text);
  return element;
}

function drawPath(trace, path, buttons, active) {
  for (let i = 0; i < buttons.length; i++) buttons[i].classList.toggle("active", i === active);
  $("graph").replaceChildren(); $("join-details").replaceChildren();
  const joins = path.join_ids.map(id => trace.joins[id]);
  const first = joins.length ? joins[0].source : path.column;
  const stops = [first, ...joins.map(join => join.target)];
  const step = 220, width = Math.max(560, 60 + stops.length * step);
  const svg = svgNode("svg", {width, height:140, viewBox:`0 0 ${width} 140`, role:"img", "aria-label": "Relationship path " + [path.root, ...path.features].join(".")});
  const defs = svgNode("defs", {}), marker = svgNode("marker", {id:"arrow",viewBox:"0 0 10 10",refX:9,refY:5,markerWidth:5,markerHeight:5,orient:"auto-start-reverse"});
  marker.append(svgNode("path",{d:"M 0 0 L 10 5 L 0 10 z",fill:"#235edb"})); defs.append(marker); svg.append(defs);
  for (let i = 0; i < stops.length; i++) {
    const x = 24 + i * step, stop = stops[i];
    if (i) {
      svg.append(svgNode("line",{x1:x-64,y1:66,x2:x-5,y2:66,class:"edge","marker-end":"url(#arrow)"}));
      svg.append(svgNode("text",{x:x-33,y:45,"text-anchor":"middle",class:"role"},joins[i-1].role));
    }
    svg.append(svgNode("rect",{x,y:45,width:156,height:45,rx:7,class:i === stops.length-1?"node terminal":"node"}));
    svg.append(svgNode("text",{x:x+78,y:63,"text-anchor":"middle",class:"node-title"},stop.table));
    svg.append(svgNode("text",{x:x+78,y:80,"text-anchor":"middle",class:"node-alias"},"alias " + stop.alias));
    if (i === stops.length-1) svg.append(svgNode("text",{x:x+78,y:113,"text-anchor":"middle",class:"column-label"},"→ " + path.column.column));
  }
  $("graph").append(svg);
  for (const join of joins) {
    const card = node("div",undefined,"join-detail");
    card.append(node("strong",join.role),node("span",`${join.source.alias}.${join.source.column} = ${join.target.alias}.${join.target.column}`));
    $("join-details").append(card);
  }
  if (!joins.length) $("join-details").append(node("p","Direct column: no relationship join is needed.","small muted"));
}

function renderPaths(trace) {
  $("paths").replaceChildren(); $("graph").replaceChildren(); $("join-details").replaceChildren();
  if (!trace?.paths?.length) { $("graph").append(node("p", language === "sql" ? "SQL is executed directly. Switch to SQLP to inspect its compiler-owned path trace." : "Compile a valid SQLP query to reveal its relationship paths.")); return; }
  const buttons = trace.paths.map((path, i) => {
    const button = node("button",[path.root,...path.features].join("."));
    button.title = path.clause.toUpperCase() + " occurrence " + (path.index+1);
    button.addEventListener("click",() => drawPath(trace,path,buttons,i));
    $("paths").append(button); return button;
  });
  let longest = 0;
  for (let i = 1; i < trace.paths.length; i++) if (trace.paths[i].join_ids.length > trace.paths[longest].join_ids.length) longest = i;
  drawPath(trace,trace.paths[longest],buttons,longest);
}

function renderResult(result) {
  const run = result.run, baseline = result.baseline;
  $("compiled").textContent = run.sql || "Compilation did not produce SQL.";
  $("parameters").textContent = run.parameters?.length ? run.parameters.map(p => `${p.type}: ${JSON.stringify(p.value)}`).join(" · ") : "None";
  $("join-count").textContent = run.trace ? `${run.trace.joins.length} checked joins` : language === "sql" ? "Direct SQL" : "No translation";
  const diagnostic = run.error ? run.error.diagnostic?.message || run.error.message || run.error.code || "Query failed" : "";
  $("compile-error").textContent = diagnostic; $("compile-error").hidden = !diagnostic;
  $("execution-note").textContent = "Manual intervention · 0 AI calls · SQLite read-only execution";
  $("answer-verdict").textContent = run.status !== "ok" ? "Query failed" : run.correct ? "Matches snapshot answer" : "Different from supplied answer";
  $("answer-verdict").className = "pill " + (run.correct ? "good" : "bad");
  $("answer-summary").replaceChildren();
  for (const [label, value] of [["Your answer",run.rows ? run.rows.length + " rows" : "Failed"],["Starting query on this snapshot",baseline.rows ? baseline.rows.length + " rows" : "Failed"],["Change from starting query",result.changed_from_baseline ? "Answer changed" : "Same answer"]]) {
    const metric = node("div"); metric.append(node("span",label),node("b",value)); $("answer-summary").append(metric);
  }
  renderTable($("actual-answer"),run.columns || [],run.rows);
  renderTable($("expected-answer"),result.expected_rows.length ? result.expected_rows[0].map((_,i) => "column " + (i+1)) : [],result.expected_rows);
  renderPaths(run.trace);
}

async function execute() {
  if (!selected) return;
  const number = ++requestNumber;
  const request = {scenario:selected.id,language,query:$("query").value,snapshot:$("snapshot").value};
  $("run").disabled = true; showError(""); runState("pending");
  try {
    const response = await fetch("/api/run",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(request)});
    const result = await response.json();
    if (number !== requestNumber) return;
    if (!response.ok) throw new Error(result.error || "Local execution failed");
    renderResult(result);
    completedRun = request;
    $("result-context").textContent = `Last completed run: ${request.scenario} · ${request.language.toUpperCase()} · ${result.snapshot_label || request.snapshot}. Displayed SQL, paths, and answers belong to this run.`;
    runState("completed");
  } catch (error) {
    if (number === requestNumber) { showError(error.message); runState("failed"); }
  }
  finally { if (number === requestNumber) $("run").disabled = false; }
}

const count = value => Number.isSafeInteger(value) && value >= 0;
const numberText = value => count(value) ? value.toLocaleString() : "Unknown";

function measurement(condition) {
  const countsKnown = condition && count(condition.attempts) && count(condition.correct) && condition.correct <= condition.attempts;
  const usageKnown = condition && ["input_tokens", "output_tokens", "total_tokens"].every(key => count(condition[key])) &&
    Number.isSafeInteger(condition.input_tokens + condition.output_tokens) &&
    condition.input_tokens + condition.output_tokens === condition.total_tokens;
  return {countsKnown, usageKnown};
}

function drawTokenChart(group) {
  const width = 760, origin = 82, barWidth = 570;
  const largest = Math.max(group.sql.total_tokens, group.sqlp.total_tokens);
  const svg = svgNode("svg", {viewBox:`0 0 ${width} 125`, role:"img", class:"token-chart",
    "aria-label":`Recorded total tokens: SQL ${group.sql.total_tokens}, SQLP ${group.sqlp.total_tokens}. Input and output components; output includes reported reasoning.`});
  for (const [index, language] of ["sql", "sqlp"].entries()) {
    const condition = group[language], y = 21 + index * 48;
    const inputWidth = largest ? condition.input_tokens / largest * barWidth : 0;
    const outputWidth = largest ? condition.output_tokens / largest * barWidth : 0;
    svg.append(svgNode("text",{x:19,y:y+17,class:"chart-language"},language.toUpperCase()));
    svg.append(svgNode("rect",{x:origin,y,width:barWidth,height:26,rx:3,class:"bar-track"}));
    svg.append(svgNode("rect",{x:origin,y,width:inputWidth,height:26,class:"bar-input"}));
    svg.append(svgNode("rect",{x:origin+inputWidth,y,width:outputWidth,height:26,class:"bar-output"}));
    svg.append(svgNode("text",{x:741,y:y+17,"text-anchor":"end",class:"chart-total"},condition.total_tokens.toLocaleString()));
  }
  return svg;
}

function renderMeasurementGroup(group) {
  const container = $("experiments"); container.replaceChildren();
  const context = node("div",undefined,"measurement-context");
  context.append(node("span",group.model || "Model unspecified"),node("span",group.dataset || "Dataset unspecified"),
    node("span",group.protocol || "Protocol unspecified"),node("span",`Batch size ${numberText(group.batch_size)}`));
  container.append(context);
  const validQuestions = count(group.question_count) && group.question_count > 0;
  const validRepetitions = count(group.repetitions) && group.repetitions > 0;
  container.append(node("p", validQuestions && validRepetitions ?
    `${group.question_count.toLocaleString()} unique questions · ${group.repetitions.toLocaleString()} repetition${group.repetitions === 1 ? "" : "s"}. Repeated questions are correlated observations, not additional independent tasks.` :
    "Question/repetition counts are unrecorded. Treat these as descriptive measurements.","small muted"));
  const checks = {sql:measurement(group.sql),sqlp:measurement(group.sqlp)};
  const cards = node("div",undefined,"measurement-cards");
  for (const language of ["sql","sqlp"]) {
    const data = group[language] || {}, checked = checks[language], card = node("div",undefined,"measurement-card " + language);
    const top = node("div",undefined,"measurement-heading");
    top.append(node("h3",language.toUpperCase()),node("span",checked.countsKnown ?
      `${data.correct.toLocaleString()} / ${data.attempts.toLocaleString()} oracle matches` : "Match counts unknown","pill"));
    card.append(top,node("strong",checked.usageKnown ? data.total_tokens.toLocaleString() : "Unknown","measurement-total"),
      node("span","total provider tokens","measurement-total-label"));
    const components = node("dl",undefined,"measurement-components");
    for (const [label,key] of [["Input","input_tokens"],["Output, including reasoning","output_tokens"]]) {
      const item = node("div"); item.append(node("dt",label),node("dd",checked.usageKnown ? numberText(data[key]) : "Unknown")); components.append(item);
    }
    card.append(components);
    const averageKnown = checked.usageKnown && checked.countsKnown && data.attempts > 0;
    const average = averageKnown ? (data.total_tokens / data.attempts).toLocaleString(undefined,{maximumFractionDigits:1}) : "Unknown";
    card.append(node("p",`${average} tokens / attempted question (amortized)`,"small muted"));
    cards.append(card);
  }
  container.append(cards);
  if (checks.sql.usageKnown && checks.sqlp.usageKnown) {
    const comparison = node("div",undefined,"measurement-comparison");
    const comparable = checks.sql.countsKnown && checks.sqlp.countsKnown && group.sql.attempts === group.sqlp.attempts;
    let delta = "Total-token percentage is undefined when the SQL total is zero.";
    if (!comparable) delta = "Attempt counts differ or are unknown; raw totals are not a matched per-question comparison.";
    else if (group.sql.total_tokens > 0) {
      const change = (group.sqlp.total_tokens - group.sql.total_tokens) / group.sql.total_tokens * 100;
      delta = `SQLP total vs SQL: ${change > 0 ? "+" : ""}${change.toFixed(2)}% (${(group.sqlp.total_tokens - group.sql.total_tokens).toLocaleString()} tokens).`;
    }
    comparison.append(node("p",delta,"measurement-delta"),drawTokenChart(group));
    const legend = node("div",undefined,"token-legend");
    legend.append(node("span","Input tokens","input-key"),node("span","Output tokens, including reported reasoning","output-key"));
    comparison.append(legend); container.append(comparison);
  }
  container.append(node("p","Totals belong to this entire recorded cohort. Per-question values are arithmetic amortization of shared call usage, not measured usage for an individual query. Changing this preset makes no model calls and does not re-score your edited query.","measurement-disclosure"));
}

function renderExperiments(experiments) {
  if (!experiments) return;
  $("experiments-panel").hidden = false;
  $("experiment-note").textContent = typeof experiments.note === "string" ? experiments.note : "";
  $("experiments").replaceChildren();
  $("experiment-controls").hidden = true;
  if (Array.isArray(experiments.groups) && experiments.groups.length &&
      experiments.groups.every(group => group && typeof group.id === "string" && group.id) &&
      new Set(experiments.groups.map(group => group.id)).size === experiments.groups.length) {
    $("experiment-controls").hidden = false;
    $("experiment-select").replaceChildren();
    for (const group of experiments.groups) {
      const option = node("option",group.label || `${group.model || "Unspecified model"} · ${group.protocol || "Recorded protocol"} · batch ${numberText(group.batch_size)}`);
      option.value = group.id; $("experiment-select").append(option);
    }
    $("experiment-select").onchange = () => {
      const group = experiments.groups.find(item => item.id === $("experiment-select").value);
      if (group) renderMeasurementGroup(group);
    };
    renderMeasurementGroup(experiments.groups[0]);
  } else if (Array.isArray(experiments.columns) && Array.isArray(experiments.rows)) {
    const table = node("table"), heading = node("tr"), body = node("tbody");
    for (const column of experiments.columns) heading.append(node("th",column));
    const head = node("thead"); head.append(heading); table.append(head);
    for (const values of experiments.rows) { const row = node("tr"); for (const value of values) row.append(node("td",value)); body.append(row); }
    table.append(body); $("experiments").append(table);
  } else $("experiments").append(node("pre",JSON.stringify(experiments,null,2)));
}

$("sqlp-tab").addEventListener("click",() => setLanguage("sqlp"));
$("sql-tab").addEventListener("click",() => setLanguage("sql"));
$("scenario").addEventListener("change",selectScenario);
$("snapshot").addEventListener("change",execute);
$("run").addEventListener("click",execute);
$("reset").addEventListener("click",() => { $("query").value = startQuery(); execute(); });
$("query").addEventListener("input",queryEdited);
$("query").addEventListener("keydown",event => { if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {event.preventDefault();execute();} });
fetch("/api/scenarios").then(async response => {
  if (!response.ok) throw new Error("Unable to load registered scenarios");
  catalog = await response.json();
  for (const scenario of catalog.scenarios) { const option = node("option",scenario.id); option.value = scenario.id; $("scenario").append(option); }
  for (const id of ["scenario","snapshot","run","reset"]) $(id).disabled = false;
  renderExperiments(catalog.experiments); selectScenario();
}).catch(error => showError(error.message));
