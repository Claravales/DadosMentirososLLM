"""Convierte el texto libre de un modelo en una `Action`.

Formatos aceptados (se usa la ÚLTIMA jugada VÁLIDA del texto, porque un modelo con
razonamiento paso a paso suele poner ejemplos o jugadas descartadas antes de la
decisión final; una jugada mal formada posterior no anula una buena anterior).
Los bloques <think>...</think> (modelos con razonamiento) se descartan antes de
buscar la jugada; si el bloque quedó abierto (respuesta truncada) se ignora todo
lo que hay desde la etiqueta.

  JSON:  {"action": "bid", "quantity": 3, "face": 4}      {"action": "dudo"}
  Tags:  [APUESTA: 3, 4]                                  [DUDO]

El parser sólo extrae; que la jugada sea LEGAL lo decide `rules.validate_action`.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Optional

from .rules import Action

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_THINK_OPEN_RE = re.compile(r"<think>.*", re.DOTALL | re.IGNORECASE)
_TAG_DUDO_RE = re.compile(r"\[\s*DUDO\s*\]", re.IGNORECASE)
_TAG_BID_RE = re.compile(r"\[\s*APUESTA\s*:\s*(\d+)\s*[,xX]\s*(\d+)\s*\]", re.IGNORECASE)

_BID_WORDS = {"bid", "apuesta", "apostar", "raise", "subir"}
_DUDO_WORDS = {"dudo", "dudar", "challenge", "doubt", "liar", "mentiroso"}


@dataclass
class ParseResult:
    action: Optional[Action] = None
    error: Optional[str] = None


def _to_int(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and re.fullmatch(r"\s*\d+\s*", value):
        return int(value)
    return None


def _from_json_obj(obj: Any) -> Optional[ParseResult]:
    if not isinstance(obj, dict):
        return None
    kind = obj.get("action", obj.get("accion", obj.get("jugada")))
    if not isinstance(kind, str):
        return None
    kind = kind.strip().lower()
    if kind in _DUDO_WORDS:
        return ParseResult(Action.dudo())
    if kind in _BID_WORDS:
        q = _to_int(obj.get("quantity", obj.get("cantidad")))
        f = _to_int(obj.get("face", obj.get("valor", obj.get("cara"))))
        if q is None or f is None:
            return ParseResult(error="La apuesta necesita 'quantity' y 'face' como números enteros.")
        return ParseResult(Action.make_bid(q, f))
    return ParseResult(error=f"Valor de 'action' no reconocido: {kind!r} (usa 'bid' o 'dudo').")


def _strip_thinking(text: str) -> str:
    """Quita el razonamiento <think>."""
    text = _THINK_RE.sub(" ", text)
    text = _THINK_OPEN_RE.sub(" ", text)          # bloque sin cerrar (truncado)
    if re.search(r"</think>", text, re.IGNORECASE):  # plantillas que omiten el <think> inicial
        text = re.split(r"</think>", text, flags=re.IGNORECASE)[-1]
    return text


def parse_response(text: Optional[str]) -> ParseResult:
    if text is None or not str(text).strip():
        return ParseResult(error="Respuesta vacía.")
    text = _strip_thinking(str(text))
    if not text.strip():
        return ParseResult(error="La respuesta sólo contiene razonamiento y ninguna jugada final "
                                 "(¿truncada?). Responde directamente con la jugada.")

    # candidatos: (posición en el texto, ParseResult)
    candidates: list[tuple[int, ParseResult]] = []

    # JSON: se escanea cada '{' con raw_decode, así se admiten objetos anidados.
    decoder = json.JSONDecoder()
    i = 0
    while True:
        i = text.find("{", i)
        if i < 0:
            break
        try:
            obj, end = decoder.raw_decode(text, i)
        except json.JSONDecodeError:
            i += 1
            continue
        res = _from_json_obj(obj)
        if res is not None:
            candidates.append((i, res))
            i = end
        else:
            i += 1  # dict sin 'action': puede haber una jugada anidada dentro

    for m in _TAG_BID_RE.finditer(text):
        candidates.append((m.start(), ParseResult(Action.make_bid(int(m.group(1)), int(m.group(2))))))
    for m in _TAG_DUDO_RE.finditer(text):
        candidates.append((m.start(), ParseResult(Action.dudo())))

    if not candidates:
        return ParseResult(
            error='No encontré ninguna jugada. Responde con {"action":"bid","quantity":N,"face":F} '
            'o {"action":"dudo"}.'
        )
    candidates.sort(key=lambda c: c[0])
    valid = [c for c in candidates if c[1].action is not None]
    return (valid[-1] if valid else candidates[-1])[1]
