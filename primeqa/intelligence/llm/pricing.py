"""Anthropic model pricing + cost computation.

Kept in one place so swapping a model or adjusting prices is a one-line
change. Prices are USD per 1M tokens and include input / output / cache
read (90% discount on input) / cache write (125% of input).

Source: https://www.anthropic.com/pricing as of 2026-04-19.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Dict, Optional


class ModelPrice:
    """Prices per 1M tokens, USD."""
    __slots__ = ("input", "output", "cache_read", "cache_write")

    def __init__(self, input: float, output: float):
        self.input = input
        self.output = output
        # Anthropic pricing: cache read is 10% of input, cache write is
        # 125% of input. Compute once.
        self.cache_read = round(input * 0.10, 4)
        self.cache_write = round(input * 1.25, 4)


# Keep one canonical key per model id. Routers and UI refer to these
# by the VARCHAR values; never decompose the string.
MODEL_PRICING: Dict[str, ModelPrice] = {
    # Claude 4.7 (newest, current OPUS default in router.py)
    "claude-opus-4-7":            ModelPrice(input=15.00, output=75.00),
    # Claude 5 family — Sonnet 5 is the S3 generation default as of 2026-07-02
    # (see router.SONNET_5 / generation/routing.py). Same $3/$15 Sonnet tier.
    "claude-sonnet-5":            ModelPrice(input=3.00,  output=15.00),
    # Claude 4.6
    "claude-opus-4-6":            ModelPrice(input=15.00, output=75.00),
    "claude-sonnet-4-6":          ModelPrice(input=3.00,  output=15.00),
    # Claude 4.5 family (SONNET default + Opus legacy fallback)
    "claude-opus-4-5-20251101":   ModelPrice(input=15.00, output=75.00),
    "claude-sonnet-4-5-20250929": ModelPrice(input=3.00,  output=15.00),
    "claude-haiku-4-5-20251001":  ModelPrice(input=1.00,  output=5.00),
    # Claude 4.1 (available to this key if needed)
    "claude-opus-4-1-20250805":   ModelPrice(input=15.00, output=75.00),
    # Claude 4 family (Opus still current; Sonnet deprecated 6/15/2026)
    "claude-opus-4-20250514":     ModelPrice(input=15.00, output=75.00),
    "claude-sonnet-4-20250514":   ModelPrice(input=3.00,  output=15.00),
    # Older families supported for tenants still on them
    "claude-3-7-sonnet-20250219": ModelPrice(input=3.00,  output=15.00),
    "claude-3-5-haiku-20241022":  ModelPrice(input=0.80,  output=4.00),
    "claude-3-5-sonnet-20241022": ModelPrice(input=3.00,  output=15.00),
}


# ---- Catalog overlay (migration 061, per-tenant model control arc) ---------
# Models enabled at runtime from the superadmin Models panel carry their own
# entered rates in llm_models; resolve_rates checks that overlay BEFORE the
# code table so a runtime-enabled model can never silently bill at the
# unknown-model fallback rate. Cached in-process because this sits on the
# per-call usage-recording path.
_RATES_TTL_S = 60.0
_rates_cache: Dict[str, tuple] = {}   # model_id -> (monotonic_expiry, ModelPrice|None)


def _catalog_rates(model: str) -> Optional[ModelPrice]:
    """The llm_models overlay rate for ``model``, or None (no row / no rates /
    read failure \u2014 fail-open to the code table; a missing catalog table before
    migration 061 lands must never break cost accounting)."""
    import time
    hit = _rates_cache.get(model)
    if hit is not None and time.monotonic() < hit[0]:
        return hit[1]
    price: Optional[ModelPrice] = None
    try:
        from sqlalchemy.orm import Session
        from primeqa.db import engine
        from primeqa.core.models import LlmModel

        sess = Session(bind=engine)
        try:
            row = sess.query(
                LlmModel.input_usd_per_mtok, LlmModel.output_usd_per_mtok,
            ).filter(LlmModel.model_id == model).first()
        finally:
            sess.close()
        if row and row[0] is not None and row[1] is not None:
            price = ModelPrice(input=float(row[0]), output=float(row[1]))
    except Exception:
        return None   # fail-open, uncached \u2014 retry on the next call
    _rates_cache[model] = (time.monotonic() + _RATES_TTL_S, price)
    return price


def resolve_rates(model: str) -> Optional[ModelPrice]:
    """The effective rates for ``model``: catalog overlay \u2192 MODEL_PRICING \u2192
    None. The single lookup every consumer (compute_cost_usd, dashboards)
    should use once a model can be enabled at runtime."""
    return _catalog_rates(model) or MODEL_PRICING.get(model)


def compute_cost_usd(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cached_input_tokens: int = 0,
    cache_write_tokens: int = 0,
) -> float:
    """Compute the USD cost of a single LLM call.

    input_tokens here should be the NON-cached portion (i.e. what the
    provider billed at full input rate). cached_input_tokens are
    billed at the cheaper cache-read rate. cache_write_tokens are the
    one-off cost of populating the cache.

    Returns a float rounded to 6 decimals (micros of a dollar).
    """
    price = resolve_rates(model)
    if not price:
        # Unknown model \u2014 fall back to Sonnet-4 rates for an honest
        # upper-ish estimate rather than returning 0 which would silently
        # under-report spend.
        price = MODEL_PRICING["claude-sonnet-4-20250514"]

    cost = Decimal("0")
    cost += Decimal(str(price.input)) * Decimal(input_tokens) / Decimal("1000000")
    cost += Decimal(str(price.output)) * Decimal(output_tokens) / Decimal("1000000")
    cost += Decimal(str(price.cache_read)) * Decimal(cached_input_tokens) / Decimal("1000000")
    cost += Decimal(str(price.cache_write)) * Decimal(cache_write_tokens) / Decimal("1000000")
    return float(round(cost, 6))


def get_price(model: str) -> Optional[ModelPrice]:
    return resolve_rates(model)
