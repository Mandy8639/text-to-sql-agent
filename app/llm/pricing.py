"""Per-model token pricing, used to report cost per question in evals.

Prices are USD per 1M tokens (Anthropic first-party API, checked 2026-10). Update this table
when prices change; unknown models cost `None` rather than a wrong number.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Price:
    input_per_mtok: float
    output_per_mtok: float


PRICES: dict[str, Price] = {
    "claude-opus-5-5": Price(4.00, 20.00),
    "claude-sonnet-5-5": Price(2.00, 10.00),
    "claude-haiku-5-5": Price(0.10, 0.50),
}


def estimate_cost_usd(model: str, input_tokens: int, output_tokens: int) -> float | None:
    """Return the cost of one call in USD, or None if the model's price is unknown."""
    price = PRICES.get(model)
    if price is None:
        return None
    return (input_tokens * price.input_per_mtok + output_tokens * price.output_per_mtok) / 1e6
