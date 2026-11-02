"""Pass validated catalog arguments through a bounded, private response file."""

from contextlib import contextmanager
from pathlib import Path
import tempfile

from .validate_dataset import compiler_arguments

HEADER = b"SQLP-CATALOG-ARGS-1\n"
MAX_FILE_BYTES = 16 * 1024 * 1024
MAX_TOKENS = 100_000


def encode_catalog(catalog):
    """Serialize existing CLI tokens; no SQL or new catalog semantics are added."""
    arguments = compiler_arguments(catalog)
    if len(arguments) > MAX_TOKENS:
        raise ValueError("Catalog exceeds the compiler argument-file token limit")
    encoded = bytearray(HEADER)
    for argument in arguments:
        token = argument.encode("utf-8")
        if b"\0" in token:
            raise ValueError("Catalog argument tokens cannot contain NUL")
        if len(encoded) + len(token) + 1 > MAX_FILE_BYTES:
            raise ValueError("Catalog exceeds the compiler argument-file size limit")
        encoded.extend(token)
        encoded.append(0)
    return bytes(encoded)


@contextmanager
def catalog_argument_file(catalog):
    """Yield short argv valid for the context lifetime, cleaned up on all exits."""
    contents = encode_catalog(catalog)
    with tempfile.TemporaryDirectory(prefix="sqlp-catalog-") as directory:
        path = Path(directory) / "catalog.args"
        # The enclosing directory is also private; use explicit file permissions
        # so the format remains safe if the directory policy changes later.
        with path.open("xb") as output:
            path.chmod(0o600)
            output.write(contents)
        yield ["--catalog-args-file", str(path)]
