"""Opt-in lossless transport for SQL single-quoted NatSQL string literals.

The pinned converter changes apostrophes before tokenization. This helper only
transports existing string tokens through that converter; it does not repair
syntax, infer values, or inspect a schema/database. Native conversion is separate.
"""

import hashlib
from pathlib import Path
import re


VERSION = 1
PROFILE = "single_quoted_sql_literals_v1"
_MARKER = re.compile(r"sqlp-literal-[0-9a-f]{64}-[0-9]{4,}\Z")


def transport_metadata():
    return {"version": VERSION, "profile": PROFILE,
            "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}


def _text(value):
    if not isinstance(value, str):
        raise ValueError("Literal transport requires a text query")
    try:
        value.encode("utf-8")
    except UnicodeError as error:
        raise ValueError("Literal transport requires valid Unicode") from error


def _quoted(query, start, quote):
    """Read SQL doubled-quote escaping; backslashes have no escape meaning."""
    position, parts = start + 1, []
    while position < len(query):
        if query[position] == quote:
            if position + 1 < len(query) and query[position + 1] == quote:
                parts.append(quote)
                position += 2
                continue
            return position + 1, "".join(parts)
        parts.append(query[position])
        position += 1
    raise ValueError("Unterminated quoted token at character " + str(start))


def _tokens(query):
    """Yield spans and decoded quoted tokens, skipping whitespace and comments."""
    position = 0
    while position < len(query):
        start, character = position, query[position]
        if character.isspace():
            position += 1
            continue
        if query.startswith("--", position):
            position += 2
            while position < len(query) and query[position] not in "\r\n":
                position += 1
            continue
        if query.startswith("/*", position):
            end = query.find("*/", position + 2)
            if end < 0:
                raise ValueError("Unterminated SQL comment at character " + str(start))
            position = end + 2
            continue
        if character in "'\"`":
            position, value = _quoted(query, position, character)
            kind = {"'": "single", '"': "double", "`": "identifier"}[character]
            # SQLite X'AB' is a blob token, not a string token.
            if (character == "'" and start > 0 and query[start - 1] in "xX"
                    and (start == 1 or not (query[start - 2].isalnum() or query[start - 2] == "_"))):
                kind = "blob"
            yield kind, start, position, value
            continue
        if character == "[":
            end = query.find("]", position + 1)
            if end < 0:
                raise ValueError("Unterminated quoted identifier at character " + str(start))
            position = end + 1
            yield "identifier", start, position, query[start + 1:end]
            continue
        if character.isalpha() or character == "_":
            position += 1
            while position < len(query) and (query[position].isalnum() or query[position] == "_"):
                position += 1
            yield "word", start, position, query[start:position]
            continue
        position += 1
        yield "punctuation", start, position, character


def protect_literals(query):
    """Return protected text and a deterministic occurrence-preserving mapping."""
    _text(query)
    source_tokens = list(_tokens(query))
    tokens = [token for index, token in enumerate(source_tokens)
              if token[0] == "single" and not _identifier_position(source_tokens, index)]
    source_hash = hashlib.sha256(query.encode("utf-8")).hexdigest()
    salt = 0
    while True:
        digest = hashlib.sha256((PROFILE + "\0" + str(salt) + "\0" + query).encode("utf-8")).hexdigest()
        markers = ["sqlp-literal-" + digest + "-" + str(index).zfill(4)
                   for index in range(len(tokens))]
        if all(marker not in query for marker in markers):
            break
        salt += 1
    mapping = {"version": VERSION, "source_sha256": source_hash, "literals": []}
    fragments, cursor = [], 0
    for marker, (_, start, end, value) in zip(markers, tokens):
        fragments += [query[cursor:start], "'" + marker + "'"]
        cursor = end
        mapping["literals"].append({"marker": marker, "value": value,
                                    "source_lexeme": query[start:end],
                                    "start": start, "end": end})
    fragments.append(query[cursor:])
    return "".join(fragments), mapping


def _mapping_values(mapping):
    if (not isinstance(mapping, dict) or mapping.get("version") != VERSION
            or type(mapping.get("version")) is not int
            or not isinstance(mapping.get("source_sha256"), str)
            or re.fullmatch(r"[0-9a-f]{64}", mapping["source_sha256"]) is None
            or not isinstance(mapping.get("literals"), list)):
        raise ValueError("Invalid literal transport mapping")
    values = {}
    for item in mapping["literals"]:
        if (not isinstance(item, dict) or not isinstance(item.get("marker"), str)
                or _MARKER.fullmatch(item["marker"]) is None
                or not isinstance(item.get("value"), str)
                or not isinstance(item.get("source_lexeme"), str)
                or not item["source_lexeme"].startswith("'")):
            raise ValueError("Invalid literal transport entry")
        end, value = _quoted(item["source_lexeme"], 0, "'")
        if end != len(item["source_lexeme"]) or value != item["value"]:
            raise ValueError("Literal mapping value differs from its source token")
        if item["marker"] in values:
            raise ValueError("Duplicate literal transport marker")
        _text(value)
        values[item["marker"]] = value
    return values


def _identifier_position(tokens, index):
    previous = tokens[index - 1] if index else None
    following = tokens[index + 1] if index + 1 < len(tokens) else None
    if ((previous and previous[3] == ".") or (following and following[3] == ".")):
        return True
    if previous and previous[0] == "word" and previous[3].upper() in {
            "FROM", "JOIN", "AS", "COLLATE", "TABLE", "INDEX", "INTO", "UPDATE", "REFERENCES"}:
        return True
    # SQLite also accepts single-quoted names in these identifier positions.
    return False


def restore_literals(sql, mapping):
    """Restore exact marker string tokens; preserve all other SQL source bytes.

    The converter emits double-quoted values. Hyphenated markers cannot name an
    actual schema identifier under this adapter's simple ASCII identifier
    profile, and did not occur in the input. Clearly qualified/table/alias
    identifiers are still left untouched. Comments, substrings, backticks and
    bracketed identifiers never participate. Unused mapping entries are allowed:
    upstream may eliminate a redundant predicate or duplicate an expression.
    """
    _text(sql)
    values = _mapping_values(mapping)
    tokens = list(_tokens(sql))
    fragments, cursor = [], 0
    for index, (kind, start, end, value) in enumerate(tokens):
        if kind not in {"single", "double"}:
            continue
        if _identifier_position(tokens, index):
            continue
        if value not in values:
            if _MARKER.fullmatch(value):
                raise ValueError("Unrecognized literal transport marker")
            continue
        fragments += [sql[cursor:start], "'" + values[value].replace("'", "''") + "'"]
        cursor = end
    fragments.append(sql[cursor:])
    return "".join(fragments)
