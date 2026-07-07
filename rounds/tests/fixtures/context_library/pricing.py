"""Pricing helpers for the context-library fixture.

Contains one deliberate, known bug (marked below) used by
tests/integration/test_agent_node_end_to_end.py to verify that a
diagnosis engine can trace a production error back to this exact line.
"""


def calculate_discounted_total(prices: list[float], discount_percent: float) -> float:
    """Return the discounted total of a list of prices.

    Raises ZeroDivisionError when `prices` is empty, since the average
    is computed by dividing by the number of prices without checking
    for an empty list first.
    """
    total = sum(prices)
    average = total / len(prices)  # BUG: no guard against an empty prices list
    return average * len(prices) * (1 - discount_percent / 100)
