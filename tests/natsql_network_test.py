"""Compare indexed deduplication with the unchanged pinned NatSQL function."""

import ast
import copy
import hashlib
import json
from pathlib import Path
import random
import sys
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.natsql_network import _NetworkList, enlarge_network_indexed
from tools.natsql_adapter import SOURCE_BLOBS, schema_input


def upstream_functions():
    path = ROOT / "build/natsql-batch-env/upstream/table_transform.py"
    if not path.exists():
        raise unittest.SkipTest("Pinned NatSQL source is not locally available")
    data = path.read_bytes()
    blob = hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
    if blob != SOURCE_BLOBS["table_transform.py"]:
        raise ValueError("Pinned NatSQL source hash changed")
    names = {"table_transform", "get_next_table", "build_table_network",
             "expand_foreign_key", "enlarge_network", "build_index_range"}
    module = ast.parse(data, filename=str(path))
    module.body = [node for node in module.body if isinstance(node, ast.FunctionDef)
                   and node.name in names]
    namespace = {"copy": copy}
    exec(compile(module, str(path), "exec"), namespace)
    return SimpleNamespace(**{name: namespace[name] for name in names})


def prepared_schema(catalog, transform):
    flags = SimpleNamespace(add_alpha_to_table=False, remove_star_from_col=False,
        modify_column_names=False, force_modify_star_column_names=False,
        use_table_name_for_star_in_col_name=False, star_type="others")
    schema = transform.table_transform(schema_input(catalog), flags, None)
    schema = transform.build_index_range(schema)
    schema = transform.expand_foreign_key(schema)
    return transform.build_table_network(schema)


def graph_catalog(size, edges):
    tables = [{"name": "T" + str(i), "columns": [
        {"name": "Id", "type": "integer", "unique": True}]} for i in range(size)]
    refs = []
    for i, (left, right) in enumerate(edges):
        column = "fk" + str(i)
        tables[left]["columns"].append({"name": column, "type": "integer", "unique": False})
        refs.append({"source_table": "T" + str(left), "feature": "r" + str(i),
                     "source_column": column, "target_table": "T" + str(right), "target_column": "Id"})
    return {"version": 1, "tables": tables, "references": refs}


class IndexPredicateTests(unittest.TestCase):
    def test_oriented_edges_and_table_counts_are_preserved(self):
        index = _NetworkList([[[[1, 2]], [0, 1]]])
        self.assertFalse(index.add_if_new([[[1, 2]], [1, 0]]))
        self.assertTrue(index.add_if_new([[[2, 1]], [0, 1]]))
        self.assertTrue(index.add_if_new([[[1, 2]], [0, 1, 2]]))

    def test_repeated_edges_use_subset_semantics_not_multiset_equality(self):
        index = _NetworkList([[[[1, 2], [1, 2]], [0, 1, 2]]])
        self.assertFalse(index.add_if_new([[[1, 2], [3, 4]], [2, 1, 0]]))
        self.assertTrue(index.add_if_new([[[2, 1], [3, 4]], [0, 1, 2]]))

    def test_unconditional_duplicate_appends_and_reverse_deletions(self):
        network = [[[1, 2]], [0, 1]]
        index = _NetworkList([])
        index.append(copy.deepcopy(network))
        index.append(copy.deepcopy(network))
        self.assertEqual(list(index), [network, network])
        self.assertFalse(index.add_if_new(network))
        del index[1]
        del index[0]
        self.assertTrue(index.add_if_new(network))
        self.assertEqual(copy.deepcopy(index), [])


class ExactUpstreamTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.transform = upstream_functions()

    def compare(self, catalog, *, duplicate_fk=False):
        schema = prepared_schema(catalog, self.transform)
        if duplicate_fk and schema["foreign_keys"]:
            schema["foreign_keys"].extend(copy.deepcopy(schema["foreign_keys"][:2]))
        original_schema = copy.deepcopy(schema)
        original_network = copy.deepcopy(schema["network"])
        expected = self.transform.enlarge_network(copy.deepcopy(schema["network"]), copy.deepcopy(schema))
        actual = enlarge_network_indexed(schema["network"], schema, self.transform)
        self.assertEqual(actual, expected)  # Ordered list equality, including duplicates.
        self.assertIs(type(actual), list)
        self.assertEqual(schema, original_schema)
        self.assertEqual(schema["network"], original_network)

    def test_academic_parallel_roles_and_self_reference(self):
        self.compare(json.loads((ROOT / "examples/academic/catalog.json").read_text()))

    def test_chinook_network_exactly_matches_upstream(self):
        path = ROOT / "build/chinook-ready/catalog.json"
        if not path.exists():
            self.skipTest("Prepared Chinook catalog is not locally available")
        self.compare(json.loads(path.read_text()))

    def test_small_cyclic_parallel_and_repeated_foreign_keys(self):
        cases = [(3, [(0, 1), (1, 2), (2, 0)]),
                 (3, [(0, 1), (0, 1), (1, 2)]),
                 (3, [(0, 0), (0, 1), (1, 2)]),
                 (4, [(0, 1), (0, 2), (1, 3), (2, 3)])]
        for size, edges in cases:
            with self.subTest(size=size, edges=edges):
                self.compare(graph_catalog(size, edges), duplicate_fk=True)

    def test_seeded_small_graphs_preserve_every_network_and_order(self):
        rng = random.Random(2026111701)
        for case in range(24):
            size = rng.randrange(2, 5)
            edges = [(rng.randrange(size), rng.randrange(size))
                     for _ in range(rng.randrange(1, 6))]
            with self.subTest(case=case):
                self.compare(graph_catalog(size, edges), duplicate_fk=case % 3 == 0)


if __name__ == "__main__":
    unittest.main()
