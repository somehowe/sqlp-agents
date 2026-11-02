"""Offline protocol tests. Every HTTP interaction uses a fake opener."""

from copy import deepcopy
import http.client
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch
import urllib.error

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.model_provider import (ENDPOINT, MAX_RESPONSE_BYTES, OpenAIResponsesClient,
                                  ProviderError, _NoRedirect, parse_response)


def response(text="SELECT 1;"):
    return {"model": "configured-model", "id": "resp_fixture", "status": "completed",
            "output": [{"type": "message", "role": "assistant", "status": "completed",
                        "content": [{"type": "output_text", "text": text}]}],
            "usage": {"input_tokens": 20, "output_tokens": 7, "total_tokens": 27,
                      "input_tokens_details": {"cached_tokens": 12},
                      "output_tokens_details": {"reasoning_tokens": 4}}}


class ParseResponseTests(unittest.TestCase):
    def test_preserves_text_raw_and_overlapping_token_details(self):
        raw = response("\n```sql\nSELECT 1;\n```\n")
        raw["output"].insert(0, {"type": "reasoning", "summary": []})
        before = deepcopy(raw)
        result = parse_response(raw)
        self.assertEqual(result["raw_text"], "\n```sql\nSELECT 1;\n```\n")
        self.assertEqual(result["usage"], {"input_tokens": 20, "output_tokens": 7,
                                           "total_tokens": 27})
        self.assertEqual(result["request_id"], "resp_fixture")
        self.assertIs(result["raw_response"], raw)
        self.assertEqual(raw, before)

    def test_visible_chunks_are_concatenated_without_added_characters(self):
        raw = response("SELECT ")
        raw["output"][0]["content"].append({"type": "output_text", "text": "1;"})
        self.assertEqual(parse_response(raw)["raw_text"], "SELECT 1;")

    def test_unpaired_surrogates_are_protocol_errors_with_raw_response_preserved(self):
        for field in ("text", "model", "id"):
            for surrogate in ("\ud800", "\udfff"):
                raw = response()
                if field == "text":
                    raw["output"][0]["content"][0]["text"] = "SELECT '" + surrogate + "';"
                else:
                    raw[field] += surrogate
                with self.subTest(field=field, surrogate=repr(surrogate)):
                    with self.assertRaises(ProviderError) as caught:
                        parse_response(raw)
                    self.assertIs(caught.exception.raw_response, raw)
                    self.assertNotIn(surrogate, str(caught.exception))

    def test_valid_non_ascii_output_and_identifiers_are_preserved(self):
        raw = response("SELECT '雪🙂';")
        raw.update(model="model-雪", id="resp-ä")
        parsed = parse_response(raw)
        self.assertEqual(parsed["status"], "completed")
        self.assertEqual(parsed["raw_text"], "SELECT '雪🙂';")
        self.assertEqual((parsed["model"], parsed["request_id"]), ("model-雪", "resp-ä"))

    def test_missing_and_partial_usage_are_unknown(self):
        for usage in (None, {}, {"input_tokens": 0}, {"output_tokens": 2, "total_tokens": 2}):
            with self.subTest(usage=usage):
                raw = response()
                raw["usage"] = usage
                self.assertIsNone(parse_response(raw)["usage"])
        raw.pop("usage")
        self.assertIsNone(parse_response(raw)["usage"])

    def test_malformed_counts_and_inconsistent_total_are_errors(self):
        for usage in ([], {"input_tokens": True}, {"output_tokens": -1},
                      {"total_tokens": "27"}, {"input_tokens": 1.0},
                      {"input_tokens": 1, "output_tokens": 2, "total_tokens": 4}):
            with self.subTest(usage=usage):
                raw = response()
                raw["usage"] = usage
                with self.assertRaises(ProviderError) as caught:
                    parse_response(raw)
                self.assertIs(caught.exception.raw_response, raw)

    def test_statuses_never_promote_partial_or_refused_text_to_completed(self):
        for root_status, expected in (("incomplete", "incomplete"), ("failed", "failed"),
                                      ("cancelled", "failed"), ("completed", "refused")):
            raw = response()
            raw["status"] = root_status
            raw["output"][0]["content"].append({"type": "refusal", "refusal": "Refused"})
            self.assertEqual(parse_response(raw)["status"], expected)
        for text in ("", " \n"):
            self.assertEqual(parse_response(response(text))["status"], "empty")
        raw = response()
        raw["output"][0]["status"] = "incomplete"
        self.assertEqual(parse_response(raw)["status"], "incomplete")

    def test_malformed_protocol_shapes_are_errors(self):
        variants = [{}, {"model": ""}, {"id": 9}, {"status": "in_progress"},
                    {"output": None}, {"output": [None]},
                    {"output": [{"type": "function_call"}]},
                    {"output": [{"type": "message", "role": "assistant", "content": [7]}]},
                    {"output": [{"type": "message", "role": "assistant", "content": [
                        {"type": "output_text", "text": False}]}]}]
        with self.assertRaises(ProviderError):
            parse_response([])
        for patch_fields in variants:
            raw = response()
            raw.update(patch_fields)
            if patch_fields == {}:
                raw.pop("model")
            with self.subTest(patch_fields=patch_fields), self.assertRaises(ProviderError):
                parse_response(raw)


class ClientTests(unittest.TestCase):
    def client(self, encoded=None):
        opener = Mock()
        opener.open.return_value = io.BytesIO(encoded if encoded is not None else b'{"ok":true}')
        return OpenAIResponsesClient("offline-secret", opener=opener), opener

    def test_constructor_has_no_network_or_environment_side_effects(self):
        with patch("urllib.request.build_opener") as build:
            OpenAIResponsesClient("offline-secret")
        build.assert_not_called()
        for key, timeout in (("", 60), ("bad\nkey", 60), ("key", True),
                             ("key", 0), ("key", float("inf"))):
            with self.assertRaises(ValueError):
                OpenAIResponsesClient(key, timeout)

    def test_fixed_endpoint_one_request_and_unchanged_stateless_payload(self):
        client, opener = self.client()
        payload = {"model": "configured-model", "input": "question", "store": False}
        before = deepcopy(payload)
        self.assertEqual(client(payload), {"ok": True})
        opener.open.assert_called_once()
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, ENDPOINT)
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(json.loads(request.data), payload)
        self.assertEqual(payload, before)
        self.assertEqual(opener.open.call_args.kwargs["timeout"], 60)
        self.assertIsNone(_NoRedirect().redirect_request(None, None, 302, "", {}, "https://elsewhere"))

    def test_stateful_and_streaming_requests_rejected_before_network(self):
        for extra in ({"store": True}, {"previous_response_id": None}, {"conversation": {}},
                      {"stream": True}, {"background": True}, {"tools": [{"type": "web_search"}]}):
            client, opener = self.client()
            with self.subTest(extra=extra), self.assertRaises(ProviderError):
                client({"store": False, **extra})
            opener.open.assert_not_called()

    def test_transport_and_http_errors_hide_secret_and_never_retry(self):
        for error in (urllib.error.URLError("offline-secret"), OSError("offline-secret"),
                      http.client.IncompleteRead(b"offline-secret"),
                      urllib.error.HTTPError(ENDPOINT, 429, "offline-secret", {}, None)):
            client, opener = self.client()
            opener.open.side_effect = error
            with self.assertRaises(ProviderError) as caught:
                client({"store": False})
            self.assertNotIn("offline-secret", str(caught.exception))
            opener.open.assert_called_once()

    def test_http_error_response_body_is_closed_without_reading_it(self):
        body = io.BytesIO(b"offline-secret response body")
        client, opener = self.client()
        opener.open.side_effect = urllib.error.HTTPError(ENDPOINT, 429, "offline-secret", {}, body)
        with self.assertRaises(ProviderError) as caught:
            client({"store": False})
        self.assertTrue(body.closed)
        self.assertEqual(str(caught.exception), "Provider request failed (HTTP 429)")
        opener.open.assert_called_once()

    def test_invalid_or_oversize_json_is_rejected(self):
        for encoded in (b"[]", b"\xff", b'{"x":NaN}', b'{"x":1e1000}', b'{"x":1,"x":2}',
                        b"{" * 1100, b" " * (MAX_RESPONSE_BYTES + 1)):
            client, opener = self.client(encoded)
            with self.subTest(length=len(encoded)), self.assertRaises(ProviderError):
                client({"store": False})
        client, opener = self.client()
        with self.assertRaises(ProviderError):
            client({"store": False, "temperature": float("nan")})
        opener.open.assert_not_called()


if __name__ == "__main__":
    unittest.main()
