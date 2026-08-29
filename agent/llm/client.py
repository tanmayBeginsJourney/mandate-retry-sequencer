"""The Z.ai transport. One HTTP call, cached, budgeted, and unable to raise.

WHY THERE IS NO SDK HERE. `urllib` from the standard library, forty lines. The
endpoint is OpenAI-compatible chat completions and the request is one JSON
object; adding a dependency to post it would put a package between this repo and
a reproducible run, and `requirements.txt` currently needs numpy alone for the
gated suite. If that ever stops being true it should be for a better reason
than convenience.

WHAT THIS FILE GUARANTEES TO ITS CALLER:

  * IT NEVER RAISES. `ports.Diagnoser` says a diagnoser may not raise, and an
    LLM failure is an event in the audit log rather than an exception in the
    recovery loop. Timeouts, HTTP errors, malformed JSON, a missing key and a
    blown budget all come back as `LLMResult(ok=False, ...)` with the reason.
  * IT IS CACHED BY `(prompt_id, case_hash)`. Two consequences, and both are
    load-bearing. A re-run costs nothing and returns the identical response, so
    an eval is replayable offline and a number quoted from it is reproducible
    without a key. And a PROMPT CHANGE MISSES THE CACHE, so a prompt edit that
    moves the eval shows up as a diff instead of silently reusing the old
    answers -- which is the whole point of versioning prompts.
  * IT LOGS WHAT IT SPENT. Token counts come from the response's `usage` block,
    never from our own estimate, and the price table is read from
    `PRICES_USD_PER_MTOK` below. A cost we computed ourselves would be a number
    with no source.

THE BUDGET IS A HARD STOP, NOT A WARNING. `Budget.spend` raises nothing; it
refuses, and the refusal becomes a failed `LLMResult` that falls back to the
deterministic diagnoser. A run that quietly kept spending past its cap would be
the same shape as every other guardrail in this repo that reported green while
doing nothing.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

def _load_dotenv() -> None:
    """Read `.env` from the repo root into os.environ, without overwriting.

    A key belongs in a file that is gitignored, not in a shell history and not
    in a source file. `.env` is in `.gitignore`; this reads it once at import so
    that `python agent/eval/run_eval.py --llm` works without the caller having
    to export anything, and an already-set environment variable always wins so
    a deliberate override is never silently ignored.

    NOTHING HERE EVER PRINTS A KEY. The only thing any output says about it is
    whether one was found.
    """
    import agent as _a
    path = os.path.join(_a._PKG_ROOT, ".env")
    if not os.path.exists(path):
        return
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, val = line.partition("=")
                k, val = k.strip(), val.strip().strip("\"'")
                if k and k not in os.environ:
                    os.environ[k] = val
    except OSError:
        pass


DEFAULT_BASE_URL = "https://api.z.ai/api/paas/v4"
DIAGNOSER_MODEL = "glm-5.3-flash"
JUDGE_MODEL = "glm-5.3"

#: USD per million tokens, (input, output).
#:
#: [VERIFIED] 29 August 2026 from https://docs.z.ai/guides/overview/pricing
#: read directly:
#:   glm-5.3-flash  $0.075 in / $0.25 out, cached input $0.015. That is a 50%
#:                  promotional discount off $0.15 / $0.50 / $0.03, running to
#:                  24:00 on 9 September 2026 (UTC+8) -- which covers this
#:                  project's whole window, so the promo price is the one that
#:                  applies and the list price is recorded beside it.
#:   glm-5.3        $1.4 in / $4.4 out, cached input $0.26. Standard pricing;
#:                  no promotion. THE JUDGE IS ~19x THE DIAGNOSER PER INPUT
#:                  TOKEN, which is why it runs once per case and the diagnoser
#:                  runs on everything.
#:
#: This table was `(None, None)` for the judge until the price was actually
#: read. An unpriced call is reported as unpriced rather than estimated --
#: rule 5, a constant with no source is not quoted as if it had one.
PRICES_USD_PER_MTOK = {
    DIAGNOSER_MODEL: (0.075, 0.25),          # [VERIFIED], promo to 9 Sep 2026
    JUDGE_MODEL: (1.4, 4.4),                 # [VERIFIED], standard pricing
}
LIST_PRICES_USD_PER_MTOK = {                 # what it costs after the promo
    DIAGNOSER_MODEL: (0.15, 0.50),
    JUDGE_MODEL: (1.4, 4.4),
}
CACHED_INPUT_USD_PER_MTOK = {DIAGNOSER_MODEL: 0.015, JUDGE_MODEL: 0.26}


@dataclass
class LLMResult:
    ok: bool
    text: str = ""
    parsed: dict | None = None
    model: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    latency_s: float = 0.0
    from_cache: bool = False
    error: str = ""

    def cost_usd(self) -> float | None:
        """None when the SKU's price is unverified. Never a guessed number."""
        pin, pout = PRICES_USD_PER_MTOK.get(self.model, (None, None))
        if pin is None or pout is None:
            return None
        cin = CACHED_INPUT_USD_PER_MTOK.get(self.model, pin)
        fresh = max(self.prompt_tokens - self.cached_tokens, 0)
        return (fresh * pin + self.cached_tokens * cin
                + self.completion_tokens * pout) / 1_000_000


@dataclass
class Budget:
    """A hard stop with a counter behind it. Refuses; does not warn."""
    limit_usd: float = 10.0
    spent_usd: float = 0.0
    calls: int = 0
    unpriced_calls: int = 0          # SKUs whose price we did not verify
    refusals: int = 0

    def allows(self) -> bool:
        return self.spent_usd < self.limit_usd

    def charge(self, r: LLMResult) -> None:
        self.calls += 1
        c = r.cost_usd()
        if c is None:
            self.unpriced_calls += 1
        else:
            self.spent_usd += c

    def asdict(self) -> dict:
        return dict(limit_usd=self.limit_usd,
                    spent_usd=round(self.spent_usd, 6), calls=self.calls,
                    unpriced_calls=self.unpriced_calls, refusals=self.refusals)


class ResponseCache:
    """Keyed by (prompt_id, case_hash). One JSON file, human-readable.

    Deliberately not keyed by the rendered prompt text: `prompt_id` is the
    version and `case_hash` is the input, and if a prompt's TEXT changes
    without its ID changing that is a bug in the caller worth catching loudly
    rather than papering over with a content hash.
    """

    def __init__(self, path: str):
        self.path = path
        self.data: dict = {}
        self.hits = 0
        self.misses = 0
        if os.path.exists(path):
            with open(path, encoding="utf-8") as fh:
                self.data = json.load(fh)

    @staticmethod
    def key(prompt_id: str, case_hash: str, model: str) -> str:
        return f"{model}|{prompt_id}|{case_hash}"

    def get(self, k: str):
        v = self.data.get(k)
        if v is None:
            self.misses += 1
            return None
        self.hits += 1
        return v

    def put(self, k: str, payload: dict) -> None:
        self.data[k] = payload

    def save(self) -> None:
        d = os.path.dirname(os.path.abspath(self.path))
        if d:
            os.makedirs(d, exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self.data, fh, indent=1, sort_keys=True)
        os.replace(tmp, self.path)


class ZaiClient:
    """OpenAI-compatible chat completions against Z.ai. Never raises."""

    def __init__(self, *, model: str = DIAGNOSER_MODEL,
                 api_key: str | None = None, base_url: str | None = None,
                 cache: ResponseCache | None = None,
                 budget: Budget | None = None,
                 temperature: float = 1.0, top_p: float = 0.95,
                 timeout_s: float = 60.0, max_retries: int = 2):
        self.model = model
        if not api_key:
            _load_dotenv()
        self.api_key = api_key or os.environ.get("ZAI_API_KEY", "")
        self.base_url = (base_url or os.environ.get("ZAI_BASE_URL")
                         or DEFAULT_BASE_URL).rstrip("/")
        self.cache = cache
        self.budget = budget or Budget()
        # Vendor-recommended settings for this SKU. [REPORTED], not tuned by
        # us -- and because responses are cached, a reported score is ONE draw
        # per case rather than a mean over draws. Said in the pre-registration.
        self.temperature = temperature
        self.top_p = top_p
        self.timeout_s = timeout_s
        self.max_retries = max_retries

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def complete(self, *, system: str, user: str, prompt_id: str,
                 case_hash: str, schema: dict | None = None) -> LLMResult:
        ck = ResponseCache.key(prompt_id, case_hash, self.model)
        if self.cache is not None:
            hit = self.cache.get(ck)
            if hit is not None:
                return LLMResult(ok=True, text=hit["text"],
                                 parsed=hit.get("parsed"), model=self.model,
                                 prompt_tokens=hit.get("prompt_tokens", 0),
                                 completion_tokens=hit.get("completion_tokens", 0),
                                 cached_tokens=hit.get("cached_tokens", 0),
                                 from_cache=True)

        if not self.api_key:
            return LLMResult(ok=False, model=self.model,
                             error="no ZAI_API_KEY in the environment; the "
                                   "deterministic fallback is the default path "
                                   "and this is a fallback, not a failure")
        if not self.budget.allows():
            self.budget.refusals += 1
            return LLMResult(ok=False, model=self.model,
                             error=f"budget exhausted at "
                                   f"${self.budget.spent_usd:.4f} of "
                                   f"${self.budget.limit_usd:.2f}")

        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "temperature": self.temperature,
            "top_p": self.top_p,
        }
        if schema is not None:
            # Structured output. The model returns an object matching `schema`,
            # so parsing is not a regex over prose. If the SKU rejects the
            # parameter the caller still gets a JSON object because the prompt
            # asks for one; `_parse` handles both.
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "diagnosis", "strict": True,
                                "schema": schema}}

        last = "no attempt made"
        for attempt in range(self.max_retries + 1):
            t0 = time.time()
            try:
                req = urllib.request.Request(
                    f"{self.base_url}/chat/completions",
                    data=json.dumps(body).encode("utf-8"),
                    headers={"Content-Type": "application/json",
                             "Authorization": f"Bearer {self.api_key}"},
                    method="POST")
                with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                    raw = json.loads(resp.read().decode("utf-8"))
                text = raw["choices"][0]["message"]["content"]
                usage = raw.get("usage", {}) or {}
                det = usage.get("prompt_tokens_details", {}) or {}
                r = LLMResult(
                    ok=True, text=text, parsed=_parse(text), model=self.model,
                    prompt_tokens=int(usage.get("prompt_tokens", 0)),
                    completion_tokens=int(usage.get("completion_tokens", 0)),
                    cached_tokens=int(det.get("cached_tokens", 0)),
                    latency_s=round(time.time() - t0, 3))
                self.budget.charge(r)
                if self.cache is not None:
                    self.cache.put(ck, dict(
                        text=r.text, parsed=r.parsed,
                        prompt_tokens=r.prompt_tokens,
                        completion_tokens=r.completion_tokens,
                        cached_tokens=r.cached_tokens))
                return r
            except urllib.error.HTTPError as e:
                detail = ""
                try:
                    detail = e.read().decode("utf-8")[:300]
                except Exception:
                    pass
                last = f"HTTP {e.code}: {detail}"
                # 4xx other than rate-limiting will not fix themselves.
                if e.code not in (408, 429) and e.code < 500:
                    break
            except Exception as e:                      # noqa: BLE001
                last = f"{type(e).__name__}: {e}"
            if attempt < self.max_retries:
                time.sleep(1.5 * (attempt + 1))
        return LLMResult(ok=False, model=self.model, error=last)


def _parse(text: str) -> dict | None:
    """Best-effort JSON out of a model response. Returns None, never raises.

    Tries the whole string, then the outermost brace-delimited span, because a
    model asked for JSON sometimes wraps it in a fence or a sentence. A parse
    failure is a FAILED diagnosis that falls back to the deterministic path --
    it is never a partially-parsed one, because a half-read intervention is
    worse than none.
    """
    for candidate in (text, _braces(text)):
        if not candidate:
            continue
        try:
            v = json.loads(candidate)
            return v if isinstance(v, dict) else None
        except Exception:                               # noqa: BLE001
            continue
    return None


def _braces(text: str) -> str:
    i, j = text.find("{"), text.rfind("}")
    return text[i:j + 1] if 0 <= i < j else ""


def case_key(prompt_id: str, payload: dict) -> str:
    """Stable hash for anything that is not a CaseView (the judge's input)."""
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode()
    ).hexdigest()[:16]
