"""Durable local spending reservations for explicitly authorized experiments.

This estimates charges from published rates; it does not query a billing account.
There are no credentials or network operations in this module. Supply a client.
"""

from contextlib import contextmanager
from decimal import Decimal, ROUND_CEILING
import fcntl
import hashlib
import json
import os
from pathlib import Path
import time

from .model_provider import ProviderError

# Dollars per million tokens, Standard short-context, checked November 6, 2026.
# A write is charged instead of ordinary input, never as an additional charge.
RATES = {
    "gpt-6.1-sol": ("2", "0.1", "2.5", "5", "10"),
    "gpt-6-luna": ("0.1", "0.01", "0.125", "0.25", "0.5"),
    "gpt-6-astra": ("10", "1", "12.5", "25", "50"),
}
TIERS = {"default": "1", "fast": "2", "priority": "2", "flex": "0.5", "batch": "0.5", "ultrafast": "6"}
MAX_REQUEST_BYTES = 256 * 1024
MAX_OUTPUT_TOKENS = 32768


def _ceil(value):
    return int(value.to_integral_value(rounding=ROUND_CEILING))


def estimate_charge(raw, model):
    """Return integer microdollars, or None when reported accounting is unknown."""
    usage = raw.get("usage") if isinstance(raw, dict) else None
    if not isinstance(usage, dict) or raw.get("service_tier") not in TIERS:
        return None
    if any(type(usage.get(k)) is not int or usage[k] < 0 for k in ("input_tokens", "output_tokens")):
        return None
    details = usage.get("input_tokens_details")
    if not isinstance(details, dict):
        return None
    cached, writes = details.get("cached_tokens", 0), details.get("cache_write_tokens", 0)
    if any(type(n) is not int or n < 0 for n in (cached, writes)) or cached + writes > usage["input_tokens"]:
        return None
    ordinary, read, write30, _, output = map(Decimal, RATES[model])
    # Our experiment requests set no cache options/breakpoints: documented 30m default.
    tokens = ((usage["input_tokens"]-cached-writes)*ordinary + cached*read + writes*write30
              + usage["output_tokens"]*output)
    return _ceil(tokens*Decimal(TIERS[raw["service_tier"]]))


class BudgetedProvider:
    """Reserve before each request, retain raw responses, settle conservatively.

    Separate processes share an advisory lock. Unknown response usage or transport
    failures retain the full reservation; there are no automatic retries.
    """

    def __init__(self, client, directory, *, authorized_usd=100, prior_upper_usd=1, label="experiment"):
        self.client, self.directory, self.label = client, Path(directory), label
        if not callable(client) or not isinstance(label, str) or not label:
            raise ValueError("Supply a provider and experiment label")
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.limit = _ceil(Decimal(str(authorized_usd))*1_000_000)
        self.prior = _ceil(Decimal(str(prior_upper_usd))*1_000_000)
        if not 0 <= self.prior < self.limit:
            raise ValueError("Budget must exceed prior spending")
        with self._locked() as state:
            if state:
                if state.get("authorized_micro_usd") != self.limit or state.get("prior_upper_micro_usd") != self.prior:
                    raise ValueError("Existing ledger budget differs; do not silently reset spending")
            else:
                state.update(version=1, authorized_micro_usd=self.limit, prior_upper_micro_usd=self.prior,
                             pricing_source="https://developers.openai.com/api/docs/pricing", calls=[])

    @contextmanager
    def _locked(self):
        descriptor = os.open(self.directory/"budget.lock", os.O_RDWR | os.O_CREAT, 0o600)
        with os.fdopen(descriptor, "r+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            path = self.directory/"budget.json"
            state = json.loads(path.read_text()) if path.exists() else {}
            yield state
            temporary = self.directory/"budget.pending"
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as destination:
                json.dump(state, destination, allow_nan=False, indent=2)
                destination.flush()
                os.fsync(destination.fileno())
            temporary.replace(path)

    def __call__(self, payload):
        model = payload.get("model") if isinstance(payload, dict) else None
        maximum = payload.get("max_output_tokens") if isinstance(payload, dict) else None
        if model not in RATES or type(maximum) is not int or not 0 < maximum <= MAX_OUTPUT_TOKENS:
            raise ProviderError("Request model/output cap is outside the budgeted profile")
        if (payload.get("store") is not False or payload.get("tools") != []
                or "previous_response_id" in payload or "conversation" in payload
                or payload.get("stream", False) is not False
                or payload.get("background", False) is not False):
            raise ProviderError("Budgeted requests must be explicitly stateless and tool-free")
        messages = payload.get("input")
        if (not isinstance(messages, list) or not isinstance(payload.get("instructions", ""), str)
                or any(not isinstance(message, dict) or not isinstance(message.get("content"), str)
                       for message in messages)):
            raise ProviderError("Budgeted profile accepts text messages only")
        encoded = json.dumps(payload, sort_keys=True, ensure_ascii=True, allow_nan=False).encode()
        if len(encoded) > MAX_REQUEST_BYTES:
            raise ProviderError("Request exceeds budgeted input size")
        rates = list(map(Decimal, RATES[model]))
        # UTF-8/JSON bytes + ample framing reserve bounds this text-only profile.
        # Highest published tier, longest write TTL, and a 10% regional margin.
        reservation = _ceil(((len(encoded)+8192)*rates[3] + maximum*rates[4])*Decimal("6.6"))
        with self._locked() as state:
            used = state["prior_upper_micro_usd"] + sum(call["upper_micro_usd"] for call in state["calls"])
            if used + reservation > state["authorized_micro_usd"]:
                raise ProviderError("Authorized spending reservation exhausted")
            index = len(state["calls"])
            state["calls"].append({"index": index, "label": self.label, "model": model,
                "request_sha256": hashlib.sha256(json.dumps(payload, sort_keys=True,
                    separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode()).hexdigest(),
                "started_at": time.time(),
                "upper_micro_usd": reservation, "status": "sending"})
        try:
            raw = self.client(payload)
        except ProviderError:
            with self._locked() as state:
                state["calls"][index]["status"] = "transport_or_http_error_usage_unknown"
            raise
        response_path = self.directory/("response-%05d.json" % index)
        fd = os.open(response_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as destination:
            json.dump(raw, destination, allow_nan=False)
            destination.flush()
            os.fsync(destination.fileno())
        model_matches = raw.get("model") == model
        estimate = estimate_charge(raw, model) if model_matches else None
        with self._locked() as state:
            call = state["calls"][index]
            call.update(status=raw.get("status"), estimated_micro_usd=estimate,
                        response_file=response_path.name, completed_at=time.time())
            usage = raw.get("usage")
            if (model_matches and isinstance(usage, dict) and all(type(usage.get(k)) is int and usage[k] >= 0
                                              for k in ("input_tokens", "output_tokens"))):
                factor = Decimal(TIERS.get(raw.get("service_tier"), "6"))*Decimal("1.1")
                upper = _ceil((usage["input_tokens"]*rates[3]+usage["output_tokens"]*rates[4])*factor)
                call["upper_micro_usd"] = upper
            if not model_matches:
                call["status"] = "returned_model_mismatch"
        if not model_matches:
            raise ProviderError("Returned model differs from budgeted model", raw_response=raw)
        return raw
