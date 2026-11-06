"""Lo que un jugador PUEDE saber en su turno.

Garantía de privacidad: el árbitro construye este objeto pasándole únicamente
los dados del propio jugador. Los dados ajenos de la ronda en curso no existen
dentro de `Observation`; sólo se ven los de rondas ya destapadas (información
pública, igual que en una mesa real).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .rules import Bid


@dataclass(frozen=True)
class Observation:
    you: str
    your_dice: tuple[int, ...]
    round_number: int
    turn_order: tuple[str, ...]  # jugadores vivos, en orden de turno
    dice_counts: dict  # nombre -> nº de dados vivos (de todos, público)
    total_dice: int
    current_bid: Optional[Bid]
    current_bidder: Optional[str]
    bids_this_round: tuple[dict, ...]  # [{"player","quantity","face"}...]
    past_rounds: tuple[dict, ...] = field(default_factory=tuple)  # rondas ya destapadas
    can_dudo: bool = False
    min_bid: Optional[Bid] = None  # apuesta legal más baja (None => sólo dudar)

    def to_dict(self) -> dict:
        return {
            "you": self.you,
            "your_dice": list(self.your_dice),
            "round_number": self.round_number,
            "turn_order": list(self.turn_order),
            "dice_counts": dict(self.dice_counts),
            "total_dice": self.total_dice,
            "current_bid": self.current_bid.as_dict() if self.current_bid else None,
            "current_bidder": self.current_bidder,
            "bids_this_round": list(self.bids_this_round),
            "past_rounds": list(self.past_rounds),
            "can_dudo": self.can_dudo,
            "min_bid": self.min_bid.as_dict() if self.min_bid else None,
        }

    @property
    def unknown_dice(self) -> int:
        """Nº de dados ajenos (desconocidos para mí)."""
        return self.total_dice - len(self.your_dice)
