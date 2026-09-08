"""Token accounting for a run.

The service has been run dozens of times without anyone being able to say what
a run cost. Working that out by hand from a log is possible — it is how the
"86% of calls were page-offset detection" finding was made — but it should not
require grepping.

Usage is grouped by purpose as well as by model, because the interesting
question is rarely "how many tokens" but "which part of the pipeline is
spending them".

Prices are deliberately not hardcoded. They change, they differ per account,
and a wrong number is worse than none: it would be quoted in a report. Put
real figures in app/config/model_prices.json and a cost appears; leave it and
the summary reports tokens only.
"""

import json
import logging
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

PURPOSE_EXTRACTION = "nyckeltalsextraktion"
PURPOSE_SECOND_PASS = "riktad omsökning"
PURPOSE_STABILITY = "stabilitetskontroll"
PURPOSE_YEAR = "årtolkning"
PURPOSE_PAGE_OFFSET = "sidnummeroffset"
PURPOSE_OTHER = "övrigt"

PRICES_FILENAME = "model_prices.json"


@dataclass
class Bucket:
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def add(self, prompt: int, completion: int, cached: int = 0) -> None:
        self.calls += 1
        self.prompt_tokens += prompt
        self.completion_tokens += completion
        self.cached_tokens += cached


@dataclass
class UsageTracker:
    """Thread-safe, because chunks are analysed concurrently."""

    by_model: dict = field(default_factory=dict)
    by_purpose: dict = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def record(self, model: str, purpose: str, usage) -> None:
        if usage is None:
            return
        prompt = getattr(usage, "prompt_tokens", 0) or 0
        completion = getattr(usage, "completion_tokens", 0) or 0

        cached = 0
        details = getattr(usage, "prompt_tokens_details", None)
        if details is not None:
            cached = getattr(details, "cached_tokens", 0) or 0

        with self._lock:
            self.by_model.setdefault(model or "okänd", Bucket()).add(prompt, completion, cached)
            self.by_purpose.setdefault(purpose or PURPOSE_OTHER, Bucket()).add(
                prompt, completion, cached
            )

    # ------------------------------------------------------------------
    @property
    def calls(self) -> int:
        return sum(b.calls for b in self.by_model.values())

    @property
    def total_tokens(self) -> int:
        return sum(b.total_tokens for b in self.by_model.values())

    @property
    def cached_tokens(self) -> int:
        return sum(b.cached_tokens for b in self.by_model.values())

    def cost(self, prices: dict) -> float | None:
        """Total cost, or None when any model used has no price configured.

        Partial pricing would understate the total, which is the one direction
        a cost estimate must not err in.
        """
        if not prices:
            return None
        total = 0.0
        for model, bucket in self.by_model.items():
            entry = prices.get(model)
            if not entry:
                return None
            total += bucket.prompt_tokens / 1_000_000 * float(entry.get("in", 0))
            total += bucket.completion_tokens / 1_000_000 * float(entry.get("ut", 0))
        return total

    def as_dict(self, prices: dict = None) -> dict:
        prices = prices or {}
        payload = {
            "anrop": self.calls,
            "tokens_totalt": self.total_tokens,
            "tokens_cachade": self.cached_tokens,
            "per_modell": {
                model: {
                    "anrop": b.calls,
                    "prompt": b.prompt_tokens,
                    "svar": b.completion_tokens,
                }
                for model, b in sorted(self.by_model.items())
            },
            "per_ändamål": {
                purpose: {"anrop": b.calls, "tokens": b.total_tokens}
                for purpose, b in sorted(
                    self.by_purpose.items(), key=lambda kv: -kv[1].total_tokens
                )
            },
        }
        cost = self.cost(prices)
        if cost is not None:
            payload["kostnad"] = round(cost, 4)
            payload["valuta"] = prices.get("_valuta", "USD")
        return payload


def load_prices(config_dir: Path = None) -> dict:
    """Read model prices, if anyone has configured any."""
    override = os.getenv("JBG_MODEL_PRICES")
    path = Path(override) if override else (
        (config_dir or Path(__file__).resolve().parents[1] / "config") / PRICES_FILENAME
    )
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as ex:
        logger.warning(f"Kunde inte läsa prislistan {path}: {ex}")
        return {}
    return {k: v for k, v in data.items() if not k.startswith("_")} | (
        {"_valuta": data.get("_valuta", "USD")} if "_valuta" in data else {}
    )


def log_summary(tracker: UsageTracker, files_analysed: int, prices: dict = None) -> None:
    """One block at the end of a run, so the cost of a change is visible."""
    if not tracker.calls:
        return
    prices = prices if prices is not None else load_prices()

    logger.info(
        f"Modellanvändning: {tracker.calls} anrop, "
        f"{tracker.total_tokens:,} tokens".replace(",", " ")
    )
    for model, b in sorted(tracker.by_model.items()):
        logger.info(
            f"  {model}: {b.calls} anrop, {b.prompt_tokens:,} prompt + "
            f"{b.completion_tokens:,} svar".replace(",", " ")
        )
    for purpose, b in sorted(tracker.by_purpose.items(), key=lambda kv: -kv[1].total_tokens):
        share = b.total_tokens / tracker.total_tokens * 100 if tracker.total_tokens else 0
        logger.info(
            f"  {purpose}: {b.calls} anrop, {b.total_tokens:,} tokens "
            f"({share:.0f}%)".replace(",", " ")
        )
    if files_analysed:
        logger.info(
            f"  per fil: {tracker.calls / files_analysed:.1f} anrop, "
            f"{tracker.total_tokens // files_analysed:,} tokens".replace(",", " ")
        )
    if tracker.cached_tokens:
        logger.info(f"  varav {tracker.cached_tokens:,} cachade prompt-tokens".replace(",", " "))

    cost = tracker.cost(prices)
    if cost is None:
        logger.info(
            "  kostnad: okänd. Fyll i app/config/model_prices.json med aktuella "
            "priser per miljon tokens för att få en uppskattning."
        )
    else:
        logger.info(f"  uppskattad kostnad: {cost:.2f} {prices.get('_valuta', 'USD')}")
