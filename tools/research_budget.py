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

# Dollars per million tokens, Standard short-context, checked November 16, 2026.
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


def _default_text_cache_request(payload, model):
    """Prove the submitted stateless text request uses the documented 30m TTL."""
    if (not isinstance(payload, dict) or payload.get("model") != model
            or payload.get("store") is not False or payload.get("tools") != []
            or not isinstance(payload.get("instructions", ""), str)
            or any(key in payload for key in ("prompt_cache_options", "prompt_cache_breakpoint",
                                               "previous_response_id", "conversation"))):
        return False
    messages = payload.get("input")
    return isinstance(messages, list) and all(
        isinstance(message, dict) and not set(message) - {"role", "content"}
        and isinstance(message.get("content"), str)
        for message in messages)


def settled_charge_upper(raw, model, *, payload=None):
    """Bound settled text usage while honoring explicitly reported cache reads.

    Exact text-only requests without cache options/breakpoints use the documented
    default 30m write rate and explicit ordinary/read/write token counts. Without
    that request proof, noncached input retains the highest published write rate.
    Known cached reads use their separate rate in either case. The returned tier
    and a 10% regional margin remain included. Missing, malformed, or mismatched
    accounting cannot release a reservation.
    """
    if model not in RATES or not isinstance(raw, dict) or raw.get("model") != model:
        return None
    if raw.get("status") != "completed" or estimate_charge(raw, model) is None:
        return None
    usage = raw["usage"]
    total = usage.get("total_tokens")
    if type(total) is not int or total != usage["input_tokens"] + usage["output_tokens"]:
        return None
    cached = usage["input_tokens_details"].get("cached_tokens")
    if type(cached) is not int or not 0 <= cached <= usage["input_tokens"]:
        return None
    rates = list(map(Decimal, RATES[model]))
    writes = usage["input_tokens_details"].get("cache_write_tokens")
    if _default_text_cache_request(payload, model) and type(writes) is int:
        amount = ((usage["input_tokens"] - cached - writes) * rates[0]
                  + cached * rates[1] + writes * rates[2] + usage["output_tokens"] * rates[4])
    else:
        amount = ((usage["input_tokens"] - cached) * rates[3]
                  + cached * rates[1] + usage["output_tokens"] * rates[4])
    return _ceil(amount * Decimal(TIERS[raw["service_tier"]]) * Decimal("1.1"))


def reconcile_completed_reservations(directory, *, apply=False, expected_ledger_sha256=None):
    """Audit or explicitly reduce completed reservations; never reset a budget.

    Dry-run does not change ledger bytes. Apply requires its exact reviewed hash,
    preserves an owner-only original backup and per-call audit, then replaces the
    ledger atomically under the same advisory lock used by paid requests.
    """
    directory = Path(directory)
    lock_fd = os.open(directory / "budget.lock", os.O_RDWR | os.O_CREAT, 0o600)
    with os.fdopen(lock_fd, "r+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        path = directory / "budget.json"
        original = path.read_bytes()
        original_sha = hashlib.sha256(original).hexdigest()
        if apply and (not isinstance(expected_ledger_sha256, str)
                      or original_sha != expected_ledger_sha256):
            raise ValueError("Apply requires the exact reviewed ledger hash")
        state = json.loads(original)
        changes, skipped = [], []
        for position, call in enumerate(state["calls"]):
            previous = call["upper_micro_usd"]
            if type(previous) is not int or previous < 0 or call.get("index") != position:
                raise ValueError("Malformed reservation index or amount")
            filename = call.get("response_file")
            if call.get("status") != "completed" or not isinstance(filename, str):
                skipped.append({"index": position, "reason": "not_completed_with_retained_response"})
                continue
            response_path = directory / filename
            if response_path.parent.resolve() != directory.resolve():
                raise ValueError("Response file escapes ledger directory")
            response_bytes = response_path.read_bytes()
            raw = json.loads(response_bytes)
            model = call.get("model")
            upper = settled_charge_upper(raw, model)
            if upper is None:
                skipped.append({"index": position, "reason": "incomplete_or_mismatched_accounting"})
                continue
            estimate = estimate_charge(raw, model)
            if estimate != call.get("estimated_micro_usd") or upper < estimate:
                raise ValueError("Retained usage and recorded charge estimate disagree")
            changes.append({"index": position, "model": model, "response_file": filename,
                            "response_sha256": hashlib.sha256(response_bytes).hexdigest(),
                            "old_upper_micro_usd": previous,
                            "new_upper_micro_usd": min(previous, upper),
                            "reported_cached_tokens": raw["usage"]["input_tokens_details"]["cached_tokens"]})
        before = state["prior_upper_micro_usd"] + sum(c["upper_micro_usd"] for c in state["calls"])
        after = before - sum(c["old_upper_micro_usd"] - c["new_upper_micro_usd"] for c in changes)
        audit = {"version": 1, "kind": "completed_reservation_reconciliation",
                 "policy": "verified_cached_reads_v1", "applied": bool(apply),
                 "ledger_before_sha256": original_sha,
                 "authorized_micro_usd": state["authorized_micro_usd"],
                 "prior_upper_micro_usd": state["prior_upper_micro_usd"],
                 "before_upper_micro_usd": before, "after_upper_micro_usd": after,
                 "released_micro_usd": before - after,
                 "remaining_micro_usd": state["authorized_micro_usd"] - after,
                 "pricing_source": "https://developers.openai.com/api/docs/pricing",
                 "pricing_checked": "2026-11-16", "rates": RATES,
                 "settlement_rule": "explicit cached reads at read rate; all noncached input at highest write rate; known tier; 10% margin",
                 "changes": changes, "skipped": skipped}
        if not apply:
            return audit
        name = "reconciliation-" + original_sha[:20]
        backup = directory / (name + ".before.json")
        audit_path = directory / (name + ".audit.json")
        if backup.exists() or audit_path.exists():
            raise ValueError("Reconciliation artifacts already exist; inspect before retry")
        for item in changes:
            call = state["calls"][item["index"]]
            call["upper_micro_usd"] = item["new_upper_micro_usd"]
        state.setdefault("reservation_reconciliations", []).append({
            "policy": audit["policy"], "ledger_before_sha256": original_sha,
            "audit_file": audit_path.name, "backup_file": backup.name,
            "released_micro_usd": before - after, "applied_at": time.time()})
        replacement = (json.dumps(state, allow_nan=False, indent=2) + "\n").encode()
        audit["ledger_after_sha256"] = hashlib.sha256(replacement).hexdigest()
        for destination, value in ((backup, original),
                                   (audit_path, (json.dumps(audit, allow_nan=False, indent=2) + "\n").encode())):
            descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(value)
                stream.flush()
                os.fsync(stream.fileno())
        pending = directory / "budget.pending"
        descriptor = os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(replacement)
            stream.flush()
            os.fsync(stream.fileno())
        pending.replace(path)
        return audit


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
                upper = settled_charge_upper(raw, model, payload=payload)
                if upper is None:
                    factor = Decimal(TIERS.get(raw.get("service_tier"), "6"))*Decimal("1.1")
                    upper = _ceil((usage["input_tokens"]*rates[3]+usage["output_tokens"]*rates[4])*factor)
                    policy = "uncertain_usage_longest_write_v1"
                elif (_default_text_cache_request(payload, model)
                      and type(usage["input_tokens_details"].get("cache_write_tokens")) is int):
                    policy = "verified_text_request_default_30m_v2"
                else:
                    policy = "verified_cached_reads_v1"
                call.update(upper_micro_usd=upper, settlement_policy=policy)
            if not model_matches:
                call["status"] = "returned_model_mismatch"
        if not model_matches:
            raise ProviderError("Returned model differs from budgeted model", raw_response=raw)
        return raw
