"""Pure pricing for model API calls. Integer microcents only — no float, no Decimal.

The price table lives in core.constants (a dated snapshot, not a runtime fetch).
"""

from __future__ import annotations

from agentic_cab.core.constants import MODEL_PRICES_MICROCENTS_PER_MTOK

_TOKENS_PER_MTOK = 1_000_000


def price_call(
    *,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_input_tokens: int = 0,
    cache_creation_input_tokens: int = 0,
) -> int:
    """Return the cost of one model call, in microcents.

    Raises ValueError for a model id absent from the price table. Silently pricing an
    unknown model at zero would produce a ledger that looks populated but reports no
    spend — a wrong number is worse than a crash, so this fails loudly instead.
    """
    prices = MODEL_PRICES_MICROCENTS_PER_MTOK.get(model)
    if prices is None:
        msg = (
            f"No price entry for model {model!r}. Add it to "
            "MODEL_PRICES_MICROCENTS_PER_MTOK in core/constants.py — pricing an unknown "
            "model at zero would silently under-report spend."
        )
        raise ValueError(msg)
    # Floor division per bucket. With the current table every per-MTok price is a
    # multiple of 1_000_000, so each bucket divides evenly and no rounding occurs
    # today; the floor is the documented behaviour should a future price not divide.
    return (
        input_tokens * prices["input"] // _TOKENS_PER_MTOK
        + output_tokens * prices["output"] // _TOKENS_PER_MTOK
        + cache_read_input_tokens * prices["cache_read"] // _TOKENS_PER_MTOK
        + cache_creation_input_tokens * prices["cache_write"] // _TOKENS_PER_MTOK
    )


__all__ = ["price_call"]
