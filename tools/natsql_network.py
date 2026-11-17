"""Index the pinned NatSQL network deduplication predicate without pruning.

The adapter supplies the already verified upstream module. We compile its exact
``enlarge_network`` function with only two mechanical changes: replace its
linear ``check_same_net`` scan and track the list that scan inspects. Its loops,
foreign-key orientations, unconditional appends, duplicates, and output order
remain upstream code. This is an explicit adapter profile, never the default.
"""

import ast
from copy import deepcopy
from functools import lru_cache
import inspect
from itertools import combinations
from math import comb
import textwrap


INDEX_PROFILE_VERSION = "oriented-subset-index-v1"


def _edge_key(edge):
    # Upstream schema edges are lists. Preserve the list/tuple distinction too:
    # the original predicate uses Python membership, which distinguishes them.
    return (type(edge).__name__, tuple(edge))


def _edge_set(network):
    return frozenset(_edge_key(edge) for edge in network[0])


class _NetworkList(list):
    """An append/delete index over the upstream list, never an ordered-set list."""

    def __init__(self, values):
        super().__init__(values)
        self._index = {}
        self._dirty = True

    def _add(self, network):
        edges = _edge_set(network)
        by_size = self._index.setdefault(len(network[1]), {})
        by_size.setdefault(len(edges), set()).add(edges)

    def _ensure_index(self):
        if self._dirty:
            self._index = {}
            for network in self:
                self._add(network)
            self._dirty = False

    def append(self, network):
        self._ensure_index()
        super().append(network)
        self._add(network)

    def __delitem__(self, position):
        super().__delitem__(position)
        # The original reverse-delete pass removes long networks in place.
        # Rebuild once after that pass, before the next membership query.
        self._dirty = True

    def __deepcopy__(self, memo):
        # Upstream snapshots are ordinary lists and preserve element order.
        result = []
        memo[id(self)] = result
        result.extend(deepcopy(network, memo) for network in self)
        return result

    def add_if_new(self, network):
        """Match upstream check_same_net, including its subset/duplicate rule."""
        self._ensure_index()
        incoming = _edge_set(network)
        for count, existing in self._index.get(len(network[1]), {}).items():
            if count > len(incoming):
                continue
            if count == len(incoming):
                if incoming in existing:
                    return False
            elif len(existing) <= comb(len(incoming), count):
                if any(edges.issubset(incoming) for edges in existing):
                    return False
            elif any(frozenset(edges) in existing
                     for edges in combinations(incoming, count)):
                return False
        return True


def _check_same_net(network, networks):
    if not isinstance(networks, _NetworkList):
        raise ValueError("Pinned enlargement no longer uses its tracked list")
    return networks.add_if_new(network)


class _IndexPredicate(ast.NodeTransformer):
    def __init__(self):
        self.predicates = 0
        self.list_assignments = 0

    def visit_FunctionDef(self, node):
        if node.name == "check_same_net":
            if [arg.arg for arg in node.args.args] != ["new_net", "all_net_work"]:
                raise ValueError("Pinned network predicate signature changed")
            self.predicates += 1
            node.body = [ast.Return(value=ast.Call(
                func=ast.Name(id="__sqlp_network_check", ctx=ast.Load()),
                args=[ast.Name(id="new_net", ctx=ast.Load()),
                      ast.Name(id="all_net_work", ctx=ast.Load())], keywords=[]))]
            return node
        return self.generic_visit(node)

    def visit_Assign(self, node):
        if (len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id == "new_net_work"
                and isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Attribute)
                and isinstance(node.value.func.value, ast.Name)
                and node.value.func.value.id == "copy"
                and node.value.func.attr == "deepcopy"
                and len(node.value.args) == 1
                and isinstance(node.value.args[0], ast.Name)
                and node.value.args[0].id == "net_work"):
            self.list_assignments += 1
            node.value = ast.Call(func=ast.Name(id="__sqlp_network_list", ctx=ast.Load()),
                                  args=[node.value], keywords=[])
        return self.generic_visit(node)


@lru_cache(maxsize=8)
def _indexed_function(original):
    module = ast.parse(textwrap.dedent(inspect.getsource(original)))
    transformer = _IndexPredicate()
    module = transformer.visit(module)
    if transformer.predicates != 1 or transformer.list_assignments != 1:
        raise ValueError("Pinned enlargement function shape changed")
    ast.fix_missing_locations(module)
    namespace = dict(original.__globals__)
    namespace.update(__sqlp_network_check=_check_same_net,
                     __sqlp_network_list=_NetworkList)
    exec(compile(module, original.__code__.co_filename, "exec"), namespace)
    return namespace[original.__name__]


def enlarge_network_indexed(networks, schema, upstream_transform):
    """Return the same ordered network list with indexed duplicate checks.

    ``upstream_transform`` must come from the adapter's verified source import.
    No schema edge, table, path, or query is selected or removed by this module.
    """
    result = _indexed_function(upstream_transform.enlarge_network)(networks, schema)
    if not isinstance(result, _NetworkList):
        raise ValueError("Pinned enlargement returned an unexpected network list")
    return list(result)
