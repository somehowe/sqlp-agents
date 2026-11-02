/* Local saved-record viewer. No model calls, network requests, or persistence. */
(function () {
  "use strict";
  const MAX_BYTES = 16 * 1024 * 1024;
  const ORIGINS = {
    manual: ["Prepared queries", "Hand-written development queries. These records do not measure model performance."],
    replay: ["Replayed responses", "Recorded or synthetic responses were replayed. This is not a new model run; inspect the original provenance before using it as evidence."],
    dry_run: ["Dry-run plan", "Requests were planned without calling a model or executing generated answers."],
    live: ["Live-run record", "This file is labeled as a live model run. Its labels and provenance are supplied by the file; this viewer does not authenticate them."]
  };
  const object = value => value !== null && typeof value === "object" && !Array.isArray(value);
  const fail = message => { throw new Error(message); };
  function text(value, name, empty = true) {
    if (typeof value !== "string" || value.length > 1000000 || (!empty && !value.length)) fail("Invalid " + name + ".");
    for (let index = 0; index < value.length; index++) {
      const code = value.charCodeAt(index);
      if (code >= 0xd800 && code <= 0xdbff) {
        const next = value.charCodeAt(++index);
        if (!(next >= 0xdc00 && next <= 0xdfff)) fail("Invalid Unicode in " + name + ".");
      } else if (code >= 0xdc00 && code <= 0xdfff) fail("Invalid Unicode in " + name + ".");
    }
    return value;
  }
  function array(value, name, maximum) {
    if (!Array.isArray(value) || value.length > maximum) fail("Invalid or oversized " + name + ".");
    return value;
  }
  function integer(value, name, maximum) {
    if (!Number.isSafeInteger(value) || value < 0 || value > maximum) fail("Invalid " + name + ".");
    return value;
  }
  function cell(value) {
    if (!object(value) || !["null", "int64", "real", "string", "blob"].includes(value.type)) fail("Invalid typed answer cell.");
    if (value.type === "null") {
      if (value.value !== null) fail("NULL cells must contain null.");
    } else {
      text(value.value, "answer cell");
      if (value.type === "int64" && (!/^-?(?:0|[1-9]\d{0,18})$/.test(value.value) || BigInt(value.value) < -(2n ** 63n) || BigInt(value.value) >= 2n ** 63n)) fail("Invalid int64 cell.");
      if (value.type === "real" && !/^(?:-?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?|[+-]?inf|nan)$/i.test(value.value)) fail("Invalid real cell.");
      if (value.type === "blob" && !/^(?:[0-9a-f]{2})*$/i.test(value.value)) fail("Invalid blob cell.");
    }
  }
  function rows(value, width) {
    array(value, "answer rows", 10000).forEach(row => {
      array(row, "answer columns", 2000);
      if (row.length !== width) fail("Answer row width does not match its columns.");
      row.forEach(cell);
    });
  }
  function column(value) {
    if (!object(value)) fail("Invalid trace column.");
    ["table", "alias", "column"].forEach(key => text(value[key], "trace " + key, false));
  }
  function trace(value) {
    if (value === null || value === undefined) return;
    if (!object(value)) fail("Invalid translation trace.");
    array(value.joins, "trace joins", 63).forEach(join => {
      if (!object(join)) fail("Invalid trace join.");
      text(join.role, "reference role", false); column(join.source); column(join.target);
    });
    array(value.paths, "trace paths", 2512).forEach(path => {
      if (!object(path) || !["select", "where"].includes(path.clause)) fail("Invalid trace path.");
      integer(path.index, "path index", path.clause === "select" ? 1999 : 511); text(path.root, "path root", false); column(path.column);
      array(path.features, "path features", 64).forEach(feature => text(feature, "path component", false));
      array(path.join_ids, "path join IDs", 63);
      if (path.features.length !== path.join_ids.length + 1) fail("Trace path length does not match its joins.");
      let alias = path.root;
      path.join_ids.forEach((id, index) => {
        integer(id, "join ID", value.joins.length - 1);
        const join = value.joins[id];
        if (join.source.alias !== alias || join.role !== path.features[index]) fail("Trace joins do not follow the recorded path.");
        alias = join.target.alias;
      });
      if (path.column.alias !== alias || path.column.column !== path.features.at(-1)) fail("Trace terminal column does not match the path.");
    });
  }
  function usage(value) {
    if (value === null || value === undefined) return;
    if (!object(value)) fail("Invalid token usage.");
    ["input_tokens", "output_tokens", "total_tokens"].forEach(key => integer(value[key], "token count", Number.MAX_SAFE_INTEGER));
    if (BigInt(value.total_tokens) !== BigInt(value.input_tokens) + BigInt(value.output_tokens)) fail("Inconsistent token usage.");
  }
  function boundedJSON(value) {
    let nodes = 0, characters = 0;
    const parents = new Set();
    function visit(item, depth, label) {
      if (++nodes > 250000 || depth > 32) fail("JSON structure exceeds the viewer's size or nesting limit.");
      if (typeof item === "string") { text(item, label); characters += item.length; }
      else if (typeof item === "number") {
        if (!Number.isFinite(item) || (Number.isInteger(item) && !Number.isSafeInteger(item))) fail("Invalid number in " + label + ": use finite safe numbers or strings for int64 values.");
      } else if (item !== null && typeof item !== "boolean") {
        if (!object(item) && !Array.isArray(item)) fail("Expected JSON values.");
        if (parents.has(item)) fail("Cyclic JSON structure.");
        parents.add(item);
        if (Array.isArray(item)) {
          for (let i = 0; i < item.length; i++) visit(item[i], depth + 1, label + "[" + i + "]");
        } else Object.keys(item).forEach(key => {
          text(key, "JSON key"); characters += key.length;
          visit(item[key], depth + 1, label + "." + key.slice(0, 120).replaceAll("_", " "));
        });
        parents.delete(item);
      }
      if (characters > MAX_BYTES) fail("JSON text exceeds the viewer's size limit.");
    }
    visit(value, 0, "JSON");
  }
  function hash(value, name) {
    if (typeof value !== "string" || !/^[0-9a-f]{64}$/i.test(value)) fail("Invalid " + name + " SHA-256.");
    return value.toLowerCase();
  }
  function requestFor(report, caseId, run) {
    const entries = (report.requests || []).filter(entry => entry.case_id === caseId && entry.language === run.language);
    if (!entries.length) return {request: null, note: "No saved request payload was recorded for this question and language."};
    if (entries.length !== 1) return {request: null, note: "Ambiguous saved requests: more than one entry names this question and language. No payload is associated with this attempt."};
    const request = entries[0];
    if (!run.request_sha256) return {request: null, note: "The attempt has no request hash. Its saved request payload cannot be associated with this attempt."};
    if (request.request_sha256.toLowerCase() !== run.request_sha256.toLowerCase()) return {request: null, note: "Request hash mismatch: the attempt and planned entry name different recorded hashes. No payload is associated with this attempt."};
    return {request, note: "Recorded request hashes agree for this question and language. This checks consistency within the file; it does not recompute the payload hash or authenticate the request."};
  }
  function validateReport(report) {
    boundedJSON(report);
    if (!object(report) || report.version !== 1 || !Object.hasOwn(ORIGINS, report.origin)) fail("Expected a version-1 comparison or pilot report with a recognized origin.");
    if (typeof report.ok !== "boolean" || (report.completed !== undefined && typeof report.completed !== "boolean")) fail("Invalid report completion flags.");
    const cases = report.cases === undefined && object(report.error) ? [] : array(report.cases, "cases", 1000);
    const scopeUnknown = report.scope === undefined && report.ok === false && report.completed === false && object(report.error) && !cases.length;
    if (!scopeUnknown) text(report.scope, "report scope", false);
    if (report.demo_note !== undefined) text(report.demo_note, "demo note");
    const ids = new Set();
    cases.forEach(item => {
      if (!object(item)) fail("Invalid question record.");
      text(item.id, "question ID", false); text(item.question, "question", false);
      if (ids.has(item.id)) fail("Duplicate question ID.");
      ids.add(item.id);
      const width = integer(item.column_count, "expected column count", 2000);
      if (width === 0) fail("Expected column count must be positive.");
      if (item.expected_rows !== undefined) rows(item.expected_rows, width);
      const languages = new Set();
      array(item.runs, "condition runs", 2).forEach(run => {
        if (!object(run) || !["sql", "sqlp"].includes(run.language) || languages.has(run.language)) fail("Invalid or duplicate language condition.");
        languages.add(run.language);
        if (!["ok", "error"].includes(run.status) || ![true, false, null, undefined].includes(run.correct)) fail("Invalid run status or correctness label.");
        if (run.status === "error" && run.correct === true) fail("A failed run cannot be labeled correct.");
        text(run.query, "query");
        if (run.sql !== null && run.sql !== undefined) text(run.sql, "executed SQL");
        if (run.columns !== null && run.columns !== undefined) array(run.columns, "output columns", 2000).forEach(name => text(name, "output column"));
        if (run.rows !== null && run.rows !== undefined) {
          if (!Array.isArray(run.columns)) fail("Answer rows require column names.");
          rows(run.rows, run.columns.length);
        }
        if (run.status === "ok" && (!Array.isArray(run.columns) || !Array.isArray(run.rows))) fail("Successful runs require recorded columns and rows.");
        if (run.status === "error" && run.rows !== null && run.rows !== undefined) fail("Failed runs must not contain a scored answer.");
        array(run.parameters || [], "parameters", 512).forEach(parameter => {
          if (!object(parameter) || !["int64", "string"].includes(parameter.type)) fail("Invalid parameter.");
          cell(parameter);
        });
        usage(run.token_usage); trace(run.trace);
        if (run.request_sha256 !== undefined) hash(run.request_sha256, "request");
        if (run.generation !== undefined && !object(run.generation)) fail("Invalid generation record.");
      });
    });
    if (report.requests !== undefined) array(report.requests, "planned requests", 2000).forEach(request => {
      if (!object(request) || !["sql", "sqlp"].includes(request.language) || !object(request.payload)) fail("Invalid saved request.");
      text(request.case_id, "request question ID", false); hash(request.request_sha256, "saved request");
    });
    if (report.manifest !== undefined) {
      if (!object(report.manifest)) fail("Invalid report manifest.");
      ["catalog_sha256", "data_snapshot_sha256"].forEach(key => {
        if (report.manifest[key] !== undefined) hash(report.manifest[key], key);
      });
    }
    return report;
  }
  function parseReport(source) {
    if (typeof source !== "string" || new TextEncoder().encode(source).length > MAX_BYTES) fail("Choose a JSON report no larger than 16 MiB.");
    let report;
    try { report = JSON.parse(source); } catch (_) { fail("The selected file is not valid JSON."); }
    return validateReport(report);
  }
  function catalogFacts(catalog) {
    if (!object(catalog) || catalog.version !== 1) fail("Invalid mapping/request catalog.");
    const tables = array(catalog.tables, "catalog tables", 128).map(table => {
      if (!object(table)) fail("Invalid catalog table.");
      text(table.name, "catalog table name", false);
      const columns = array(table.columns, "catalog columns", 2000).map(col => {
        if (!object(col) || !["integer", "string"].includes(col.type) || typeof col.unique !== "boolean") fail("Invalid catalog column.");
        text(col.name, "catalog column name", false);
        return {name: col.name, type: col.type, unique: col.unique};
      });
      if (!columns.length || new Set(columns.map(col => col.name)).size !== columns.length) fail("Empty or duplicate catalog columns.");
      return {name: table.name, columns: columns.sort((a, b) => a.name < b.name ? -1 : a.name > b.name ? 1 : 0)};
    });
    if (!tables.length || new Set(tables.map(table => table.name)).size !== tables.length) fail("Empty or duplicate catalog tables.");
    const references = array(catalog.references, "catalog references", 4096).map(ref => {
      if (!object(ref)) fail("Invalid catalog reference.");
      const result = {};
      ["source_table", "feature", "source_column", "target_table", "target_column"].forEach(key => { text(ref[key], "catalog reference " + key, false); result[key] = ref[key]; });
      return result;
    });
    return {version: 1, tables: tables.sort((a, b) => a.name < b.name ? -1 : a.name > b.name ? 1 : 0),
      references: references.sort((a, b) => JSON.stringify(a) < JSON.stringify(b) ? -1 : JSON.stringify(a) > JSON.stringify(b) ? 1 : 0)};
  }
  function validateMapping(mapping) {
    boundedJSON(mapping);
    if (!object(mapping) || mapping.version !== 1 || mapping.ok !== true || mapping.origin !== "restricted_arm_mapping" ||
        typeof mapping.data_checked !== "boolean" || mapping.identity_semantics !== "single_primary_key_within_source_table" ||
        mapping.entity_results_supported !== false || mapping.entity_equality_supported !== false) fail("Expected a successful version-1 restricted ARM mapping artifact.");
    const entities = array(mapping.entities, "mapping entities", 128);
    if (!entities.length) fail("Mapping must contain entities.");
    const byName = new Map();
    entities.forEach(entity => {
      if (!object(entity)) fail("Invalid mapping entity.");
      text(entity.name, "logical entity name", false); text(entity.source_table, "physical table name", false);
      if (byName.has(entity.name)) fail("Duplicate logical entity.");
      byName.set(entity.name, entity);
      const columns = array(entity.columns, "mapping columns", 2000);
      columns.forEach(col => {
        if (!object(col) || !["integer", "string"].includes(col.type) || typeof col.key !== "boolean") fail("Invalid mapped column.");
        text(col.name, "logical column", false); text(col.source_column, "physical column", false);
      });
      if (!columns.length || new Set(columns.map(col => col.name)).size !== columns.length) fail("Empty or duplicate mapped columns.");
      const keys = columns.filter(col => col.key), id = entity.identification;
      if (keys.length !== 1 || !object(id) || id.kind !== "single_primary_key" || id.column !== keys[0].name || id.source_column !== keys[0].source_column || id.type !== keys[0].type) fail("Mapping identification must match its one declared key.");
    });
    const logicalRefs = [];
    entities.forEach(entity => {
      const roles = new Set();
      array(entity.references, "mapped references", 4096).forEach(ref => {
        if (!object(ref) || !object(ref.logical) || !object(ref.physical)) fail("Invalid mapped reference.");
        text(ref.feature, "mapped role", false);
        if (roles.has(ref.feature)) fail("Duplicate mapped role.");
        roles.add(ref.feature);
        const logical = ref.logical, physical = ref.physical;
        [logical, physical].forEach(value => ["source_table", "feature", "source_column", "target_table", "target_column"].forEach(key => text(value[key], "mapped reference " + key, false)));
        const target = byName.get(logical.target_table);
        const sourceCol = entity.columns.find(col => col.name === logical.source_column);
        const targetCol = target && target.columns.find(col => col.name === logical.target_column);
        if (!target || !sourceCol || !targetCol || !targetCol.key || sourceCol.type !== targetCol.type ||
            logical.source_table !== entity.name || physical.source_table !== entity.source_table ||
            physical.target_table !== target.source_table || physical.source_column !== sourceCol.source_column ||
            physical.target_column !== targetCol.source_column || logical.feature !== ref.feature || physical.feature !== ref.feature) fail("Mapped reference disagrees with its logical/physical columns.");
        integer(ref.declared_foreign_key_id, "declared foreign-key ID", Number.MAX_SAFE_INTEGER);
        logicalRefs.push(logical);
      });
    });
    const views = array(mapping.views, "generated views", 128);
    const viewNames = new Set();
    views.forEach(view => {
      if (!object(view) || !byName.has(view.name) || viewNames.has(view.name)) fail("Invalid or duplicate generated view.");
      viewNames.add(view.name); text(view.sql, "generated view SQL", false);
    });
    if (views.length !== entities.length) fail("Each mapping entity requires one recorded view definition.");
    const derived = {version: 1, tables: entities.map(entity => ({name: entity.name,
      columns: entity.columns.map(col => ({name: col.name, type: col.type, unique: col.key}))})), references: logicalRefs};
    if (JSON.stringify(catalogFacts(derived)) !== JSON.stringify(catalogFacts(mapping.catalog))) fail("Mapping catalog disagrees with its entities and references.");
    if (!object(mapping.hashes)) fail("Mapping hashes are missing.");
    Object.keys(mapping.hashes).forEach(key => hash(mapping.hashes[key], "mapping " + key));
    ["source_snapshot_sha256", "copied_snapshot_sha256", "snapshot_sha256"].forEach(key => {
      if (mapping[key] !== undefined) hash(mapping[key], key);
    });
    return mapping;
  }
  function parseMapping(source) {
    if (typeof source !== "string" || new TextEncoder().encode(source).length > MAX_BYTES) fail("Choose a mapping JSON file no larger than 16 MiB.");
    let mapping;
    try { mapping = JSON.parse(source); } catch (_) { fail("The selected mapping file is not valid JSON."); }
    return validateMapping(mapping);
  }
  function mappingAssociation(report, mapping) {
    if (!report) return {state: "unverified", note: "Mapping association not verified: no comparison record is loaded."};
    const manifest = object(report.manifest) ? report.manifest : {};
    const snapshotKnown = typeof manifest.data_snapshot_sha256 === "string" && typeof mapping.snapshot_sha256 === "string";
    const snapshotMatches = snapshotKnown && manifest.data_snapshot_sha256.toLowerCase() === mapping.snapshot_sha256.toLowerCase();
    const expected = JSON.stringify(catalogFacts(mapping.catalog));
    let runs = 0, catalogs = 0, catalogMismatch = false;
    (report.cases || []).forEach(item => item.runs.forEach(run => {
      runs++;
      const associated = requestFor(report, item.id, run).request;
      if (!associated || !Array.isArray(associated.payload.input)) return;
      const found = [];
      associated.payload.input.forEach(message => {
        if (!object(message) || typeof message.content !== "string") return;
        try {
          const task = JSON.parse(message.content);
          boundedJSON(task);
          if (object(task) && object(task.schema_and_relationships)) found.push(JSON.stringify(catalogFacts(task.schema_and_relationships)));
        } catch (_) { /* Unrecognized input is not verified catalog evidence. */ }
      });
      if (found.length) { catalogs++; if (found.some(value => value !== expected)) catalogMismatch = true; }
    }));
    const catalogMatches = runs > 0 && catalogs === runs && !catalogMismatch;
    const details = [catalogMismatch ? "Associated request catalog facts differ." : catalogMatches ? "Associated request catalog facts match structurally." : "Catalog association is not verified; complete associated request catalogs are unavailable.",
      snapshotKnown ? snapshotMatches ? "Recorded mapped-snapshot hashes match." : "Recorded mapped-snapshot hashes differ." : "Snapshot association is not verified; a comparable hash is missing.",
      "Catalog hash strings are displayed only: mapping and pilot JSON encodings can differ for Unicode. These checks show record consistency, not authentication."];
    if (catalogMismatch || (snapshotKnown && !snapshotMatches)) return {state: "mismatch", note: "Mapping association mismatch. " + details.join(" ")};
    if (catalogMatches && snapshotMatches) return {state: "consistent", note: "Recorded mapping association is consistent. " + details.join(" ")};
    return {state: "unverified", note: "Mapping association not verified. " + details.join(" ")};
  }
  function identifier(value) {
    return /^[A-Za-z_][A-Za-z_0-9]*$/.test(value) && !/^(select|from|where|as|distinct|and)$/i.test(value) ? value : '"' + value.replaceAll('"', '""') + '"';
  }
  function formatPath(path) { return [path.root, ...path.features].map(identifier).join("."); }
  function formatCell(value) { return value.type === "null" ? "NULL" : value.type === "blob" ? "x'" + value.value + "'" : value.value; }
  function count(value) { return value === null || value === undefined ? "Unknown" : value.toLocaleString("en-US"); }
  function sumUsage(runs) {
    if (!runs.length || runs.some(run => !run.token_usage)) return null;
    return ["input_tokens", "output_tokens", "total_tokens"].reduce((sum, key) => {
      sum[key] = runs.reduce((total, run) => total + BigInt(run.token_usage[key]), 0n); return sum;
    }, {});
  }
  function diagnostic(error) {
    if (!object(error)) return "No diagnostic text was recorded.";
    const detail = object(error.diagnostic) ? error.diagnostic : error;
    const parts = [];
    if (typeof error.stage === "string") parts.push(error.stage);
    if (detail !== error && typeof detail.stage === "string") parts.push(detail.stage);
    if (typeof detail.code === "string") parts.push(detail.code);
    return (parts.length ? "[" + parts.join(" / ") + "] " : "") + (typeof detail.message === "string" ? detail.message : "No diagnostic text was recorded.");
  }
  function createViewer(doc) {
    const byId = id => doc.getElementById(id);
    const el = (tag, className, content) => {
      const node = doc.createElement(tag);
      if (className) node.className = className;
      if (content !== undefined) node.textContent = String(content);
      return node;
    };
    const add = (parent, ...children) => { children.forEach(child => parent.appendChild(child)); return parent; };
    let currentReport = null, currentMapping = null;
    byId("mapping-panel").hidden = true;
    function table(columns, values) {
      if (!values.length) return el("p", "empty-answer", "0 rows · Empty answer");
      const result = el("table");
      const head = el("tr");
      columns.slice(0, 30).forEach(name => { const th = el("th", "", name); th.scope = "col"; head.appendChild(th); });
      add(result, add(el("thead"), head));
      const body = el("tbody");
      values.slice(0, 100).forEach(row => {
        const tr = el("tr");
        row.slice(0, 30).forEach(value => {
          const td = el("td", value.type === "null" ? "cell-null" : "", formatCell(value));
          td.title = value.type; tr.appendChild(td);
        });
        body.appendChild(tr);
      });
      result.appendChild(body);
      const container = add(el("div"), add(el("div", "answer-scroll"), result));
      if (values.length > 100 || columns.length > 30) container.appendChild(el("p", "small muted", "Preview: first " + Math.min(values.length, 100) + " of " + values.length + " rows and " + Math.min(columns.length, 30) + " of " + columns.length + " columns. The runner scored the full recorded answer."));
      return container;
    }
    function inspector(value) {
      const panel = el("section", "path-inspector");
      const heading = el("h4", "section-label", "Follow a path");
      const select = el("select", "path-select");
      select.setAttribute("aria-label", "SQLP path to inspect");
      value.paths.forEach((path, index) => {
        const option = el("option", "", path.clause.toUpperCase() + " " + (path.index + 1) + " · " + formatPath(path));
        option.value = String(index); select.appendChild(option);
      });
      const output = el("div", "path-output");
      const update = () => {
        output.replaceChildren();
        const path = value.paths[Number(select.value) || 0];
        if (!path.join_ids.length) output.appendChild(el("p", "small muted", "Direct column on the root binding; no relationship joins."));
        path.join_ids.forEach((id, index) => {
          const join = value.joins[id];
          const step = el("div", "join-step");
          add(step, el("span", "join-role", (index + 1) + " / " + join.role),
            el("p", "join-equation", identifier(join.source.alias) + "." + identifier(join.source.column) + " = " + identifier(join.target.alias) + "." + identifier(join.target.column)),
            el("span", "join-tables", join.source.table + " (" + join.source.alias + ") → " + join.target.table + " (" + join.target.alias + ")"));
          output.appendChild(step);
        });
        output.appendChild(el("div", "terminal", "Read " + identifier(path.column.alias) + "." + identifier(path.column.column) + " from " + identifier(path.column.table)));
      };
      select.addEventListener("change", update);
      add(panel, heading, select, output); update();
      return panel;
    }
    function generationInspector(report, caseId, run) {
      const details = el("details", "generation-inspector");
      details.appendChild(el("summary", "", "Inspect saved request and raw provider response"));
      const association = requestFor(report, caseId, run);
      details.appendChild(el("p", "small request-association", association.note));
      if (run.request_sha256) details.appendChild(el("p", "small hash-text", "Attempt request SHA-256: " + run.request_sha256));
      const candidates = (report.requests || []).filter(entry => entry.case_id === caseId && entry.language === run.language);
      if (!association.request && candidates.length === 1) details.appendChild(el("p", "small hash-text", "Planned request SHA-256: " + candidates[0].request_sha256));
      if (association.request) {
        const request = add(el("details", "request-payload"), el("summary", "", "Saved request payload"),
          el("p", "small muted", "Parsed saved JSON, including instructions and input. Original HTTP wire bytes are not recorded here."),
          el("pre", "code-block json-code", JSON.stringify(association.request.payload, null, 2)));
        details.appendChild(request);
      }
      const generation = object(run.generation) ? run.generation : {};
      if (Object.hasOwn(generation, "raw_response")) {
        details.appendChild(add(el("details", "raw-response"), el("summary", "", "Raw provider response (saved JSON)"),
          el("pre", "code-block json-code", JSON.stringify(generation.raw_response, null, 2))));
        if (object(generation.raw_response) && Object.hasOwn(generation.raw_response, "usage")) {
          add(details, el("p", "section-label", "Raw provider usage"),
            el("pre", "code-block raw-usage", JSON.stringify(generation.raw_response.usage, null, 2)),
            el("p", "small muted", "Raw usage is shown for inspection. Cached/reasoning counters can overlap totals; they are not added to them."));
        } else details.appendChild(el("p", "small muted", "No raw provider usage field was recorded in the saved response."));
      } else {
        details.appendChild(el("p", "small muted", "No raw provider response or raw usage was recorded for this condition."));
      }
      return details;
    }
    function runCard(language, run, report, caseId) {
      const card = el("article", "run-card " + language);
      const heading = el("div", "run-heading");
      let label = "No attempt", state = "";
      if (run) {
        if (run.status === "error") { label = "Failed attempt"; state = "failed"; }
        else if (run.correct === true) { label = "Answer matches"; state = "correct"; }
        else if (run.correct === false) { label = "Answer differs"; state = "incorrect"; }
        else label = "Unscored answer";
      }
      add(heading, el("h3", "", language.toUpperCase()), el("span", "result-label " + state, label));
      const body = el("div", "run-body"); add(card, heading, body);
      if (!run) { body.appendChild(el("p", "muted small", "This file has no recorded attempt for this language.")); return card; }
      add(body, el("p", "section-label", "Generated query"), el("pre", "code-block", run.query || "(empty output)"));
      const tokenStrip = el("div", "token-strip");
      [["Input tokens", "input_tokens"], ["Output tokens", "output_tokens"], ["Total tokens", "total_tokens"]].forEach(([name, key]) => {
        add(tokenStrip, add(el("div"), el("span", "token-label", name), el("span", "token-value", count(run.token_usage ? run.token_usage[key] : null))));
      });
      body.appendChild(tokenStrip);
      if (!run.token_usage) body.appendChild(el("p", "small muted", "Provider usage was not recorded. Unknown is not zero."));
      if (run.generation && typeof run.generation.model === "string") body.appendChild(el("p", "small muted", "Returned model: " + run.generation.model));
      if (run.status === "error") body.appendChild(el("p", "notice error-notice", diagnostic(run.error)));
      add(body, el("p", "section-label", "Recorded answer"));
      if (run.rows === null || run.rows === undefined) body.appendChild(el("p", "empty-answer", "No answer recorded."));
      else {
        body.appendChild(el("p", "small muted", run.rows.length + " rows · " + run.columns.length + " columns"));
        body.appendChild(table(run.columns, run.rows));
      }
      if (language === "sqlp" && typeof run.sql === "string") {
        const details = el("details");
        add(details, el("summary", "", "Inspect compiled SQL and parameters"), el("pre", "code-block", run.sql));
        const parameters = el("ul", "parameters");
        (run.parameters || []).forEach((parameter, index) => parameters.appendChild(el("li", "", "?" + (index + 1) + " · " + parameter.type + " · " + parameter.value)));
        details.appendChild(parameters); body.appendChild(details);
      }
      if (language === "sqlp" && run.trace && run.trace.paths.length) body.appendChild(inspector(run.trace));
      body.appendChild(generationInspector(report, caseId, run));
      return card;
    }
    function showCase(report, index) {
      const item = report.cases[index];
      byId("question-text").textContent = item.question;
      byId("question-meta").textContent = item.id + " · Expected width: " + item.column_count + " column" + (item.column_count === 1 ? "" : "s");
      const comparison = byId("comparison"); comparison.replaceChildren();
      ["sql", "sqlp"].forEach(language => comparison.appendChild(runCard(language, item.runs.find(run => run.language === language), report, item.id)));
      const expected = byId("expected-answer"); expected.replaceChildren();
      byId("expected-panel").hidden = item.expected_rows === undefined;
      if (item.expected_rows !== undefined) expected.appendChild(table(Array.from({length: item.column_count}, (_, col) => "Column " + (col + 1)), item.expected_rows));
    }
    function metric(label, value, note) {
      return add(el("div", "metric"), el("span", "metric-label", label), el("span", "metric-value", value), el("span", "metric-note", note));
    }
    function updateMappingAssociation() {
      if (!currentMapping) return;
      const association = mappingAssociation(currentReport, currentMapping);
      const node = byId("mapping-association");
      node.textContent = association.note;
      node.className = "notice mapping-association " + association.state + (association.state === "mismatch" ? " error-notice" : "");
    }
    function loadMapping(mapping, filename = "Mapping artifact") {
      validateMapping(mapping);
      currentMapping = mapping;
      byId("mapping-error").hidden = true;
      byId("mapping-panel").hidden = false;
      byId("mapping-name").textContent = filename;
      const container = byId("mapping-entities"); container.replaceChildren();
      mapping.entities.forEach(entity => {
        const panel = el("article", "mapping-entity");
        const id = entity.identification;
        add(panel, el("h3", "", entity.name + " → " + entity.source_table),
          el("p", "small", "Declared source key: " + identifier(entity.source_table) + "." + identifier(id.source_column) + " → " + identifier(entity.name) + "." + identifier(id.column)),
          table(["Logical column", "Physical column", "Type", "Key"], entity.columns.map(col => [col.name, col.source_column, col.type, col.key ? "primary key" : ""].map(value => ({type: "string", value})))));
        panel.appendChild(el("h4", "section-label", "Approved reference mappings"));
        if (!entity.references.length) panel.appendChild(el("p", "small muted", "No references declared for this entity."));
        entity.references.forEach(ref => {
          const logical = ref.logical, physical = ref.physical;
          add(panel, add(el("div", "join-step"), el("span", "join-role", entity.name + "." + ref.feature + " → " + logical.target_table),
            el("p", "join-equation", identifier(logical.source_table) + "." + identifier(logical.source_column) + " = " + identifier(logical.target_table) + "." + identifier(logical.target_column)),
            el("p", "join-tables", "Physical: " + identifier(physical.source_table) + "." + identifier(physical.source_column) + " = " + identifier(physical.target_table) + "." + identifier(physical.target_column))));
        });
        const view = mapping.views.find(item => item.name === entity.name);
        add(panel, add(el("details", "mapping-view"), el("summary", "", "Generated SQLite view definition"),
          el("pre", "code-block", view.sql)));
        container.appendChild(panel);
      });
      const identities = byId("mapping-identities"); identities.replaceChildren();
      [["Identity profile", mapping.identity_semantics], ["File reports data checked", String(mapping.data_checked)],
        ["Source snapshot SHA-256", mapping.source_snapshot_sha256], ["Copied snapshot SHA-256", mapping.copied_snapshot_sha256],
        ["Mapped snapshot SHA-256", mapping.snapshot_sha256], ...Object.entries(mapping.hashes)].forEach(([name, value]) => {
          if (typeof value === "string") add(identities, el("dt", "", name), el("dd", "", value));
        });
      updateMappingAssociation();
      byId("mapping-status").textContent = "Loaded " + filename + ". " + mapping.entities.length + " logical entities. " + byId("mapping-association").textContent;
    }
    function loadReport(report, filename = "Comparison report") {
      validateReport(report);
      currentReport = report;
      const cases = report.cases || [];
      byId("load-error").hidden = true; byId("workspace").hidden = false; byId("empty-state").hidden = true;
      byId("origin-badge").textContent = ORIGINS[report.origin][0];
      byId("record-scope").textContent = report.scope === undefined ? "Scope unknown" : report.scope; byId("record-name").textContent = filename;
      byId("origin-note").textContent = ORIGINS[report.origin][1] + (report.demo_note ? " " + report.demo_note : "") + (report.completed === false ? " This run did not complete; do not treat it as a completed paired experiment." : "");
      const summary = byId("summary"); summary.replaceChildren();
      summary.appendChild(metric("Questions", String(cases.length), "Recorded comparison cases"));
      ["sql", "sqlp"].forEach(language => {
        const runs = cases.flatMap(item => item.runs.filter(run => run.language === language));
        const totals = sumUsage(runs);
        const scored = runs.filter(run => typeof run.correct === "boolean");
        const matches = runs.filter(run => run.correct === true).length;
        summary.appendChild(metric(language.toUpperCase() + " recorded matches", scored.length ? matches + " / " + scored.length : "Unscored", "Total tokens: " + count(totals ? totals.total_tokens : null) + (report.completed === false ? " · Partial run" : "")));
      });
      byId("record-error").hidden = !report.error;
      byId("record-error").textContent = report.error ? diagnostic(report.error) : "";
      byId("empty-notice").hidden = cases.length !== 0;
      byId("empty-notice").textContent = report.origin === "dry_run" ? "Dry-run plan: " + (report.requests || []).length + " planned requests. No generated answers were executed." : "This report contains no comparison cases.";
      const select = byId("case-select"); select.replaceChildren();
      cases.forEach((item, index) => { const option = el("option", "", (index + 1) + " · " + item.id); option.value = String(index); select.appendChild(option); });
      select.onchange = () => showCase(report, Number(select.value));
      byId("question-section").hidden = !cases.length;
      byId("comparison").replaceChildren(); byId("expected-panel").hidden = true;
      if (cases.length) showCase({...report, cases}, 0);
      const fields = byId("provenance-fields"); fields.replaceChildren();
      const manifest = object(report.manifest) ? report.manifest : {};
      const identities = [["Protocol", manifest.protocol], ["Data snapshot SHA-256", manifest.data_snapshot_sha256], ["Catalog SHA-256", manifest.catalog_sha256], ["Workload SHA-256", manifest.workload_sha256], ["Compiler SHA-256", manifest.compiler_sha256], ["Source SHA-256", manifest.source_sha256], ["Git commit", manifest.git_head], ["Returned model", report.returned_model]];
      identities.forEach(([name, value]) => { if (typeof value === "string") add(fields, el("dt", "", name), el("dd", "", value)); });
      byId("provenance-panel").hidden = fields.childNodes.length === 0;
      updateMappingAssociation();
      byId("load-status").textContent = "Loaded " + filename + ". " + cases.length + " question records.";
    }
    let selection = 0;
    byId("report-file").addEventListener("change", async event => {
      const ticket = ++selection;
      const file = event.target.files[0];
      if (!file) return;
      try {
        if (file.size > MAX_BYTES) fail("Choose a JSON report no larger than 16 MiB.");
        const source = new TextDecoder("utf-8", {fatal: true}).decode(await file.arrayBuffer());
        if (ticket !== selection) return;
        loadReport(parseReport(source), file.name);
      } catch (error) {
        if (ticket !== selection) return;
        byId("load-error").textContent = "Could not load the report: " + error.message;
        byId("load-error").hidden = false;
        byId("load-status").textContent = "The report could not be loaded. The previous record, if any, is still displayed.";
      }
    });
    let mappingSelection = 0;
    byId("mapping-file").addEventListener("change", async event => {
      const ticket = ++mappingSelection;
      const file = event.target.files[0];
      if (!file) return;
      try {
        if (file.size > MAX_BYTES) fail("Choose a mapping JSON file no larger than 16 MiB.");
        const source = new TextDecoder("utf-8", {fatal: true}).decode(await file.arrayBuffer());
        if (ticket !== mappingSelection) return;
        loadMapping(parseMapping(source), file.name);
      } catch (error) {
        if (ticket !== mappingSelection) return;
        byId("mapping-error").textContent = "Could not load the mapping: " + error.message;
        byId("mapping-error").hidden = false;
        byId("mapping-status").textContent = "The previous mapping, if any, and comparison record are still displayed.";
      }
    });
    return {loadReport, loadMapping};
  }
  const api = {MAX_BYTES, validateReport, parseReport, validateMapping, parseMapping, mappingAssociation, requestFor, formatPath, formatCell, sumUsage, diagnostic, createViewer};
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (typeof document !== "undefined") createViewer(document);
})();
