"use strict";

const $ = id => document.getElementById(id);
let catalog, selected, language = "sqlp", requestNumber = 0, completedRun = null;
let activeTrace = null, activePath = null, pathButtons = [], activePathIndex = 0;
let graphMode = "query", hopStep = -1, playback = null, graphView = null, graphBounds = null;
let graphSvg = null, graphContext = null, sqlSegments = [], previewNumber = 0;
const palette = ["#5068e9", "#a16bc6", "#d79a46", "#3b9c8b"];
const shorten = (value, limit = 24) => String(value).length > limit ? String(value).slice(0, limit - 1) + "…" : String(value);
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
  $("query-file-label").textContent = language === "sqlp" ? "query.sqlp" : "query.sql";
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
  const grouped = new Map();
  for (const row of rows) {
    const key = JSON.stringify(row.map(cell => [cell.type, cell.value === undefined ? null : cell.value]));
    if (!grouped.has(key)) grouped.set(key, {row, count:0});
    grouped.get(key).count++;
  }
  const repeated = grouped.size < rows.length;
  const table = node("table"), header = node("tr");
  for (const column of columns) header.append(node("th", column));
  if (repeated) header.append(node("th", "Copies"));
  const head = node("thead"); head.append(header); table.append(head);
  const body = node("tbody");
  for (const item of [...grouped.values()].slice(0, 20)) {
    const tr = node("tr");
    for (const cell of item.row) tr.append(node("td", cell.type === "null" ? "NULL" : cell.value));
    if (repeated) {
      const copies = node("td"); copies.append(node("span", "×" + item.count, "multiplicity" + (item.count > 1 ? " repeated" : ""))); tr.append(copies);
    }
    body.append(tr);
  }
  table.append(body); target.append(table);
  if (repeated) target.append(node("p", `${rows.length} rows · ${grouped.size} distinct displayed values. Multiplicity is preserved in scoring.`));
  if (grouped.size > 20) target.append(node("p", "Showing 20 of " + grouped.size + " distinct rows; all " + rows.length + " rows were scored."));
}

function svgNode(tag, attrs, text) {
  const element = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [key, value] of Object.entries(attrs)) element.setAttribute(key, String(value));
  if (text !== undefined) element.textContent = String(text);
  return element;
}

function stopPlayback() {
  if (playback !== null && typeof clearTimeout === "function") clearTimeout(playback);
  playback = null;
  $("path-play").textContent = "▶ Follow path";
  $("path-play").classList.toggle("is-playing", false);
}

function quoteIdentifier(value) { return '"' + String(value).replace(/"/g, '""') + '"'; }
function joinText(join) {
  const column = value => quoteIdentifier(value.alias) + "." + quoteIdentifier(value.column);
  return `JOIN ${quoteIdentifier(join.target.table)} AS ${quoteIdentifier(join.target.alias)} ON ${column(join.source)} = ${column(join.target)}`;
}
function renderSQL(sql, trace) {
  const target = $("compiled"); target.replaceChildren(); sqlSegments = [];
  if (!sql) { target.textContent = "Compilation did not produce SQL."; return; }
  const ranges = [];
  for (const [id, join] of (trace?.joins || []).entries()) {
    const text = joinText(join), position = sql.indexOf(text);
    if (position >= 0) ranges.push({id, position, end:position + text.length});
  }
  ranges.sort((a,b) => a.position-b.position);
  let position = 0;
  for (const range of ranges) {
    if (range.position < position) continue;
    if (range.position > position) target.append(node("span",sql.slice(position,range.position)));
    const span = node("span",sql.slice(range.position,range.end),"sql-line");
    span.setAttribute("data-join-id", range.id);
    span.title = "Compiler join " + range.id + " · " + trace.joins[range.id].role;
    span.addEventListener("click",() => {
      const index = activeTrace?.paths.findIndex(path => path.join_ids.includes(range.id));
      if (index >= 0) selectPath(index);
    });
    target.append(span); sqlSegments.push({element:span, id:range.id}); position = range.end;
  }
  if (position < sql.length) target.append(node("span",sql.slice(position)));
}

function traceModel(trace) {
  const nodes = new Map(), children = new Map(), edges = [];
  function add(column) {
    if (!nodes.has(column.alias)) nodes.set(column.alias,{table:column.table,alias:column.alias,fields:[],depth:0});
    const item = nodes.get(column.alias);
    if (column.column && !item.fields.includes(column.column)) item.fields.push(column.column);
    return item;
  }
  for (const [id, join] of trace.joins.entries()) {
    add(join.source); add(join.target);
    if (!children.has(join.source.alias)) children.set(join.source.alias,[]);
    children.get(join.source.alias).push(join.target.alias);
    edges.push({...join,id});
  }
  for (const path of trace.paths) add(path.column);
  const targetAliases = new Set(edges.map(edge => edge.target.alias));
  const roots = [...nodes.keys()].filter(alias => !targetAliases.has(alias));
  let leaf = 0;
  const visited = new Set();
  function place(alias, depth) {
    if (visited.has(alias)) return nodes.get(alias).y;
    visited.add(alias);
    const item = nodes.get(alias); item.depth = depth; item.x = 35 + depth * 260;
    const descendants = children.get(alias) || [];
    item.y = descendants.length ? descendants.map(child => place(child,depth+1)).reduce((a,b)=>a+b,0)/descendants.length : 46 + leaf++ * 166;
    return item.y;
  }
  for (const root of roots) place(root,0);
  for (const alias of nodes.keys()) if (!visited.has(alias)) place(alias,0);
  return {nodes:[...nodes.values()], edges, width:Math.max(570,70 + Math.max(0,...[...nodes.values()].map(item=>item.depth))*260 + 190),height:Math.max(260,leaf*166 + 35)};
}

function catalogModel() {
  const metadata = graphContext?.catalog || selected?.catalog;
  if (!metadata?.tables?.length) return null;
  const tables = metadata.tables, nodes = tables.map((table,index) => ({table:table.name,alias:table.name,
    fields:table.columns.slice(0,3).map(column=>column.name),x:32 + (index%3)*285,y:38 + Math.floor(index/3)*172}));
  const edges = (metadata.references || []).map((ref,id) => ({id,role:ref.feature,
    source:{table:ref.source_table,alias:ref.source_table,column:ref.source_column},
    target:{table:ref.target_table,alias:ref.target_table,column:ref.target_column}}));
  return {nodes,edges,width:Math.min(3,tables.length)*285+15,height:Math.ceil(tables.length/3)*172+28};
}

function setGraphView() {
  if (graphSvg && graphView) graphSvg.setAttribute("viewBox",`${graphView.x} ${graphView.y} ${graphView.width} ${graphView.height}`);
}
function fitGraph() {
  if (!graphBounds) return;
  graphView = {x:0,y:0,width:graphBounds.width,height:graphBounds.height}; setGraphView();
}
function zoomGraph(factor) {
  if (!graphView || !graphBounds) return;
  const width = Math.max(220,Math.min(graphBounds.width*2,graphView.width*factor));
  const height = Math.max(150,Math.min(graphBounds.height*2,graphView.height*factor));
  graphView = {x:graphView.x+(graphView.width-width)/2,y:graphView.y+(graphView.height-height)/2,width,height}; setGraphView();
}
function attachPan(svg) {
  let dragging = null;
  svg.addEventListener("pointerdown",event => {
    if (!graphView || event.button !== 0 || event.target?.closest?.(".table-node")) return;
    dragging = {x:event.clientX,y:event.clientY,view:{...graphView}};
    if (svg.setPointerCapture) svg.setPointerCapture(event.pointerId);
  });
  svg.addEventListener("pointermove",event => {
    if (!dragging) return;
    const box = svg.getBoundingClientRect();
    graphView.x = dragging.view.x - (event.clientX-dragging.x)*dragging.view.width/Math.max(1,box.width);
    graphView.y = dragging.view.y - (event.clientY-dragging.y)*dragging.view.height/Math.max(1,box.height);
    setGraphView();
  });
  svg.addEventListener("pointerup",() => { dragging = null; });
  svg.addEventListener("pointercancel",() => { dragging = null; });
  svg.addEventListener("wheel",event => { event.preventDefault(); zoomGraph(event.deltaY > 0 ? 1.12 : .89); },{passive:false});
}

async function inspectTable(item) {
  const context = graphContext;
  if (!context?.scenario || !context?.snapshot) return;
  const token = ++previewNumber;
  $("node-inspector").hidden = false;
  $("node-inspector").scrollIntoView?.({block:"nearest"});
  $("node-inspector-title").textContent = item.table + (graphMode === "query" ? " · alias " + item.alias : "");
  $("node-inspector-note").textContent = "Loading real records from the displayed run’s frozen snapshot…";
  $("node-records").replaceChildren(node("p","Reading registered table…"));
  try {
    const response = await fetch("/api/preview",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({scenario:context.scenario,snapshot:context.snapshot,table:item.table})});
    const data = await response.json();
    if (token !== previewNumber) return;
    if (!response.ok) throw new Error(data.error || "Table preview unavailable");
    $("node-inspector-note").textContent = `${data.row_count.toLocaleString()} table rows · showing at most ${data.preview_limit} sample records · ${context.scenario} / ${context.snapshot}. These are table samples, not answer provenance.` + (data.columns_truncated ? " Some columns are omitted from this preview." : "");
    renderTable($("node-records"),data.columns,data.rows);
    $("node-inspector").scrollIntoView?.({block:"nearest"});
  } catch(error) {
    if (token === previewNumber) { $("node-inspector-note").textContent = error.message; $("node-records").replaceChildren(); }
  }
}

function graphCard(svg,item,pathAliases,pathColumn,stepAlias,isSchema) {
  const active = pathAliases.has(item.alias), focus = item.alias === stepAlias;
  const group = svgNode("g",{transform:`translate(${item.x} ${item.y})`,class:"table-node"+(isSchema?" schema-node":""),tabindex:0,role:"button",
    "aria-label":`Inspect ${item.table}${isSchema?"":" alias " + item.alias} sample records`,"data-table":item.table,"data-alias":item.alias});
  group.append(svgNode("title",{},`${item.table}${isSchema?"":" AS " + item.alias}. Click to inspect actual table samples, not result provenance.`));
  group.append(svgNode("rect",{width:194,height:112,rx:9,class:"node"+(active?" active-node":" dim-node")+(focus?" focus-node":"")}));
  group.append(svgNode("path",{d:"M9 1 H185 Q193 1 193 9 V43 H1 V9 Q1 1 9 1",class:"node-header"+(active?" active-header":"")}));
  group.append(svgNode("text",{x:14,y:19,class:"node-alias"},isSchema?"CATALOG TABLE":"ALIAS " + shorten(item.alias,23)));
  group.append(svgNode("text",{x:14,y:35,class:"node-title"},shorten(item.table,23)));
  const fields = [...item.fields];
  if (pathColumn?.alias === item.alias && !fields.includes(pathColumn.column)) fields.unshift(pathColumn.column);
  const visible = fields.slice(0,3);
  if (pathColumn?.alias === item.alias && !visible.includes(pathColumn.column)) visible[visible.length-1] = pathColumn.column;
  for (const [index,field] of visible.entries()) {
    const terminal = active && pathColumn?.alias === item.alias && pathColumn.column === field;
    group.append(svgNode("circle",{cx:16,cy:59+index*17,r:2.5,class:terminal?"terminal-dot":"field-dot"}));
    group.append(svgNode("text",{x:26,y:62+index*17,class:"field-label"+(terminal?" terminal-field":"")},shorten(field,23)));
  }
  group.addEventListener("click",() => inspectTable(item));
  group.addEventListener("keydown",event => { if (event.key === "Enter" || event.key === " ") {event.preventDefault();inspectTable(item);} });
  svg.append(group);
}

function drawGraph() {
  $("graph").replaceChildren(); graphSvg = null;
  const schema = graphMode === "schema";
  const model = schema ? catalogModel() : activeTrace ? traceModel(activeTrace) : null;
  if (!model || !model.nodes.length) {
    $("graph").append(node("p", language === "sql" ? "SQL executes directly. Choose Catalog map to explore the supplied relationships, or SQLP to inspect a compiler-owned path trace." : "Run a valid SQLP query to reveal its branching relationship graph."));
    return;
  }
  graphBounds = model;
  if (!graphView) {
    const compact=typeof matchMedia === "function" && matchMedia("(max-width:600px)").matches;
    graphView = {x:0,y:0,width:Math.min(compact?360:1050,model.width),height:Math.min(compact?390:510,model.height)};
  }
  const pathIds = new Set(activePath?.join_ids || []);
  const progressIds = new Set(hopStep < 0 ? activePath?.join_ids || [] : (activePath?.join_ids || []).slice(0,hopStep));
  const pathAliases = new Set(activePath ? [activePath.root,...(activePath.join_ids || []).map(id=>activeTrace.joins[id].target.alias)] : []);
  const stepAlias = activePath ? hopStep === 0 ? activePath.root : hopStep > 0 ? activeTrace.joins[activePath.join_ids[hopStep-1]]?.target.alias : activePath.column.alias : null;
  const svg = svgNode("svg",{width:model.width,height:model.height,role:"img","aria-label":schema?"Approved reference catalog map":"All compiler-owned query relationship branches; selected path " + (activePath?[activePath.root,...activePath.features].join("."):"none")});
  const defs=svgNode("defs",{});
  for (const [id,color] of [["graph-arrow","#c0cce0"],["active-arrow","#5068e9"]]) {
    const marker=svgNode("marker",{id,viewBox:"0 0 10 10",refX:9,refY:5,markerWidth:6,markerHeight:6,orient:"auto-start-reverse"});
    marker.append(svgNode("path",{d:"M0 0 L10 5 L0 10 z",fill:color}));defs.append(marker);
  }
  svg.append(defs);
  const byAlias = new Map(model.nodes.map(item=>[item.alias,item]));
  for (const edge of model.edges) {
    const source=byAlias.get(edge.source.alias), target=byAlias.get(edge.target.alias); if (!source || !target) continue;
    const isSelected = schema ? activePath && activePath.join_ids.some(id=>{const j=activeTrace.joins[id];return j.source.table===edge.source.table && j.target.table===edge.target.table && j.role===edge.role;}) : pathIds.has(edge.id);
    const progressed = schema ? isSelected : progressIds.has(edge.id);
    let d,labelX,labelY;
    if (source === target) {
      d=`M${source.x+150} ${source.y} C${source.x+150} ${source.y-38} ${source.x+230} ${source.y-38} ${source.x+230} ${source.y+35} C${source.x+230} ${source.y+75} ${source.x+220} ${source.y+82} ${source.x+194} ${source.y+82}`;
      labelX=source.x+206;labelY=source.y-11;
    } else {
      const x1=source.x+194,y1=source.y+56,x2=target.x-3,y2=target.y+56;
      const bend = Math.max(35,Math.abs(x2-x1)*.5);
      d=`M${x1} ${y1} C${x1+bend} ${y1} ${x2-bend} ${y2} ${x2} ${y2}`;
      labelX=(x1+x2)/2;labelY=(y1+y2)/2-12;
    }
    const line=svgNode("path",{d,class:"edge"+(schema?" schema-edge":"")+(isSelected?(progressed?" active-edge":" future-edge"):"")+(progressed && playback!==null?" traversing":""),"marker-end":"url(#"+(progressed?"active-arrow":"graph-arrow")+")","data-join-id":schema?"catalog-"+edge.id:edge.id});
    line.append(svgNode("title",{},`${edge.role}: ${edge.source.table}.${edge.source.column} = ${edge.target.table}.${edge.target.column}`));svg.append(line);
    svg.append(svgNode("text",{x:labelX,y:labelY,"text-anchor":"middle",class:"role"+(isSelected?" active-role":"")},shorten(edge.role,18)));
  }
  for (const item of model.nodes) graphCard(svg,item,schema?new Set():pathAliases,activePath?.column,stepAlias,schema);
  graphSvg=svg;setGraphView();attachPan(svg);$("graph").append(svg);
  $("trace-overview").textContent = schema ? `${model.nodes.length} catalog tables · ${model.edges.length} declared roles` : `${model.nodes.length} table instances · ${model.edges.length} physical joins`;
  const tableCounts = new Map();for(const item of model.nodes)tableCounts.set(item.table,(tableCounts.get(item.table)||0)+1);
  const repeated = [...tableCounts].filter(([,n])=>n>1);
  $("graph-caption").textContent = schema ? "Declared relationships supplied to both languages. A catalog edge is not a discovered connection. Select a table to inspect actual sample records." : repeated.length ? repeated.map(([table,n])=>`${table} appears as ${n} distinct aliases`).join("; ") + ". Roles keep those instances separate. Click a node for real table samples; drag or zoom to explore." : "Blue joins belong to the selected path; muted branches belong to other path occurrences. Click a node for real table samples. Drag or zoom to explore.";
}

function drawPath(trace,path,buttons,active,keepViewport=false) {
  activeTrace=trace;activePath=path;activePathIndex=active;
  if(!keepViewport)graphView=null;
  for(let i=0;i<buttons.length;i++)buttons[i].classList.toggle("active",i===active);
  const joins=path.join_ids.map(id=>trace.joins[id]);
  $("current-path").textContent=[path.root,...path.features].join(".");
  $("path-length").textContent=joins.length+" reference hop"+(joins.length===1?"":"s");
  $("path-status").textContent=hopStep<0?"All "+joins.length+" joins":hopStep===0?"Start · "+path.root:`Hop ${hopStep}/${joins.length} · ${joins[hopStep-1]?.role || "direct"}`;
  for(const id of ["path-prev","path-next","path-play"])$(id).disabled=!joins.length || graphMode!=="query";
  $("join-details").replaceChildren();
  for(const [index,join] of joins.entries()) {
    const card=node("div",undefined,"join-detail"+(hopStep===index+1?" step-active":""));
    card.append(node("strong",`${index+1} / ${join.role}`),node("span",`${join.source.alias}.${join.source.column} = ${join.target.alias}.${join.target.column}`));$("join-details").append(card);
  }
  if(!joins.length)$("join-details").append(node("p","Direct column: no relationship join is needed.","small muted"));
  const shown=new Set(hopStep<0?path.join_ids:path.join_ids.slice(0,hopStep));
  for(const segment of sqlSegments){segment.element.classList.toggle("path-highlight",shown.has(segment.id));segment.element.classList.toggle("step-highlight",hopStep>0&&path.join_ids[hopStep-1]===segment.id);}
  drawGraph();
}
function selectPath(index) {
  if(!activeTrace?.paths[index])return;
  stopPlayback();hopStep=-1;drawPath(activeTrace,activeTrace.paths[index],pathButtons,index);
}
function stepPath(direction) {
  if(!activePath || graphMode!=="query")return;
  stopPlayback();const count=activePath.join_ids.length;
  hopStep=hopStep<0?(direction>0?0:Math.max(0,count-1)):Math.max(0,Math.min(count,hopStep+direction));
  drawPath(activeTrace,activePath,pathButtons,activePathIndex,true);
  const model=traceModel(activeTrace), alias=hopStep?activeTrace.joins[activePath.join_ids[hopStep-1]]?.target.alias:activePath.root;
  const item=model.nodes.find(item=>item.alias===alias);
  if(item&&graphView&&(item.x<graphView.x || item.x+194>graphView.x+graphView.width)) {graphView.x=Math.max(0,Math.min(model.width-graphView.width,item.x+97-graphView.width/2));setGraphView();}
}
function playPath() {
  if(playback!==null){stopPlayback();drawGraph();return;}
  if(!activePath?.join_ids.length || typeof setTimeout!=="function")return;
  hopStep=0;
  function tick() {
    $("path-play").textContent="Ⅱ Pause";$("path-play").classList.toggle("is-playing",true);
    playback=setTimeout(()=>{
      if(hopStep>=activePath.join_ids.length){stopPlayback();drawGraph();return;}
      hopStep++;tick();
    },950);
    drawPath(activeTrace,activePath,pathButtons,activePathIndex,true);
    const model=traceModel(activeTrace), alias=hopStep?activeTrace.joins[activePath.join_ids[hopStep-1]]?.target.alias:activePath.root;
    const item=model.nodes.find(item=>item.alias===alias);
    if(item&&graphView){graphView.x=Math.max(0,Math.min(Math.max(0,model.width-graphView.width),item.x+97-graphView.width/2));setGraphView();}
  }
  tick();
}
function renderPaths(trace,request) {
  stopPlayback();++previewNumber;$("node-inspector").hidden=true;
  activeTrace=trace || null;activePath=null;hopStep=-1;graphView=null;graphMode="query";
  graphContext={scenario:request?.scenario,snapshot:request?.snapshot,catalog:selected?.catalog};
  $("graph-query").classList.toggle("active",true);$("graph-query").setAttribute("aria-pressed","true");
  $("graph-schema").classList.toggle("active",false);$("graph-schema").setAttribute("aria-pressed","false");
  $("paths").replaceChildren();$("join-details").replaceChildren();
  if(!trace?.paths?.length){
    $("current-path").textContent=language === "sql" ? "SQL executes directly" : "No compiled path available";$("path-length").textContent="";
    for(const id of ["path-prev","path-next","path-play"])$(id).disabled=true;
    drawGraph();return;
  }
  pathButtons=trace.paths.map((path,i)=>{
    const button=node("button",[path.root,...path.features].join("."));button.title=path.clause.toUpperCase()+" occurrence "+(path.index+1);
    button.addEventListener("click",()=>selectPath(i));$("paths").append(button);return button;
  });
  let longest=0;for(let i=1;i<trace.paths.length;i++)if(trace.paths[i].join_ids.length>trace.paths[longest].join_ids.length)longest=i;
  drawPath(trace,trace.paths[longest],pathButtons,longest);
}

function renderResult(result,request) {
  const run = result.run, baseline = result.baseline;
  renderSQL(run.sql,run.trace);
  $("stat-paths").textContent=run.trace ? run.trace.paths.length : "—";
  $("stat-joins").textContent=run.trace ? run.trace.joins.length : "—";
  $("stat-rows").textContent=run.rows ? run.rows.length : "—";
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
  renderPaths(run.trace,request);
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
    renderResult(result,request);
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

const languageLabel = language => language === "natsql" ? "NatSQL" : language.toUpperCase();
function measuredLanguages(group) { return ["sql","sqlp",...(group.natsql ? ["natsql"] : [])]; }
function drawTokenChart(group) {
  const width=760,origin=82,barWidth=570;
  const languages=measuredLanguages(group).filter(language=>measurement(group[language]).usageKnown);
  const largest=Math.max(0,...languages.map(language=>group[language].total_tokens));
  const height=languages.length*48+29;
  const svg=svgNode("svg",{viewBox:`0 0 ${width} ${height}`,role:"img",class:"token-chart",
    "aria-label":"Recorded total tokens: "+languages.map(language=>`${languageLabel(language)} ${group[language].total_tokens}`).join(", ")+". Input and output components; output includes reported reasoning."});
  for(const [index,language]of languages.entries()) {
    const condition=group[language],y=21+index*48;
    const inputWidth=largest?condition.input_tokens/largest*barWidth:0,outputWidth=largest?condition.output_tokens/largest*barWidth:0;
    svg.append(svgNode("text",{x:19,y:y+17,class:"chart-language"},languageLabel(language)));
    svg.append(svgNode("rect",{x:origin,y,width:barWidth,height:26,rx:3,class:"bar-track"}));
    svg.append(svgNode("rect",{x:origin,y,width:inputWidth,height:26,class:"bar-input"+(language==="natsql"?" natsql-input":"")}));
    svg.append(svgNode("rect",{x:origin+inputWidth,y,width:outputWidth,height:26,class:"bar-output"+(language==="natsql"?" natsql-output":"")}));
    svg.append(svgNode("text",{x:741,y:y+17,"text-anchor":"end",class:"chart-total"},condition.total_tokens.toLocaleString()));
  }
  return svg;
}

function renderCohortChart(groups) {
  const target=$("cohort-chart");target.replaceChildren();target.hidden=true;
  const buckets=new Map();
  for(const group of groups) {
    const a=measurement(group.sql),b=measurement(group.sqlp);
    if(!/JSON/i.test(String(group.protocol)))continue;
    if(!a.usageKnown||!b.usageKnown||!a.countsKnown||!b.countsKnown||group.sql.attempts!==group.sqlp.attempts||!group.sql.total_tokens||!count(group.batch_size)||group.batch_size<1)continue;
    const signature=JSON.stringify([group.model,group.dataset,group.protocol,group.guide_revision,group.question_count,group.repetitions]);
    if(!buckets.has(signature))buckets.set(signature,[]);
    buckets.get(signature).push(group);
  }
  const series=[...buckets.values()].map(items=>items.sort((a,b)=>a.batch_size-b.batch_size)).filter(items=>new Set(items.map(group=>group.batch_size)).size>=2);
  if(!series.length)return;
  target.hidden=false;target.append(node("h3","Sharing JSON-batch context changes the tradeoff."),node("p","Same-protocol recorded JSON batches. Click a measured point to inspect its cohort. Lines connect tested settings; intermediate batch sizes are not estimates."));
  const all=series.flat(),sizes=[...new Set(all.map(group=>group.batch_size))].sort((a,b)=>a-b);
  const change=group=>100*(group.sqlp.total_tokens-group.sql.total_tokens)/group.sql.total_tokens;
  const low=Math.floor(Math.min(0,...all.map(change))/5)*5,high=Math.max(5,Math.ceil(Math.max(0,...all.map(change))/5)*5);
  const width=900,height=250,left=58,right=55,top=26,bottom=45;
  const x=size=>left+(size-sizes[0])/Math.max(1,sizes[sizes.length-1]-sizes[0])*(width-left-right);
  const y=value=>top+(high-value)/(high-low)*(height-top-bottom);
  const svg=svgNode("svg",{viewBox:`0 0 ${width} ${height}`,role:"img","aria-label":"Recorded SQLP total-token percentage change relative to SQL across tested batch sizes. Negative means fewer SQLP tokens."});
  for(let tick=low;tick<=high;tick+=5){
    svg.append(svgNode("line",{x1:left,y1:y(tick),x2:width-right,y2:y(tick),stroke:tick===0?"#91a2c3":"#e6ebf6",class:tick===0?"cohort-zero":""}));
    svg.append(svgNode("text",{x:left-13,y:y(tick)+4,"text-anchor":"end",class:"cohort-axis"},(tick>0?"+":"")+tick+"%"));
  }
  for(const size of sizes)svg.append(svgNode("text",{x:x(size),y:height-19,"text-anchor":"middle",class:"cohort-axis"},"Batch "+size));
  const legend=node("div",undefined,"cohort-legend");
  for(const [index,items]of series.entries()) {
    const color=palette[index%palette.length];
    svg.append(svgNode("path",{d:items.map((group,i)=>(i?"L":"M")+x(group.batch_size)+" "+y(change(group))).join(" "),stroke:color,class:"cohort-line"}));
    const name=items[0].model||"Unspecified model";
    for(const group of items){
      const delta=change(group),point=svgNode("circle",{cx:x(group.batch_size),cy:y(delta),r:6,fill:color,class:"cohort-point",tabindex:0,role:"button","data-group-id":group.id,"aria-label":`${name}, batch ${group.batch_size}, SQLP ${delta>0?"+":""}${delta.toFixed(2)}% total tokens. Inspect recorded cohort.`});
      point.append(svgNode("title",{},`${name} · batch ${group.batch_size} · ${delta>0?"+":""}${delta.toFixed(2)}%`));
      const choose=()=>{$("experiment-select").value=group.id;renderMeasurementGroup(group);};
      point.addEventListener("click",choose);point.addEventListener("keydown",event=>{if(event.key==="Enter"||event.key===" "){event.preventDefault();choose();}});svg.append(point);
      if(group.batch_size===sizes[sizes.length-1])svg.append(svgNode("text",{x:x(group.batch_size)+12,y:y(delta)+4,fill:color,"font-size":10,"font-family":"system-ui"},delta.toFixed(1)+"%"));
    }
    const label=node("span"),dot=svgNode("svg",{viewBox:"0 0 10 10","aria-hidden":"true"});dot.append(svgNode("circle",{cx:5,cy:5,r:4,fill:color}));label.append(dot,node("span",name));legend.append(label);
  }
  target.append(svg,legend,node("p","Below zero: fewer total SQLP tokens. Above zero: more. Every point includes input and output, including reported reasoning."));
}

function renderMeasurementGroup(group) {
  const container = $("experiments"); container.replaceChildren();
  const context = node("div",undefined,"measurement-context");
  context.append(node("span",group.model || "Model unspecified"),node("span",group.dataset || "Dataset unspecified"),
    node("span",group.protocol || "Protocol unspecified"),node("span",`Batch size ${numberText(group.batch_size)}`));
  if(typeof group.guide_revision === "string")context.append(node("span","Guide: "+group.guide_revision));
  container.append(context);
  const validQuestions = count(group.question_count) && group.question_count > 0;
  const validRepetitions = count(group.repetitions) && group.repetitions > 0;
  container.append(node("p", validQuestions && validRepetitions ?
    `${group.question_count.toLocaleString()} unique questions · ${group.repetitions.toLocaleString()} repetition${group.repetitions === 1 ? "" : "s"}. Repeated questions are correlated observations, not additional independent tasks.` :
    "Question/repetition counts are unrecorded. Treat these as descriptive measurements.","small muted"));
  const languages=measuredLanguages(group);
  const checks=Object.fromEntries(languages.map(language=>[language,measurement(group[language])]));
  const cards = node("div",undefined,"measurement-cards"+(group.natsql?" has-natsql":""));
  for (const language of languages) {
    const data = group[language] || {}, checked = checks[language], card = node("div",undefined,"measurement-card " + language);
    const top = node("div",undefined,"measurement-heading");
    top.append(node("h3",languageLabel(language)),node("span",checked.countsKnown ?
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
    renderCohortChart(experiments.groups);
  } else if (Array.isArray(experiments.columns) && Array.isArray(experiments.rows)) {
    const table = node("table"), heading = node("tr"), body = node("tbody");
    for (const column of experiments.columns) heading.append(node("th",column));
    const head = node("thead"); head.append(heading); table.append(head);
    for (const values of experiments.rows) { const row = node("tr"); for (const value of values) row.append(node("td",value)); body.append(row); }
    table.append(body); $("experiments").append(table);
  } else $("experiments").append(node("pre",JSON.stringify(experiments,null,2)));
}

for (const mode of ["query","schema"]) $("graph-"+mode).addEventListener("click",()=>{
  stopPlayback();graphMode=mode;graphView=null;
  for(const candidate of ["query","schema"]){$("graph-"+candidate).classList.toggle("active",candidate===mode);$("graph-"+candidate).setAttribute("aria-pressed",String(candidate===mode));}
  if(activePath)drawPath(activeTrace,activePath,pathButtons,activePathIndex);else drawGraph();
});
$("path-prev").addEventListener("click",()=>stepPath(-1));
$("path-next").addEventListener("click",()=>stepPath(1));
$("path-play").addEventListener("click",playPath);
$("graph-fit").addEventListener("click",fitGraph);
$("graph-zoom-in").addEventListener("click",()=>zoomGraph(.8));
$("graph-zoom-out").addEventListener("click",()=>zoomGraph(1.25));
$("close-inspector").addEventListener("click",()=>{++previewNumber;$("node-inspector").hidden=true;});
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
  for (const scenario of catalog.scenarios) { const title=scenario.id === "canadian_authors_danish_publishers" ? "Author affiliation vs publisher" : scenario.id.replace(/[_-]+/g," ").replace(/^./,letter=>letter.toUpperCase()); const option = node("option",title); option.value = scenario.id; $("scenario").append(option); }
  for (const id of ["scenario","snapshot","run","reset"]) $(id).disabled = false;
  const showcase=catalog.scenarios.find(s=>s.id === "canadian_authors_danish_publishers");
  if(showcase)$("scenario").value=showcase.id;
  renderExperiments(catalog.experiments); selectScenario();
}).catch(error => showError(error.message));
