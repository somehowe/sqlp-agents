"""Build larger, deliberately demanding synthetic SQL/SQLP development tasks.

Four domain-specific relationship graphs provide 1,000 questions by default.
The English questions, path plans, and reference queries are project-authored;
they are correlated constructed cases and still need human semantic review.
No model calls, external data, or credentials are used by this module.
"""

import argparse
from collections import Counter, defaultdict
from contextlib import closing
import hashlib
import json
from pathlib import Path
import random
import shutil
import sqlite3

from .compiler_catalog import catalog_argument_file
from .run_comparison import DEFAULT_COMPILER, _compile
from .validate_dataset import validate_dataset


DOMAINS = {
    "commerce": {
        "root": "OrderLine", "noun": "order line",
        "edges": (
            ("OrderLine", "order", "PurchaseOrder"), ("OrderLine", "product", "Product"),
            ("PurchaseOrder", "customer", "Customer"), ("PurchaseOrder", "salesRep", "Employee"),
            ("PurchaseOrder", "warehouse", "Warehouse"), ("Customer", "accountManager", "Employee"),
            ("Customer", "region", "Region"), ("Product", "supplier", "Supplier"),
            ("Product", "category", "Category"), ("Supplier", "contact", "Employee"),
            ("Supplier", "region", "Region"), ("Warehouse", "manager", "Employee"),
            ("Warehouse", "region", "Region"), ("Employee", "manager", "Employee"),
            ("Employee", "mentor", "Employee"), ("Employee", "office", "Office"),
            ("Office", "region", "Region"), ("Region", "country", "Country"),
        ),
    },
    "clinical": {
        "root": "Encounter", "noun": "encounter",
        "edges": (
            ("Encounter", "patient", "Patient"), ("Encounter", "attending", "Doctor"),
            ("Encounter", "referrer", "Doctor"), ("Encounter", "ward", "Ward"),
            ("Patient", "primaryDoctor", "Doctor"), ("Patient", "insurer", "Insurer"),
            ("Patient", "residence", "Region"), ("Doctor", "supervisor", "Doctor"),
            ("Doctor", "mentor", "Doctor"), ("Doctor", "hospital", "Hospital"),
            ("Ward", "hospital", "Hospital"), ("Ward", "head", "Doctor"),
            ("Hospital", "network", "Network"), ("Hospital", "region", "Region"),
            ("Network", "director", "Doctor"), ("Insurer", "office", "Region"),
            ("Region", "country", "Country"),
        ),
    },
    "logistics": {
        "root": "Shipment", "noun": "shipment",
        "edges": (
            ("Shipment", "sender", "Depot"), ("Shipment", "recipient", "Depot"),
            ("Shipment", "carrier", "Carrier"), ("Shipment", "driver", "Driver"),
            ("Depot", "supervisor", "Dispatcher"), ("Depot", "region", "Region"),
            ("Carrier", "headquarters", "Depot"), ("Carrier", "contact", "Dispatcher"),
            ("Driver", "dispatcher", "Dispatcher"), ("Driver", "homeDepot", "Depot"),
            ("Dispatcher", "manager", "Dispatcher"), ("Dispatcher", "mentor", "Dispatcher"),
            ("Dispatcher", "company", "Carrier"), ("Region", "country", "Country"),
        ),
    },
    "research": {
        "root": "GrantAward", "noun": "grant award",
        "edges": (
            ("GrantAward", "lead", "Researcher"), ("GrantAward", "reviewer", "Researcher"),
            ("GrantAward", "project", "Project"), ("GrantAward", "panel", "Panel"),
            ("Researcher", "mentor", "Researcher"), ("Researcher", "advisor", "Researcher"),
            ("Researcher", "institution", "Institution"), ("Project", "sponsor", "Sponsor"),
            ("Project", "host", "Institution"), ("Project", "coordinator", "Researcher"),
            ("Panel", "chair", "Researcher"), ("Panel", "sponsor", "Sponsor"),
            ("Institution", "region", "Region"), ("Institution", "director", "Researcher"),
            ("Sponsor", "headquarters", "Region"), ("Sponsor", "officer", "Researcher"),
            ("Region", "country", "Country"),
        ),
    },
}
DEPTHS = (1, 2, 3, 4, 6, 8)
FAMILIES = (
    "path_projection", "competing_roles", "shared_prefix", "multiple_filters",
    "duplicate_bag", "distinct_tuples", "three_branches", "role_filters",
    "empty_answer_control", "integer_filter",
)
COUNTRIES = ("Canada", "Denmark", "Japan", "Kenya", "Côte d'Ivoire", "New Zealand", "Brazil", "India")
FIELDS = ("source_table", "feature", "source_column", "target_table", "target_column")


def _quote(name):
    return '"' + name.replace('"', '""') + '"'


def _literal(value):
    return str(value) if type(value) is int else "'" + value.replace("'", "''") + "'"


def _write(path, value):
    Path(path).write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _references(spec):
    return tuple((owner, role, role + "Id", target, "Id") for owner, role, target in spec["edges"])


def make_catalog(domain):
    spec = DOMAINS[domain]
    tables = dict.fromkeys([spec["root"], *(name for edge in spec["edges"] for name in (edge[0], edge[2]))])
    references = _references(spec)
    result = []
    for table in tables:
        columns = [("Id", "integer"), ("Name", "string"), ("Country", "string"),
                   ("Label", "string"), ("Score", "integer")]
        if table == spec["root"]:
            columns += [("Code", "string"), ("Batch", "integer"), ("Year", "integer")]
        columns += [(source, "integer") for owner, _, source, _, _ in references if owner == table]
        result.append({"name": table, "columns": [{"name": name, "type": kind, "unique": name == "Id"}
                                                   for name, kind in columns]})
    return {"version": 1, "tables": result, "references": [dict(zip(FIELDS, ref)) for ref in references]}


def create_snapshot(path, domain, seed, *, root_rows=12000):
    """Create fictional records; every declared relationship is total and keyed."""
    if Path(path).exists():
        raise ValueError("Snapshot destination already exists")
    if type(root_rows) is not int or not 48 <= root_rows <= 24000:
        raise ValueError("root_rows must be an integer from 48 through 24000")
    catalog, spec = make_catalog(domain), DOMAINS[domain]
    rng = random.Random(seed)
    counts = {table["name"]: root_rows if table["name"] == spec["root"] else 384
              for table in catalog["tables"]}
    records, indexes = {}, {}
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("BEGIN")
        for table in catalog["tables"]:
            name = table["name"]
            declarations = [_quote(col["name"]) + (" INTEGER" if col["type"] == "integer" else " TEXT") +
                            (" PRIMARY KEY NOT NULL" if col["name"] == "Id" else " NOT NULL") for col in table["columns"]]
            for ref in catalog["references"]:
                if ref["source_table"] == name:
                    declarations.append("FOREIGN KEY (" + _quote(ref["source_column"]) + ") REFERENCES " +
                                        _quote(ref["target_table"]) + ' ("Id") DEFERRABLE INITIALLY DEFERRED')
            connection.execute("CREATE TABLE " + _quote(name) + " (" + ", ".join(declarations) + ")")
        for table in catalog["tables"]:
            name, columns = table["name"], [col["name"] for col in table["columns"]]
            references = [ref for ref in catalog["references"] if ref["source_table"] == name]
            rows = []
            for identifier in range(1, counts[name] + 1):
                # Many distinct entities deliberately share names; keys never do.
                row = {"Id": identifier, "Name": name + " name " + str(identifier % 31),
                       "Country": rng.choice(COUNTRIES), "Label": name + " group " + str(identifier % 7),
                       "Score": rng.randrange(1, 17)}
                if name == spec["root"]:
                    row.update(Code=domain[:3].upper() + "-" + str(identifier).zfill(6),
                               Batch=(identifier - 1) % 24, Year=2020 + identifier % 7)
                for ref in references:
                    row[ref["source_column"]] = rng.randrange(1, counts[ref["target_table"]] + 1)
                rows.append(row)
            records[name] = rows
            indexes[name] = {row["Id"]: row for row in rows}
            connection.executemany("INSERT INTO " + _quote(name) + " VALUES (" + ",".join("?" for _ in columns) + ")",
                                   [[row[column] for column in columns] for row in rows])
        connection.commit()
        if connection.execute("PRAGMA foreign_key_check").fetchall():
            raise ValueError("Generated snapshot fails foreign key checks")
        preflight = validate_dataset(connection, catalog)
        if not preflight["ok"]:
            raise ValueError("Generated snapshot fails catalog preflight: " + json.dumps(preflight))
    return records, indexes, counts


def _value(spec, indexes, root, path):
    mapping = {(owner, role): (role + "Id", target) for owner, role, target in spec["edges"]}
    table, record = spec["root"], root
    for role in path[:-1]:
        source, target = mapping[table, role]
        table, record = target, indexes[target][record[source]]
    return record[path[-1]]


def oracle_rows(domain, plan, records, indexes):
    """Interpret private paths with dictionary lookups, without SQL or compiler."""
    spec = DOMAINS[domain]
    mapping = {(owner, role): (role + "Id", target) for owner, role, target in spec["edges"]}
    def value(record, path):
        table = spec["root"]
        for role in path[:-1]:
            source, target = mapping[table, role]
            table, record = target, indexes[target][record[source]]
        return record[path[-1]]
    rows = []
    for record in records[spec["root"]]:
        if all(value(record, path) == literal for path, literal in plan["predicates"]):
            rows.append(tuple(value(record, path) for path in plan["projections"]))
    if plan["distinct"]:
        rows = list(set(rows))
    return [list(row) for row in sorted(rows, key=lambda value: json.dumps(value, ensure_ascii=False))]


def _path_pool(spec):
    outgoing = defaultdict(list)
    for owner, role, target in spec["edges"]:
        outgoing[owner].append((role, target))
    current, result = [((), spec["root"])], {}
    for depth in range(1, 9):
        current = [(prefix + (role,), target) for prefix, table in current for role, target in outgoing[table]]
        # Bound generation independently of database size or requested task count.
        current = current[:10000]
        if depth in DEPTHS:
            result[depth] = [path for path, _ in current]
    return result


def _words(name):
    import re
    return re.sub(r"(?<=[a-z])(?=[A-Z])", " ", name).lower()


def _phrase(path):
    field = "stored " + path[-1] + " field"
    if len(path) == 1:
        return "its " + field
    return "the " + field + " of its " + "'s ".join(_words(component) for component in path[:-1])


def _abstract_template(spec, shape):
    """Remove table/role spellings but retain join sharing and table revisits."""
    mapping = {(owner, role): target for owner, role, target in spec["edges"]}
    tables, roles, aliases, edges = {spec["root"]: 0}, {}, {(): (0, spec["root"])}, []
    def path_id(path):
        prefix = ()
        for role in path[:-1]:
            source_alias, source_table = aliases[prefix]
            following = prefix + (role,)
            if following not in aliases:
                target = mapping[source_table, role]
                tables.setdefault(target, len(tables))
                source_roles = roles.setdefault(source_table, {})
                source_roles.setdefault(role, len(source_roles))
                aliases[following] = (len(aliases), target)
                edges.append((source_alias, aliases[following][0], tables[source_table], tables[target], source_roles[role]))
            prefix = following
        return (aliases[prefix][0], path[-1])
    projections = [path_id(path) for path in shape["projections"]]
    predicates = [path_id(path) for path in shape["predicate_paths"]]
    encoded = {"edges": edges, "projections": projections, "predicates": predicates, "distinct": shape["distinct"]}
    return hashlib.sha256(json.dumps(encoded, sort_keys=True).encode()).hexdigest()[:16]


def _question(spec, projections, predicates, distinct):
    noun = spec["noun"]
    conditions = []
    for path, literal in predicates:
        value = str(literal) if type(literal) is int else '"' + literal + '"'
        conditions.append(_phrase(path) + " equals " + value)
    selected = "; then ".join(_phrase(path) for path in projections)
    result = "For each " + noun + " where " + " and ".join(conditions) + ", return " + selected + ", in that column order. "
    if distinct:
        result += "Return each distinct output tuple once, even when several qualifying " + noun + " records produce it."
    else:
        result += "Keep one output row per qualifying " + noun + "; preserve repeated output tuples."
    return result


def task_plans(domain, records, indexes, *, count=250):
    """Vary graph paths, branching, projection shape, predicates, and bag semantics."""
    if type(count) is not int or not 1 <= count <= 2000:
        raise ValueError("cases_per_domain must be an integer from 1 through 2000")
    spec, plans = DOMAINS[domain], []
    pool = _path_pool(spec)
    structural_seen, query_seen = Counter(), set()
    for index in range(count):
        family = FAMILIES[index % len(FAMILIES)]
        depth = DEPTHS[(index // len(FAMILIES)) % len(DEPTHS)]
        candidates = pool[depth]
        ordinal = index // (len(FAMILIES) * len(DEPTHS))
        left = candidates[(ordinal * 7 + index % len(FAMILIES)) % len(candidates)]
        other = [path for path in candidates if path[0] != left[0]] or candidates
        right = other[(ordinal * 11 + index % len(FAMILIES)) % len(other)]
        third = candidates[(ordinal * 13 + index % len(FAMILIES) + len(candidates) // 2) % len(candidates)]
        batch = (index * 7 + ordinal) % 24
        root = next(row for row in records[spec["root"]] if row["Batch"] == batch)
        projections, predicates, distinct = [("Code",), (*left, "Name")], [(("Batch",), batch)], False
        if family == "competing_roles":
            projections += [(*right, "Name")]
        elif family == "shared_prefix":
            projections += [(*left, "Country"), (*left, "Score")]
        elif family == "multiple_filters":
            projections += [(*right, "Name")]
            predicates += [(path, _value(spec, indexes, root, path)) for path in ((*left, "Country"), (*right, "Label"))]
        elif family in ("duplicate_bag", "distinct_tuples"):
            projections = [(*left, "Name")]
            distinct = family == "distinct_tuples"
        elif family == "three_branches":
            projections += [(*right, "Name"), (*third, "Label")]
        elif family == "role_filters":
            projections += [(*right, "Name"), ("Year",)]
            predicates += [(path, _value(spec, indexes, root, path)) for path in ((*left, "Country"), (*right, "Country"))]
        elif family == "empty_answer_control":
            predicates += [((*left, "Country"), "__no_such_country__")]
        elif family == "integer_filter":
            predicates += [((*left, "Score"), _value(spec, indexes, root, (*left, "Score")))]
            projections += [(*right, "Score")]
        shape = {"projections": projections, "predicate_paths": [path for path, _ in predicates], "distinct": distinct}
        template = hashlib.sha256(json.dumps(shape, sort_keys=True).encode()).hexdigest()[:16]
        structural_seen[template] += 1
        plan = {"id": domain + "-" + str(index + 1).zfill(4), "domain": domain,
                "family": family, "depth": depth, "template_id": domain + "-" + template,
                "abstract_template_id": _abstract_template(spec, shape),
                "projections": projections, "predicates": predicates, "distinct": distinct}
        # If an otherwise identical structural template recurs, require a new
        # batch predicate; this is explicit template reuse, not a new template.
        signature = json.dumps({key: plan[key] for key in ("projections", "predicates", "distinct")}, sort_keys=True)
        if signature in query_seen:
            for offset in range(1, 24):
                changed = (batch + offset) % 24
                plan["predicates"][0] = (("Batch",), changed)
                changed_root = next(row for row in records[spec["root"]] if row["Batch"] == changed)
                if family != "empty_answer_control":
                    plan["predicates"][1:] = [(path, _value(spec, indexes, changed_root, path))
                                             for path, _ in plan["predicates"][1:]]
                signature = json.dumps({key: plan[key] for key in ("projections", "predicates", "distinct")}, sort_keys=True)
                if signature not in query_seen:
                    break
            else:
                raise ValueError("Requested workload exhausts distinct task shapes/batch values")
        query_seen.add(signature)
        plan["question"] = _question(spec, projections, plan["predicates"], distinct)
        plans.append(plan)
    if len({plan["question"] for plan in plans}) != len(plans):
        raise ValueError("Generated questions are not distinct")
    return plans


def reference_queries(domain, plan):
    """Render explicit SQL key joins independently of the SQLP compiler."""
    spec = DOMAINS[domain]
    mapping = {(owner, role): (source, target, key) for owner, role, source, target, key in _references(spec)}
    aliases, joins = {(): ("r", spec["root"])}, []
    def column(path):
        prefix = ()
        for role in path[:-1]:
            alias, table = aliases[prefix]
            following = prefix + (role,)
            if following not in aliases:
                source, target, key = mapping[table, role]
                target_alias = "j" + str(len(aliases))
                aliases[following] = (target_alias, target)
                joins.append("JOIN " + _quote(target) + " AS " + target_alias + " ON " + alias + "." + _quote(source) +
                             " = " + target_alias + "." + _quote(key))
            prefix = following
        return aliases[prefix][0] + "." + _quote(path[-1])
    keyword = "SELECT DISTINCT " if plan["distinct"] else "SELECT "
    selected = [column(path) for path in plan["projections"]]
    conditions = [column(path) + " = " + _literal(value) for path, value in plan["predicates"]]
    sql = keyword + ", ".join(selected) + "\nFROM " + _quote(spec["root"]) + " AS r\n" + "\n".join(joins)
    sql += "\nWHERE " + " AND ".join(conditions) + ";"
    sqlp = keyword + ", ".join("r." + ".".join(path) for path in plan["projections"])
    sqlp += "\nFROM " + spec["root"] + " AS r\nWHERE "
    sqlp += " AND ".join("r." + ".".join(path) + " = " + _literal(value) for path, value in plan["predicates"]) + ";"
    return sql, sqlp, len(joins)


def _workload(domain, plans, records, indexes, compiled, database):
    cases = []
    with closing(sqlite3.connect(database)) as connection:
        for plan in plans:
            sql, sqlp, joins = reference_queries(domain, plan)
            expected = oracle_rows(domain, plan, records, indexes)
            if len(expected) > 1000:
                raise ValueError("Oracle result exceeds fixed 1000-row evaluation cap: " + plan["id"])
            reference = Counter(map(tuple, expected))
            for language, query, parameters in (("sql", sql, []), ("sqlp", compiled[plan["id"]][0], compiled[plan["id"]][1])):
                cursor = connection.execute(query, parameters)
                if len(cursor.description) != len(plan["projections"]) or Counter(cursor.fetchall()) != reference:
                    raise ValueError(language + " reference disagrees with Python traversal: " + plan["id"])
            cases.append({"id": plan["id"], "question": plan["question"], "sql": sql, "sqlp": sqlp,
                          "column_count": len(plan["projections"]), "expected_rows": expected,
                          "design": {key: plan[key] for key in ("domain", "depth", "family", "template_id", "abstract_template_id", "distinct") } |
                                    {"join_count": joins, "predicate_count": len(plan["predicates"]),
                                     "projection_count": len(plan["projections"]), "expected_empty": not expected,
                                     "deliberate_empty_control": plan["family"] == "empty_answer_control"}})
    return {"version": 1, "scope": "development", "dataset_kind": "constructed synthetic multi-domain path study",
            "human_answer_review": "pending", "domain": domain, "cases": cases}


def prepare(output_dir, compiler_path=DEFAULT_COMPILER, *, cases_per_domain=250, seed=20261116,
            root_rows=12000, alternate_seeds=(20261117,)):
    """Produce fresh primary/contrast datasets, checked reference pairs, hashes."""
    if type(seed) is not int or seed < 0 or any(type(value) is not int or value < 0 for value in alternate_seeds):
        raise ValueError("Snapshot seeds must be nonnegative integers")
    if len(set((seed, *alternate_seeds))) != len(alternate_seeds) + 1 or len(alternate_seeds) > 3:
        raise ValueError("Use at most three different alternate snapshot seeds")
    if type(cases_per_domain) is not int or not 1 <= cases_per_domain <= 2000:
        raise ValueError("cases_per_domain must be an integer from 1 through 2000")
    output_dir, compiler_path = Path(output_dir), Path(compiler_path).resolve()
    if not compiler_path.is_file():
        raise ValueError("Build the C++ compiler first: " + str(compiler_path))
    output_dir.mkdir(mode=0o700, parents=True, exist_ok=False)
    datasets = []
    try:
        for domain_index, domain in enumerate(DOMAINS):
            directory = output_dir / domain
            directory.mkdir(mode=0o700)
            catalog = make_catalog(domain)
            _write(directory / "catalog.json", catalog)
            records, indexes, counts = create_snapshot(directory / "database.sqlite", domain, seed + domain_index * 1009, root_rows=root_rows)
            plans = task_plans(domain, records, indexes, count=cases_per_domain)
            _write(directory / "oracle-plans.json", plans)
            compiled = {}
            with catalog_argument_file(catalog) as arguments:
                for plan in plans:
                    _, sqlp, _ = reference_queries(domain, plan)
                    result, parameters = _compile(sqlp, arguments, compiler_path, 10)
                    if not result["ok"]:
                        raise ValueError("Prepared SQLP failed compilation: " + plan["id"] + " " + str(result.get("error")))
                    compiled[plan["id"]] = (result["sql"], parameters)
            snapshots = []
            snapshot_records = [("primary", directory, records, indexes, counts, seed + domain_index * 1009)]
            for index, alternate in enumerate(alternate_seeds, 1):
                alternate_dir = directory / ("contrast-" + str(index))
                alternate_dir.mkdir(mode=0o700)
                other_records, other_indexes, other_counts = create_snapshot(alternate_dir / "database.sqlite", domain,
                    alternate + domain_index * 1009, root_rows=root_rows)
                snapshot_records.append(("contrast-" + str(index), alternate_dir, other_records, other_indexes, other_counts, alternate + domain_index * 1009))
            primary_rows = None
            changed_cases = set()
            for snapshot_id, folder, snapshot_rows, snapshot_indexes, row_counts, snapshot_seed in snapshot_records:
                workload = _workload(domain, plans, snapshot_rows, snapshot_indexes, compiled, folder / "database.sqlite")
                _write(folder / "workload.json", workload)
                bags = {case["id"]: Counter(map(tuple, case["expected_rows"])) for case in workload["cases"]}
                if primary_rows is None:
                    primary_rows = bags
                else:
                    changed_cases.update(case_id for case_id in bags if bags[case_id] != primary_rows[case_id])
                snapshots.append({"id": snapshot_id, "seed": snapshot_seed,
                                  "database": str((folder / "database.sqlite").relative_to(output_dir)),
                                  "workload": str((folder / "workload.json").relative_to(output_dir)),
                                  "row_counts": row_counts, "prepared_sql_matches": len(plans), "prepared_sqlp_matches": len(plans),
                                  "empty_answer_cases": sum(not case["expected_rows"] for case in workload["cases"]),
                                  "duplicate_bag_cases": sum(len(case["expected_rows"]) > len(set(map(tuple, case["expected_rows"]))) for case in workload["cases"]),
                                  "database_sha256": _sha(folder / "database.sqlite"), "workload_sha256": _sha(folder / "workload.json")})
            templates = Counter(plan["template_id"] for plan in plans)
            datasets.append({"id": domain, "directory": domain, "case_count": len(plans), "row_counts": counts,
                             "table_count": len(catalog["tables"]), "reference_count": len(catalog["references"]),
                             "structural_template_count": len(templates), "reused_template_cases": sum(n - 1 for n in templates.values()),
                             "abstract_structural_template_count": len({plan["abstract_template_id"] for plan in plans}),
                             "family_counts": dict(Counter(plan["family"] for plan in plans)),
                             "depth_counts": dict(Counter(str(plan["depth"]) for plan in plans)),
                             "cases_with_changed_answer_on_contrast": len(changed_cases), "snapshots": snapshots})
        files = {str(path.relative_to(output_dir)): _sha(path) for path in sorted(output_dir.rglob("*")) if path.is_file()}
        manifest = {"version": 1, "study": "synthetic multi-domain scale study", "case_count": cases_per_domain * len(DOMAINS),
                    "datasets": datasets, "builder_sha256": _sha(__file__), "compiler_sha256": _sha(compiler_path),
                    "files_sha256": files, "model_calls": 0, "human_answer_review": "pending",
                    "structural_template_count": sum(dataset["structural_template_count"] for dataset in datasets),
                    "abstract_structural_template_count": len({case["design"]["abstract_template_id"] for dataset in datasets
                        for case in json.loads((output_dir / dataset["directory"] / "workload.json").read_text())["cases"]}),
                    "deliberate_empty_control_cases": sum(dataset["family_counts"].get("empty_answer_control", 0) for dataset in datasets),
                    "prepared_query_executions": sum(len(dataset["snapshots"]) * dataset["case_count"] * 2 for dataset in datasets),
                    "limitations": ["Constructed fictional records; no actual patient, customer, or personnel data",
                                    "Templates and graph variants are correlated, not independent benchmark questions",
                                    "Different graph topologies share one generator and generic synthetic field distributions; no independent domain-generalization claim",
                                    "Stored Country fields are artificial independent labels, distinct from country relationship targets",
                                    "Ten percent of the default questions are labelled deliberately empty-answer controls",
                                    "English and semantic plans share an author; independent human meaning review remains pending",
                                    "SQLP fragment covers finite relationship paths, AND literal equality, projections and DISTINCT",
                                    "No aggregates, OR, range comparisons, subqueries, or many-to-many inverse traversal",
                                    "Prepared query matches validate infrastructure and do not measure model accuracy"]}
        _write(output_dir / "manifest.json", manifest)
        return manifest
    except Exception:
        # This process exclusively created this fresh output directory.
        shutil.rmtree(output_dir)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--compiler", type=Path, default=DEFAULT_COMPILER)
    parser.add_argument("--cases-per-domain", type=int, default=250)
    parser.add_argument("--root-rows", type=int, default=12000)
    parser.add_argument("--seed", type=int, default=20261116)
    parser.add_argument("--alternate-seed", type=int, action="append")
    args = parser.parse_args()
    manifest = prepare(args.output_dir, args.compiler, cases_per_domain=args.cases_per_domain,
                       root_rows=args.root_rows, seed=args.seed,
                       alternate_seeds=tuple(args.alternate_seed) if args.alternate_seed is not None else (args.seed + 1,))
    print(json.dumps({"ok": True, "output_dir": str(args.output_dir), "case_count": manifest["case_count"],
                      "structural_template_count": manifest["structural_template_count"], "model_calls": 0}))


if __name__ == "__main__":
    main()
