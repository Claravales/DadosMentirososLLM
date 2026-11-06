"""Reglas puras del juego (sin estado, sin IA, sin E/S).

Reglas elegidas (documentadas en el README):
- Cada jugador empieza con N dados (por defecto 5). Dados de 6 caras.
- Sin comodines: los 1 NO son comodín. Cuenta exactamente la cara apostada.
- Una apuesta es (cantidad, cara). Subir = más cantidad (cualquier cara) o misma
  cantidad con cara MAYOR.
- La cantidad no puede superar el total de dados vivos en la mesa.
- En el primer turno de una ronda no se puede dudar (no hay apuesta previa).
- Dudar: se destapan todos los dados. Si hay >= cantidad apostada, la apuesta era
  cierta y pierde un dado quien dudó; si no, pierde un dado quien apostó.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator, Optional, Sequence

FACES = (1, 2, 3, 4, 5, 6)


@dataclass(frozen=True)
class Bid:
    quantity: int
    face: int

    def as_dict(self) -> dict:
        return {"quantity": self.quantity, "face": self.face}

    def __str__(self) -> str:
        return f"{self.quantity}x{self.face}"


@dataclass(frozen=True)
class Action:
    """Jugada de un jugador: apostar (`bid`) o dudar (`dudo`)."""

    kind: str  # "bid" | "dudo"
    bid: Optional[Bid] = None

    @staticmethod
    def make_bid(quantity: int, face: int) -> "Action":
        return Action("bid", Bid(quantity, face))

    @staticmethod
    def dudo() -> "Action":
        return Action("dudo")

    def as_dict(self) -> dict:
        if self.kind == "bid" and self.bid is not None:
            return {"action": "bid", **self.bid.as_dict()}
        return {"action": "dudo"}


def is_higher(new: Bid, prev: Optional[Bid]) -> bool:
    """¿`new` es una subida válida sobre `prev`?"""
    if prev is None:
        return True
    if new.quantity > prev.quantity:
        return True
    return new.quantity == prev.quantity and new.face > prev.face


def validate_action(action: Action, prev: Optional[Bid], total_dice: int) -> Optional[str]:
    """Devuelve None si la jugada es legal, o un mensaje de error (en español)."""
    if action.kind == "dudo":
        if prev is None:
            return "No puedes dudar en el primer turno de la ronda: aún no hay ninguna apuesta."
        return None
    if action.kind != "bid" or action.bid is None:
        return f"Tipo de jugada desconocido: {action.kind!r}."
    b = action.bid
    if b.face not in FACES:
        return f"La cara debe estar entre 1 y 6 (recibido {b.face})."
    if b.quantity < 1:
        return f"La cantidad debe ser al menos 1 (recibido {b.quantity})."
    if b.quantity > total_dice:
        return f"Sólo hay {total_dice} dados en la mesa; no puedes apostar {b.quantity}."
    if not is_higher(b, prev):
        return (
            f"La apuesta {b.quantity}x{b.face} no supera a la anterior {prev.quantity}x{prev.face}. "
            "Debes subir la cantidad, o mantenerla con una cara mayor."
        )
    return None


def legal_bids(prev: Optional[Bid], total_dice: int) -> Iterator[Bid]:
    """Genera todas las apuestas legales, de menor a mayor."""
    for q in range(1, total_dice + 1):
        for f in FACES:
            b = Bid(q, f)
            if is_higher(b, prev):
                yield b


def min_legal_bid(prev: Optional[Bid], total_dice: int) -> Optional[Bid]:
    """Apuesta legal más baja, o None si ya no se puede subir (sólo queda dudar)."""
    return next(legal_bids(prev, total_dice), None)


def count_face(hands: Sequence[Sequence[int]], face: int) -> int:
    return sum(h.count(face) for h in hands)


def resolve_challenge(bid: Bid, hands: Sequence[Sequence[int]]) -> tuple[int, bool]:
    """Devuelve (dados reales con esa cara, ¿la apuesta era cierta?)."""
    real = count_face(hands, bid.face)
    return real, real >= bid.quantity
