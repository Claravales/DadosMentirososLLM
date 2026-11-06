"""Jugadores de referencia SIN IA. Sirven para probar el árbitro y como
línea base en los experimentos (¿un modelo le gana a un bot estadístico?)."""
from __future__ import annotations

import json
import random
from typing import Optional

from ..observation import Observation
from ..probability import p_bid_true
from ..rules import Bid, legal_bids
from .base import Player


def _bid_json(b: Bid) -> str:
    return json.dumps({"action": "bid", "quantity": b.quantity, "face": b.face})


class RandomPlayer(Player):
    """Elige al azar entre las jugadas legales (duda con probabilidad `p_dudo`)."""

    def __init__(self, name: str, seed: Optional[int] = None, p_dudo: float = 0.2, spec: str = "random"):
        super().__init__(name, spec)
        self.rng = random.Random(seed)
        self.seed = seed
        self.p_dudo = p_dudo

    def describe(self) -> dict:
        return {"class": "RandomPlayer", "p_dudo": self.p_dudo, "seed": self.seed}

    def act(self, obs: Observation, feedback: Optional[str] = None) -> str:
        bids = list(legal_bids(obs.current_bid, obs.total_dice))
        if obs.can_dudo and (not bids or self.rng.random() < self.p_dudo):
            return json.dumps({"action": "dudo"})
        # sesgo hacia subidas pequeñas para que las rondas no acaben en 2 turnos
        small = bids[: max(1, min(len(bids), 8))]
        return _bid_json(self.rng.choice(small))


class ProbabilityBot(Player):
    """Bot estadístico: duda si la apuesta previa es poco probable dada su mano;
    si no, hace la subida más segura. `bluff` = prob. de farolear (subida al azar)."""

    def __init__(self, name: str, dudo_threshold: float = 0.35, bluff: float = 0.0,
                 seed: Optional[int] = None, spec: str = "probbot"):
        super().__init__(name, spec)
        self.dudo_threshold = dudo_threshold
        self.bluff = bluff
        self.seed = seed
        self.rng = random.Random(seed)

    def describe(self) -> dict:
        return {"class": "ProbabilityBot", "dudo_threshold": self.dudo_threshold,
                "bluff": self.bluff, "seed": self.seed}

    def act(self, obs: Observation, feedback: Optional[str] = None) -> str:
        own, unknown = obs.your_dice, obs.unknown_dice
        if obs.current_bid is not None:
            if p_bid_true(obs.current_bid, own, unknown) < self.dudo_threshold:
                return json.dumps({"action": "dudo"})
        bids = list(legal_bids(obs.current_bid, obs.total_dice))
        if not bids:
            return json.dumps({"action": "dudo"})
        if self.rng.random() < self.bluff:
            return _bid_json(self.rng.choice(bids[: min(len(bids), 10)]))
        # la subida más probable; a igualdad, la más baja
        best = max(bids, key=lambda b: (p_bid_true(b, own, unknown), -b.quantity, -b.face))
        return _bid_json(best)
