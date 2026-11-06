from __future__ import annotations

from .base import InfrastructureError, Player, PlayerReply
from .baselines import ProbabilityBot, RandomPlayer
from .ollama_player import OllamaPlayer, parse_options


def split_spec(spec: str) -> tuple[str, dict[str, str]]:
    """'ollama:m@cot?think=false+num_predict=300' -> ('ollama:m@cot', {'think': 'false', 'num_predict': '300'}).
    Las opciones van tras '?' separadas por '+' (o '&'); '+' evita problemas de quoting en PowerShell/bash."""
    base, sep, query = spec.partition("?")
    options: dict[str, str] = {}
    if sep:
        for item in filter(None, query.replace("&", "+").split("+")):
            key, eq, value = item.partition("=")
            if not eq or not key.strip():
                raise ValueError(f"opción mal formada {item!r} en {spec!r}: usa clave=valor")
            options[key.strip()] = value.strip()
    return base, options


def make_player(spec: str, name: str, seed: int | None = None) -> Player:
    """Crea un jugador a partir de un texto:
        random | random:<p_dudo>
        probbot | probbot:<bluff>
        ollama:<modelo>[@<estilo>][?clave=valor+clave=valor]
            p.ej. ollama:llama3.2:3b@basic?think=false+temperature=0.2+num_predict=300
    Las opciones válidas de ollama están en `ollama_player.OPTION_PARSERS`: añadir una allí
    basta para poder usarla desde el spec (no hace falta tocar este archivo).
    """
    base, raw_options = split_spec(spec)
    kind, _, arg = base.partition(":")
    if kind in ("random", "probbot"):
        if raw_options:
            raise ValueError(f"{kind!r} no admite opciones con '?': {spec!r}")
        if kind == "random":
            return RandomPlayer(name, seed=seed, p_dudo=float(arg) if arg else 0.2, spec=spec)
        return ProbabilityBot(name, bluff=float(arg) if arg else 0.0, seed=seed, spec=spec)
    if kind == "ollama":
        if not arg:
            raise ValueError("Uso: ollama:<modelo>[@<estilo>][?clave=valor+...]")
        model, _, style = arg.partition("@")
        if not model:
            raise ValueError(f"falta el nombre del modelo en {spec!r}")
        return OllamaPlayer(name, model=model, style=style or "basic", seed=seed, spec=spec,
                            **parse_options(raw_options))
    raise ValueError(f"Tipo de jugador desconocido: {spec!r}")


__all__ = ["Player", "PlayerReply", "InfrastructureError", "RandomPlayer", "ProbabilityBot",
           "OllamaPlayer", "make_player", "split_spec"]
