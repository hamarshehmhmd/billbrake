"""USD prices per 1M tokens. Override or extend in config under `pricing:`.

Prices change; verify against the provider's pricing page before relying on them.
"""

DEFAULT_PRICING: dict[str, dict[str, float]] = {
    "gpt-4o": {"input": 2.50, "output": 10.00},
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "gpt-4.1": {"input": 2.00, "output": 8.00},
    "gpt-4.1-mini": {"input": 0.40, "output": 1.60},
    "text-embedding-3-small": {"input": 0.02, "output": 0.0},
    "claude-sonnet-4": {"input": 3.00, "output": 15.00},
    "claude-3-5-haiku": {"input": 0.80, "output": 4.00},
    "claude-opus-4": {"input": 15.00, "output": 75.00},
}


class UnknownModel(Exception):
    pass


def lookup(model: str, table: dict[str, dict[str, float]]) -> dict[str, float]:
    """Exact match first, then longest prefix (handles dated ids like claude-sonnet-4-20250514)."""
    if model in table:
        return table[model]
    best = max((k for k in table if model.startswith(k)), key=len, default=None)
    if best is None:
        raise UnknownModel(model)
    return table[best]


def cost_usd(model: str, input_tokens: int, output_tokens: int, table: dict[str, dict[str, float]]) -> float:
    p = lookup(model, table)
    return (input_tokens * p["input"] + output_tokens * p["output"]) / 1_000_000
