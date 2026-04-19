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
    # Claude 4 family (Opus + Sonnet only; Anthropic has not shipped a
    # Claude-4 Haiku \u2014 see router.HAIKU which points at 3.5 Haiku).
    "claude-opus-4-20250514":     ModelPrice(input=15.00, output=75.00),
    "claude-sonnet-4-20250514":   ModelPrice(input=3.00,  output=15.00),
    # Older families supported for tenants still on them
    "claude-3-7-sonnet-20250219": ModelPrice(input=3.00,  output=15.00),
    "claude-3-5-haiku-20241022":  ModelPrice(input=0.80,  output=4.00),
    "claude-3-5-sonnet-20241022": ModelPrice(input=3.00,  output=15.00),
}


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
    price = MODEL_PRICING.get(model)
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
    return MODEL_PRICING.get(model)
