"""Credential-file parsing checks using temporary dummy credentials only."""

from contextlib import redirect_stderr, redirect_stdout
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.credentials import load_api_key


DUMMY_KEY = "offline-fixture-credential"


class CredentialFileTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.path = self.directory / "credential"

    def write(self, text):
        self.path.write_bytes(text.encode("utf-8"))
        return self.path

    def assert_invalid(self, text):
        self.write(text)
        output = io.StringIO()
        with redirect_stdout(output), redirect_stderr(output):
            with self.assertRaises(ValueError) as caught:
                load_api_key(self.path)
        self.assertEqual(output.getvalue(), "")
        self.assertNotIn(DUMMY_KEY, str(caught.exception))
        return str(caught.exception)

    def test_raw_key_with_optional_final_line_ending(self):
        for ending in ("", "\n", "\r\n"):
            with self.subTest(ending=repr(ending)):
                self.assertEqual(load_api_key(self.write(DUMMY_KEY + ending)), DUMMY_KEY)

    def test_dotenv_uses_only_key_assignment_with_quotes_and_export(self):
        assignments = [assignment for name in ("OPENAI_API_KEY", "AI_API_KEY")
                       for assignment in (f"{name}={DUMMY_KEY}",
                                          f"export {name}='{DUMMY_KEY}'",
                                          f'{name} = "{DUMMY_KEY}"')]
        for assignment in assignments:
            with self.subTest(assignment_style=assignment.split("=")[0]):
                contents = ("# Local application configuration\n\n"
                            "PROVIDER=compatible\nAI_MODEL=unused-model\n"
                            "AI_BASE_URL=https://unused.invalid/v1\n"
                            "AI_REASONING_EFFORT=high\nEXTENSION_ID=\n"
                            + assignment + "\n")
                self.assertEqual(load_api_key(self.write(contents)), DUMMY_KEY)

    def test_missing_duplicate_and_empty_assignment_fail_without_echo(self):
        for contents in ("PROVIDER=openai\nAI_MODEL=unused\n",
                         "# OPENAI_API_KEY is deliberately missing\n",
                         "OPENAI_API_KEY=\n", 'OPENAI_API_KEY=""\n',
                         "AI_API_KEY=\n", 'AI_API_KEY=""\n',
                         f"OPENAI_API_KEY={DUMMY_KEY}\nOPENAI_API_KEY={DUMMY_KEY}\n",
                         f"OPENAI_API_KEY={DUMMY_KEY}\nexport OPENAI_API_KEY=other\n",
                         f"AI_API_KEY={DUMMY_KEY}\nAI_API_KEY={DUMMY_KEY}\n",
                         f"OPENAI_API_KEY={DUMMY_KEY}\nAI_API_KEY={DUMMY_KEY}\n"):
            with self.subTest(kind=contents.splitlines()[0].split("=")[0]):
                self.assert_invalid(contents)

    def test_key_value_rejects_internal_whitespace_controls_and_non_ascii(self):
        for invalid in (DUMMY_KEY + " space", DUMMY_KEY + "\ttab",
                        DUMMY_KEY + "\x00", DUMMY_KEY + "\x7f", DUMMY_KEY + "雪"):
            for contents in (invalid, f'OPENAI_API_KEY="{invalid}"\n'):
                with self.subTest(length=len(contents)):
                    self.assert_invalid(contents)

    def test_empty_multiple_raw_lines_and_malformed_dotenv_fail_safely(self):
        for contents in ("", "\n\t\n", f"{DUMMY_KEY}\nsecond-line\n",
                         f'OPENAI_API_KEY="{DUMMY_KEY}\n',
                         f"OPENAI_API_KEY='{DUMMY_KEY}\"\n",
                         f"OPENAI_API_KEY={DUMMY_KEY}\nnot an assignment\n"):
            with self.subTest(length=len(contents)):
                self.assert_invalid(contents)

    def test_file_limit_and_invalid_utf8_fail_without_content_in_diagnostics(self):
        for contents in ((DUMMY_KEY + "x" * (64 * 1024)).encode("ascii"),
                         DUMMY_KEY.encode("ascii") + b"\xff"):
            self.path.write_bytes(contents)
            output = io.StringIO()
            with redirect_stdout(output), redirect_stderr(output):
                with self.assertRaises(ValueError) as caught:
                    load_api_key(self.path)
            self.assertNotIn(DUMMY_KEY, str(caught.exception))
            self.assertEqual(output.getvalue(), "")

    def test_dotenv_is_literal_and_never_executes_commands(self):
        marker = self.directory / "must-not-exist"
        contents = (f"IGNORED=$(touch {marker})\n"
                    f"ALSO_IGNORED=`touch {marker}`\n"
                    'OPENAI_API_KEY="${SQLP_DUMMY_CREDENTIAL}"\n')
        with patch.dict("os.environ", {"SQLP_DUMMY_CREDENTIAL": DUMMY_KEY}):
            self.assertEqual(load_api_key(self.write(contents)), "${SQLP_DUMMY_CREDENTIAL}")
        self.assertFalse(marker.exists())
        for literal in ("$(true)", "`true`"):
            self.assertEqual(load_api_key(self.write(f'OPENAI_API_KEY="{literal}"\n')), literal)

    def test_rejected_values_have_content_independent_diagnostics(self):
        first = self.assert_invalid(f'OPENAI_API_KEY="{DUMMY_KEY} first"\n')
        second = self.assert_invalid('OPENAI_API_KEY="different-secret second"\n')
        self.assertEqual(first, second)
        self.assertNotIn("different-secret", second)

    def test_missing_path_reports_a_filesystem_error_without_reading_other_files(self):
        with self.assertRaises(OSError):
            load_api_key(self.directory / "not-present")


if __name__ == "__main__":
    unittest.main()
