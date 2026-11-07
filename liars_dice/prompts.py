"""Prompts para los modelos. >>> ESTE ARCHIVO ES TERRITORIO DEL MIEMBRO 2 <<<

Aquí hay una versión base funcional. Para el experimento, añadid más estilos
(p. ej. "cot", "agresivo", "conservador") registrándolos en STYLES.
Contrato: `build_messages(obs, feedback, style)` -> lista de mensajes estilo chat
[{"role": "system"|"user"|"assistant", "content": "..."}].
"""
from __future__ import annotations

import json
from typing import Optional

from .observation import Observation

RULES = """Juegas a los Dados Mentirosos (Liar's Dice) contra otros jugadores.
- Cada jugador tiene dados de 6 caras ocultos bajo su cubilete. Tú sólo ves los tuyos.
- En tu turno haces UNA de estas dos cosas:
  1) APOSTAR una cantidad y una cara: "hay al menos N dados con cara F en TOTAL entre todos los jugadores".
     Debes SUBIR la apuesta anterior: más cantidad (con cualquier cara) o la misma cantidad con una cara mayor.
     REPETIR la apuesta anterior (misma cantidad y misma cara) es ILEGAL, y bajarla también.
     La cantidad no puede superar el total de dados en la mesa. Los 1 NO son comodín.
  2) DUDAR de la apuesta anterior (no puedes dudar si eres el primero en apostar).
- Al dudar se destapan todos los dados. Si hay al menos la cantidad apostada, la apuesta era cierta y pierde un dado quien dudó;
  si no, pierde un dado quien apostó. Gana el último jugador con dados.
- Puedes farolear (apostar algo que no tienes) si te conviene."""

FORMAT = """Responde ÚNICAMENTE con un objeto JSON en una línea, con NÚMEROS enteros (no letras):
{"action": "bid", "quantity": 3, "face": 4}   para apostar (ejemplo: 3 dados con cara 4)
{"action": "dudo"}                             para dudar
Los números del ejemplo son sólo ilustrativos: elige los tuyos según tu situación."""

# Estilo con razonamiento paso a paso. Aquí se permite razonar antes de la jugada
# (el parser se queda con la ÚLTIMA jugada válida del texto).
STYLE_COT = """ESTILO: razona paso a paso.
Esta vez NO respondas sólo con el JSON: antes piensa en voz alta, de forma breve (máximo 6 líneas):
1) Cuenta cuántos dados tuyos tienen la cara que te interesa.
2) Estima cuántos dados de esa cara puede haber entre los dados ajenos (cada dado ajeno tiene una probabilidad de 1/6 de ser esa cara).
3) Decide si la apuesta actual es creíble o exagerada, y si te conviene dudar o subir.
Termina SIEMPRE con la jugada final como un objeto JSON en la ÚLTIMA línea, y no escribas nada después."""

# Estilo agresivo: debería farolear más y dudar menos.
STYLE_AGRESIVO = """ESTILO: jugador agresivo.
Eres un jugador atrevido que juega a presionar. Farolea con frecuencia: apuesta caras que NO tienes
para que los demás crean que las tienes. Sube la apuesta con decisión, sin miedo, y evita dudar salvo
que la apuesta anterior sea claramente imposible. Tu objetivo es que los demás se equivoquen por miedo."""

# Estilo conservador: debería farolear poco y dudar más.
STYLE_CONSERVADOR = """ESTILO: jugador conservador.
Eres un jugador prudente que juega sólo con seguridad. Apuesta únicamente por caras de las que tengas
dados en tu mano o que sean muy probables. No farolees. Si la apuesta anterior te parece exagerada
para el número de dados que hay en la mesa, DUDA sin miedo."""

# Estilo probabilista: pide decidir con cuentas, sin dar un paso a paso largo.
STYLE_PROBABILISTA = """ESTILO: jugador que decide con probabilidades.
Cada dado que no ves tiene 1/6 de probabilidad de mostrar una cara concreta. Si hay N dados que no ves,
lo normal es que haya unos N/6 de cada cara entre ellos. Suma los dados tuyos de esa cara a ese número esperado
y compáralo con la apuesta. Si la apuesta pide bastante más de lo esperado, duda. Si no, haz la subida más segura."""

STYLES = {
    "basic": "",
    "cot": STYLE_COT,
    "agresivo": STYLE_AGRESIVO,
    "conservador": STYLE_CONSERVADOR,
    "probabilista": STYLE_PROBABILISTA

}


def describe_state(obs: Observation) -> str:
    lines = [
        f"Eres {obs.you}. Ronda {obs.round_number}.",
        f"Tus dados: {list(obs.your_dice)}",
        f"Dados vivos por jugador: {json.dumps(obs.dice_counts, ensure_ascii=False)} (total en mesa: {obs.total_dice}).",
        f"Orden de turno desde ti: {list(obs.turn_order)}",
    ]
    if obs.bids_this_round:
        hist = "; ".join(f"{b['player']}: {b['quantity']}x{b['face']}" for b in obs.bids_this_round)
        lines.append(f"Apuestas de esta ronda: {hist}")
        lines.append(f"Apuesta actual: {obs.current_bid.quantity} dados con cara {obs.current_bid.face} (de {obs.current_bidder}).")
    else:
        lines.append("Eres el primero en apostar esta ronda (no puedes dudar).")
    if obs.min_bid is not None:
        lines.append(f"Apuesta legal más baja ahora: cantidad {obs.min_bid.quantity}, cara {obs.min_bid.face} "
                     f"(cualquier apuesta por debajo de esa es ilegal; repetir la actual también).")
    else:
        lines.append("Ya no se puede subir la apuesta: sólo puedes dudar.")
    for r in obs.past_rounds:
        lines.append(
            f"[Ronda {r['round']} ya destapada] apuesta {r['challenged_bid']['quantity']}x{r['challenged_bid']['face']} de {r['bidder']}, "
            f"dudó {r['challenger']}; había {r['real_count']}; perdió un dado {r['loser']}. Dados: {json.dumps(r['revealed_hands'])}"
        )
    return "\n".join(lines)


def build_messages(obs: Observation, feedback: Optional[str] = None, style: str = "basic") -> list[dict]:
    system = RULES + "\n\n" + FORMAT
    extra = STYLES.get(style, "")
    if extra:
        system += "\n\n" + extra
    user = describe_state(obs) + "\n\nTu jugada:"
    if feedback:
        user += "\n\n" + feedback
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]
