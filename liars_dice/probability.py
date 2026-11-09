"""Probabilidades exactas (binomial). Sirven para el bot de referencia y para
que el análisis mida si un modelo apuesta/duda con sentido estadístico."""
from __future__ import annotations

from math import comb
from typing import Sequence

from .rules import Bid


def prob_at_least(k: int, n: int, p: float = 1 / 6) -> float:
    """P(X >= k) con X ~ Binomial(n, p)."""
    if k <= 0:
        return 1.0
    if k > n:
        return 0.0
    return sum(comb(n, i) * p**i * (1 - p) ** (n - i) for i in range(k, n + 1))


def p_bid_true(bid: Bid, own_dice: Sequence[int], unknown_dice: int) -> float:
    """Probabilidad de que `bid` sea cierta sabiendo mis dados y que hay
    `unknown_dice` dados ajenos desconocidos (cada uno 1/6 de ser esa cara)."""
    need = bid.quantity - list(own_dice).count(bid.face)
    return prob_at_least(need, unknown_dice)
