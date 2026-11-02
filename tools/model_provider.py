"""Small optional Responses HTTP adapter; importing it never calls a model."""

import http.client
import json
import math
import urllib.error
import urllib.request


ENDPOINT = "https://api.openai.com/v1/responses"
MAX_RESPONSE_BYTES = 8 * 1024 * 1024


class ProviderError(RuntimeError):
    """A safe diagnostic, optionally retaining the received response for a journal."""

    def __init__(self, message, raw_response=None, request_id=None):
        super().__init__(message)
        self.raw_response = raw_response
        self.request_id = request_id


def parse_response(raw):
    """Normalize visible text/status and reported usage without repairing output.

    Cached input and reasoning output are subsets of the main usage counts.
    Partial usage remains unknown; known malformed counts are protocol errors.
    Only ``completed`` responses are candidates for subsequent query execution.
    """
    def invalid(message):
        raise ProviderError(message, raw_response=raw)

    if not isinstance(raw, dict):
        invalid("Provider response must be an object")
    model, request_id = raw.get("model"), raw.get("id")
    if not isinstance(model, str) or not model.strip():
        invalid("Provider response needs a model identifier")
    if request_id is not None and (not isinstance(request_id, str) or not request_id.strip()):
        invalid("Provider response has an invalid identifier")
    try:
        model.encode("utf-8")
        if request_id is not None:
            request_id.encode("utf-8")
    except UnicodeEncodeError:
        invalid("Provider identifiers must contain valid Unicode text")
    root_status = raw.get("status")
    if root_status not in ("completed", "incomplete", "failed", "cancelled"):
        invalid("Provider response has an unsupported terminal status")

    usage = raw.get("usage")
    normalized_usage = None
    if usage is not None:
        if not isinstance(usage, dict):
            invalid("Provider usage must be an object or null")
        fields = ("input_tokens", "output_tokens", "total_tokens")
        for field in fields:
            if field in usage and (type(usage[field]) is not int or usage[field] < 0):
                invalid("Provider token counts must be nonnegative integers")
        if all(field in usage for field in fields):
            if usage["total_tokens"] != usage["input_tokens"] + usage["output_tokens"]:
                invalid("Provider token counts are inconsistent")
            normalized_usage = {field: usage[field] for field in fields}

    output = raw.get("output", [])
    if not isinstance(output, list):
        invalid("Provider output must be an array")
    text, refused, incomplete = [], False, root_status == "incomplete"
    for item in output:
        if not isinstance(item, dict):
            invalid("Provider output entries must be objects")
        if item.get("type") == "reasoning":
            continue
        if item.get("type") != "message" or item.get("role") != "assistant":
            invalid("Provider returned an unsupported output item")
        if "status" in item:
            if item["status"] not in ("completed", "incomplete", "in_progress"):
                invalid("Provider message has an unsupported status")
            incomplete = incomplete or item["status"] != "completed"
        if not isinstance(item.get("content"), list):
            invalid("Provider message content must be an array")
        for part in item["content"]:
            if not isinstance(part, dict):
                invalid("Provider content entries must be objects")
            if part.get("type") == "output_text" and isinstance(part.get("text"), str):
                text.append(part["text"])
            elif part.get("type") == "refusal" and isinstance(part.get("refusal"), str):
                refused = True
            else:
                invalid("Provider returned unsupported message content")
    raw_text = "".join(text)
    try:
        raw_text.encode("utf-8")
    except UnicodeEncodeError:
        invalid("Provider output must contain valid Unicode text")
    if root_status in ("failed", "cancelled"):
        status = "failed"
    elif incomplete:
        status = "incomplete"
    elif refused:
        status = "refused"
    else:
        status = "completed" if raw_text.strip() else "empty"
    return {"raw_text": raw_text, "status": status, "model": model,
            "request_id": request_id, "usage": normalized_usage, "raw_response": raw}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        return None


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON field")
        result[key] = value
    return result


def _reject_constant(value):
    raise ValueError("Nonfinite JSON number")


def _finite_float(value):
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("Nonfinite JSON number")
    return number


class OpenAIResponsesClient:
    """One synchronous stateless request, without redirects or automatic retries.

    The caller supplies a key explicitly and decides whether to make paid calls.
    The injectable opener supports fully offline tests. No environment is read
    and no connection is opened during construction.
    """

    def __init__(self, api_key, timeout_seconds=60, *, opener=None):
        if (not isinstance(api_key, str) or not api_key or
                any(ord(char) <= 32 or ord(char) >= 127 for char in api_key)):
            raise ValueError("API key must be nonempty printable ASCII without whitespace")
        if (type(timeout_seconds) not in (int, float) or
                not math.isfinite(timeout_seconds) or timeout_seconds <= 0):
            raise ValueError("Provider timeout must be finite and positive")
        self._api_key, self.timeout_seconds = api_key, timeout_seconds
        self._opener = opener

    def __call__(self, payload):
        if (not isinstance(payload, dict) or payload.get("store") is not False or
                "previous_response_id" in payload or "conversation" in payload or
                payload.get("stream", False) is not False or
                payload.get("background", False) is not False or
                payload.get("tools", []) != []):
            raise ProviderError("Provider requests must be synchronous, stateless, and tool-free")
        try:
            body = json.dumps(payload, allow_nan=False).encode("utf-8")
        except (TypeError, ValueError, UnicodeError, RecursionError):
            raise ProviderError("Provider request is not valid JSON") from None
        request = urllib.request.Request(ENDPOINT, data=body, method="POST", headers={
            "Authorization": "Bearer " + self._api_key,
            "Content-Type": "application/json", "Accept": "application/json"})
        opener = self._opener or urllib.request.build_opener(_NoRedirect())
        try:
            with opener.open(request, timeout=self.timeout_seconds) as response:
                encoded = response.read(MAX_RESPONSE_BYTES + 1)
            if len(encoded) > MAX_RESPONSE_BYTES:
                raise ProviderError("Provider response exceeded the size limit")
            raw = json.loads(encoded.decode("utf-8"), object_pairs_hook=_unique_object,
                             parse_constant=_reject_constant, parse_float=_finite_float)
            if not isinstance(raw, dict):
                raise ValueError("Response must be an object")
            return raw
        except urllib.error.HTTPError as error:
            code = error.code if type(error.code) is int else "unknown"
            try:
                error.close()
            except (OSError, http.client.HTTPException):
                pass  # Preserve the safe HTTP diagnostic if cleanup also fails.
            raise ProviderError("Provider request failed (HTTP " + str(code) + ")") from None
        except (OSError, urllib.error.URLError, http.client.HTTPException):
            raise ProviderError("Provider transport failed") from None
        except (ValueError, UnicodeError, RecursionError):
            raise ProviderError("Provider returned invalid JSON") from None
