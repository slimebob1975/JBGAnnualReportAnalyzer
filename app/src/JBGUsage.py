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
ROLES_FILENAME = "model_roles.json"

# Maps a purpose to the key used in model_roles.json.
ROLE_KEYS = {
    PURPOSE_EXTRACTION: "extraktion",
    PURPOSE_SECOND_PASS: "omsokning",
    PURPOSE_STABILITY: "stabilitet",
    PURPOSE_YEAR: "aar",
    PURPOSE_PAGE_OFFSET: "sidnummer",
}


# The models offered in the form, grouped for the dropdown.
#
# Kept here rather than in the template because the same list has to validate
# what comes back: a role is written to a config file from a form field, so an
# unknown value must not reach it.
#
# Deliberately excluded: the codex, search-api and cyber variants, which are
# specialised for code, search and security rather than document extraction.
MODEL_GROUPS = [
    ("Senaste (5.6)", ["gpt-5.6-solar", "gpt-5.6-terra", "gpt-5.6-luna"]),
    ("Tidigare generationer", [
        "gpt-5.5", "gpt-5.4", "gpt-5.4-mini", "gpt-5.4-nano",
        "gpt-5.2", "gpt-5.1", "gpt-5", "gpt-5-mini", "gpt-5-nano",
    ]),
    ("Pro (avsevärt dyrare)", [
        "gpt-5.5-pro", "gpt-5.4-pro", "gpt-5.2-pro", "gpt-5-pro",
    ]),
]

DEFAULT_MODEL = "gpt-5.2"

# The roles a model can be chosen for in the form. The remaining roles
# (year and page offset) follow the extraction model.
FORM_ROLES = [
    ("extraktion", "Första genomgången", PURPOSE_EXTRACTION),
    ("omsokning", "Riktad omsökning", PURPOSE_SECOND_PASS),
    ("stabilitet", "Stabilitetskontroll", PURPOSE_STABILITY),
]


def known_models() -> list:
    return [name for _, names in MODEL_GROUPS for name in names]


def is_known_model(name: str) -> bool:
    return name in known_models()


def model_choices(prices: dict = None) -> list:
    """(group, [(value, label)]) for rendering, with the price in the label.

    The cost of a model is most useful at the point of choosing it.
    """
    prices = prices if prices is not None else load_prices()
    groups = []
    for group, names in MODEL_GROUPS:
        options = []
        for name in names:
            entry = UsageTracker.price_for(name, prices) or {}
            label = name
            if entry:
                label += (
                    f"  ({entry['in']:.2f} / {entry['ut']:.2f} USD per M tokens)"
                    .replace(".", ",")
                )
            options.append((name, label))
        groups.append((group, options))
    return groups


def save_roles(roles: dict, config_dir: Path = None) -> bool:
    """Persist the chosen models, keeping the rest of the file intact.

    Merged rather than rewritten: the comment block explains the format, and
    the roles that are not offered in the form must keep whatever they had.
    """
    override = os.getenv("JBG_MODEL_ROLES")
    path = Path(override) if override else (
        (config_dir or Path(__file__).resolve().parents[1] / "config") / ROLES_FILENAME
    )

    existing = {}
    if path.is_file():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            existing = {}

    for key, value in roles.items():
        if value and not is_known_model(value):
            logger.warning(f"Okänd modell '{value}' för rollen '{key}'. Sparas inte.")
            continue
        existing[key] = value or ""

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(existing, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        return True
    except OSError as ex:
        logger.warning(f"Kunde inte spara modellrollerna till {path}: {ex}")
        return False


def selected_roles(config_dir: Path = None) -> dict:
    """What the form should show as selected, per role key."""
    override = os.getenv("JBG_MODEL_ROLES")
    path = Path(override) if override else (
        (config_dir or Path(__file__).resolve().parents[1] / "config") / ROLES_FILENAME
    )
    saved = {}
    if path.is_file():
        try:
            saved = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            saved = {}
    return {
        key: (saved.get(key) if is_known_model(saved.get(key) or "") else DEFAULT_MODEL)
        for key, _, _ in FORM_ROLES
    }


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

    @staticmethod
    def price_for(model: str, prices: dict) -> dict | None:
        """Find a model's prices, tolerating a dated model name.

        The API answers with names like "gpt-5.2-2025-12-11" while the price
        list and the form both say "gpt-5.2", so an exact lookup misses.
        """
        if model in prices:
            return prices[model]
        candidates = [k for k in prices if not k.startswith("_") and model.startswith(k)]
        if not candidates:
            return None
        return prices[max(candidates, key=len)]

    def cost(self, prices: dict) -> float | None:
        """Total cost, or None when any model used has no price configured.

        Cached prompt tokens are billed at roughly a tenth of the input rate,
        and on a real run 88% of prompt tokens were cached. Charging them at
        the full rate overstated the bill several times over.

        Partial pricing returns None: understating a cost is the one direction
        an estimate must not err in.
        """
        if not prices:
            return None
        total = 0.0
        for model, bucket in self.by_model.items():
            entry = self.price_for(model, prices)
            if not entry:
                return None
            fresh = max(bucket.prompt_tokens - bucket.cached_tokens, 0)
            cached_rate = float(entry.get("cachad", entry.get("in", 0)))
            total += fresh / 1_000_000 * float(entry.get("in", 0))
            total += bucket.cached_tokens / 1_000_000 * cached_rate
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


def load_roles(config_dir: Path = None) -> dict:
    """Which model to use for which part of the analysis.

    An empty or missing value means the model chosen in the form. Configuring
    a different model per role is how the trade-off between cost and coverage
    can be measured rather than guessed at.
    """
    override = os.getenv("JBG_MODEL_ROLES")
    path = Path(override) if override else (
        (config_dir or Path(__file__).resolve().parents[1] / "config") / ROLES_FILENAME
    )
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as ex:
        logger.warning(f"Kunde inte läsa modellrollerna {path}: {ex}")
        return {}
    return {
        purpose: str(data.get(key) or "").strip()
        for purpose, key in ROLE_KEYS.items()
        if str(data.get(key) or "").strip()
    }


def log_roles(roles: dict, default_model: str) -> None:
    if not roles:
        logger.info(f"Modell för alla anrop: {default_model}")
        return
    described = ", ".join(f"{p}={m}" for p, m in sorted(roles.items()))
    logger.info(f"Modell per roll: {described}. Övriga: {default_model}")


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
        line = (
            f"  {model}: {b.calls} anrop, {b.prompt_tokens:,} prompt "
            f"({b.cached_tokens:,} cachade) + {b.completion_tokens:,} svar"
        ).replace(",", " ")
        entry = UsageTracker.price_for(model, prices)
        if entry:
            fresh = max(b.prompt_tokens - b.cached_tokens, 0)
            spend = (
                fresh / 1_000_000 * float(entry.get("in", 0))
                + b.cached_tokens / 1_000_000 * float(entry.get("cachad", entry.get("in", 0)))
                + b.completion_tokens / 1_000_000 * float(entry.get("ut", 0))
            )
            line += f" = {spend:.2f} {prices.get('_valuta', 'USD')}"
        logger.info(line)
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
