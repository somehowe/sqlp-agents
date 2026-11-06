"""Read a local API credential without evaluating application configuration."""

from pathlib import Path
import re

MAX_CREDENTIAL_BYTES = 64 * 1024
_ASSIGNMENT = re.compile(r"(?:export[ \t]+)?([A-Za-z_][A-Za-z0-9_]*)[ \t]*=[ \t]*(.*)")
_ERROR = "Credential file must contain exactly one valid API key"


def _key(value):
    if not value or any(ord(char) <= 32 or ord(char) >= 127 for char in value):
        raise ValueError(_ERROR)
    return value


def load_api_key(path):
    """Accept a raw key or literal OPENAI_API_KEY/AI_API_KEY; never echo contents.

    Other assignments are ignored. No shell expansion, environment mutation,
    interpolation, provider selection, or network access occurs here.
    """
    with Path(path).open("rb") as source:
        encoded = source.read(MAX_CREDENTIAL_BYTES + 1)
    if len(encoded) > MAX_CREDENTIAL_BYTES:
        raise ValueError(_ERROR)
    try:
        contents = encoded.decode("utf-8")
    except UnicodeError:
        raise ValueError(_ERROR) from None
    lines = contents.splitlines()
    if len(lines) == 1 and not _ASSIGNMENT.fullmatch(lines[0]) and not lines[0].lstrip().startswith("#"):
        return _key(lines[0])
    found = []
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = _ASSIGNMENT.fullmatch(line)
        if match is None:
            raise ValueError(_ERROR)
        name, value = match.groups()
        if name not in ("OPENAI_API_KEY", "AI_API_KEY"):
            continue
        value = value.strip()
        if value.startswith(("'", '"')):
            if len(value) < 2 or value[-1] != value[0]:
                raise ValueError(_ERROR)
            value = value[1:-1]
        found.append(_key(value))
    if len(found) != 1:
        raise ValueError(_ERROR)
    return found[0]
