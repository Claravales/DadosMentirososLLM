"""Contrato que debe cumplir cualquier jugador (modelo local, bot, humano...).

CONTRATO ESTABLE (ver README): `Player.act(obs, feedback)` devuelve `str` o `PlayerReply`.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional, Union

from ..observation import Observation


class InfrastructureError(RuntimeError):
    """El servidor del modelo no está disponible o está mal configurado (Ollama caído,
    modelo inexistente...). NO es culpa del modelo: el árbitro aborta la partida en
    lugar de contarla como respuesta inválida y rellenar con jugadas forzadas."""


@dataclass(frozen=True)
class PlayerReply:
    """Respuesta con metadatos opcionales. El árbitro parsea `text` y copia `meta`
    tal cual al elemento correspondiente de `attempts` en el log.

    Claves de `meta` con significado para el árbitro:
      empty_reason : si `text` viene vacío, por qué (se añade al error: "Respuesta vacía: ...").
      infra_events : lista de dicts (`kind`, ...) con incidencias de infraestructura recuperadas
                     (p. ej. un reintento tras HTTP 500). El árbitro las saca de `meta` y las registra
                     como eventos `infrastructure`. Una excepción también puede llevarlas en `.events`.
    El resto (thinking, tokens, tiempos...) son libres, pero deben ser serializables a JSON.
    """

    text: Optional[str]
    meta: dict = field(default_factory=dict)


class Player(ABC):
    """Un jugador recibe una `Observation` y devuelve TEXTO CRUDO (o un `PlayerReply`).

    El árbitro se encarga de parsearlo y validarlo; el jugador no tiene que
    garantizar nada. Si `feedback` no es None, la respuesta anterior fue
    rechazada y el motivo viene ahí (para que el modelo pueda corregirse).
    Devolver un `str` sigue siendo válido (los bots no necesitan `PlayerReply`).
    """

    def __init__(self, name: str, spec: str = ""):
        self.name = name
        self.spec = spec or self.__class__.__name__

    @abstractmethod
    def act(self, obs: Observation, feedback: Optional[str] = None) -> Union[str, PlayerReply]:
        ...

    def describe(self) -> dict:
        """Parámetros del jugador que se guardan en `game_start` (reproducibilidad).
        Sobrescribidlo para exponer vuestra configuración; debe ser serializable a JSON."""
        return {"class": self.__class__.__name__}

    def warmup(self) -> list:
        """Se llama UNA vez por spec antes de la primera partida (p. ej. cargar el modelo en memoria).
        Devuelve una lista de eventos de infraestructura (dicts con `kind`) que `run_games.py` guarda en
        `infrastructure.jsonl`; lanza `InfrastructureError` (con `.events` opcional) si el servidor no está sano."""
        return []

    def on_game_start(self) -> None:  # hook opcional (reiniciar memoria, etc.)
        pass
