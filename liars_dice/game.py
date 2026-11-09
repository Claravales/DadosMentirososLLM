"""El árbitro: reparte dados, da turnos, valida jugadas, resuelve dudas y registra todo."""
from __future__ import annotations

import copy
import random
import time
from dataclasses import dataclass, field
from typing import Callable, Optional, Sequence

from .observation import Observation
from .parser import parse_response
from .players.base import InfrastructureError, Player, PlayerReply
from .probability import p_bid_true
from .rules import Action, Bid, min_legal_bid, resolve_challenge, validate_action

PENALTIES = ("forced_move", "lose_die")

# Versión del formato de los logs jsonl. Súbela cuando un cambio ROMPA a quien los lee
# (quitar/renombrar un campo o cambiar su significado). Añadir campos nuevos no la sube.
SCHEMA_VERSION = 1


@dataclass
class GameResult:
    game_id: str
    seed: Optional[int]
    players: list  # [{"name","spec"}] en orden de asiento
    winner: Optional[str]
    rounds: int
    stats: dict  # por jugador
    events: list = field(default_factory=list)
    aborted: Optional[str] = None  # motivo si la partida se abortó por fallo de infraestructura


class Referee:
    """
    Parámetros
    ----------
    dice_per_player : dados iniciales por jugador.
    max_retries     : reintentos tras una respuesta inválida (total intentos = 1 + max_retries).
    penalty         : qué pasa si se agotan los reintentos
        "forced_move" -> el árbitro juega por él la apuesta legal más baja (o dudar si ya no hay subida).
        "lose_die"    -> además de la jugada forzada, pierde un dado al cerrarse la ronda.
    history_rounds  : cuántas rondas pasadas (ya destapadas) ve cada jugador.
    """

    def __init__(
        self,
        dice_per_player: int = 5,
        max_retries: int = 2,
        penalty: str = "forced_move",
        history_rounds: int = 3,
    ):
        if penalty not in PENALTIES:
            raise ValueError(f"penalty debe ser una de {PENALTIES}")
        self.dice_per_player = dice_per_player
        self.max_retries = max_retries
        self.penalty = penalty
        self.history_rounds = history_rounds

    # ------------------------------------------------------------------ API
    def play(
        self,
        players: Sequence[Player],
        seed: Optional[int] = None,
        game_id: str = "game",
        on_event: Optional[Callable[[dict], None]] = None,
    ) -> GameResult:
        names = [p.name for p in players]
        if len(players) < 2:
            raise ValueError("Hacen falta al menos 2 jugadores.")
        if len(set(names)) != len(names):
            raise ValueError("Los nombres de los jugadores deben ser únicos.")

        by_name = {p.name: p for p in players}
        rng = random.Random(seed)
        dice_left = {n: self.dice_per_player for n in names}
        stats = {
            n: {"bids": 0, "dudos": 0, "invalid_responses": 0, "retries": 0, "forced_moves": 0,
                "penalty_dice_lost": 0, "dice_lost_in_challenges": 0, "infra_retries": 0}
            for n in names
        }
        events: list[dict] = []

        def emit(ev: dict) -> None:
            ev = {"game_id": game_id, **ev}
            events.append(ev)
            if on_event:
                on_event(ev)

        def infra_sink(player_name: str, rnd: int, turn: int):
            """Registra incidencias de infraestructura (p. ej. reintento tras HTTP 500) como eventos."""
            def sink(evs: list) -> None:
                for ev in evs:
                    emit({"type": "infrastructure", "round": rnd, "turn": turn, "player": player_name, **ev})
                    if ev.get("kind") == "retry":
                        stats[player_name]["infra_retries"] += 1
            return sink

        def alive() -> list[str]:
            return [n for n in names if dice_left[n] > 0]

        def next_alive(after: str) -> str:
            i = names.index(after)
            for k in range(1, len(names) + 1):
                cand = names[(i + k) % len(names)]
                if dice_left[cand] > 0:
                    return cand
            raise RuntimeError("sin jugadores vivos")

        for p in players:
            p.on_game_start()
        emit({"type": "game_start", "schema_version": SCHEMA_VERSION, "seed": seed,
              "dice_per_player": self.dice_per_player,
              "max_retries": self.max_retries, "penalty": self.penalty,
              "history_rounds": self.history_rounds,
              "players": [{"name": p.name, "spec": p.spec, "params": p.describe()} for p in players]})

        past_rounds: list[dict] = []
        starter = names[0]
        round_no = 0

        aborted: Optional[str] = None
        try:
            # Termina siempre: cada ronda quita >=1 dado y las apuestas sólo suben (acotadas).
            while len(alive()) > 1:
                round_no += 1
                hands = {n: sorted(rng.randint(1, 6) for _ in range(dice_left[n])) for n in alive()}
                total_dice = sum(dice_left.values())
                emit({"type": "round_start", "round": round_no, "starter": starter,
                      "dice_counts": {n: dice_left[n] for n in alive()},
                      "hands": hands})  # los dados se guardan en el LOG (análisis), nunca se envían a otros

                bids: list[dict] = []
                pending_penalties: list[str] = []
                current = starter
                turn_idx = 0
                while True:
                    turn_idx += 1
                    prev = Bid(bids[-1]["quantity"], bids[-1]["face"]) if bids else None
                    order = tuple(self._turn_order(names, dice_left, current))
                    obs = Observation(
                        you=current,
                        your_dice=tuple(hands[current]),
                        round_number=round_no,
                        turn_order=order,
                        dice_counts={n: dice_left[n] for n in alive()},
                        total_dice=total_dice,
                        current_bid=prev,
                        current_bidder=bids[-1]["player"] if bids else None,
                        bids_this_round=tuple(dict(b) for b in bids),
                        past_rounds=tuple(copy.deepcopy(past_rounds[-self.history_rounds:])) if self.history_rounds else (),
                        can_dudo=prev is not None,
                        min_bid=min_legal_bid(prev, total_dice),
                    )
                    action, attempts, forced = self._ask(by_name[current], obs, prev, total_dice, stats[current],
                                                         infra_sink(current, round_no, turn_idx))

                    if forced and self.penalty == "lose_die":
                        pending_penalties.append(current)  # se aplica al terminar la ronda

                    own = list(hands[current])
                    turn_ev = {
                        "type": "turn", "round": round_no, "turn": turn_idx, "player": current,
                        "your_dice": own, "current_bid": prev.as_dict() if prev else None,
                        "attempts": attempts, "forced": forced, "action": action.as_dict(),
                    }
                    if action.kind == "bid":
                        b = action.bid
                        turn_ev["own_count_of_face"] = own.count(b.face)
                        turn_ev["p_bid_true"] = round(p_bid_true(b, own, total_dice - len(own)), 6)
                    else:
                        turn_ev["p_challenged_bid_true"] = round(p_bid_true(prev, own, total_dice - len(own)), 6)
                    emit(turn_ev)

                    if action.kind == "bid":
                        stats[current]["bids"] += 1
                        bids.append({"player": current, "quantity": action.bid.quantity, "face": action.bid.face})
                        current = next_alive(current)
                        continue

                    # ---- duda: resolver
                    stats[current]["dudos"] += 1
                    bidder = bids[-1]["player"]
                    challenged = Bid(bids[-1]["quantity"], bids[-1]["face"])
                    real, was_true = resolve_challenge(challenged, list(hands.values()))
                    loser = current if was_true else bidder
                    dice_left[loser] = max(0, dice_left[loser] - 1)
                    stats[loser]["dice_lost_in_challenges"] += 1
                    round_rec = {
                        "round": round_no, "bids": [dict(b) for b in bids], "challenger": current,
                        "bidder": bidder, "challenged_bid": challenged.as_dict(), "real_count": real,
                        "bid_was_true": was_true, "loser": loser,
                        "revealed_hands": {n: list(h) for n, h in hands.items()},
                    }
                    past_rounds.append(copy.deepcopy(round_rec))  # copia aislada de lo emitido/entregado
                    emit({"type": "challenge", **round_rec,
                          "dice_counts_after": {n: dice_left[n] for n in names}})
                    if dice_left[loser] == 0:
                        emit({"type": "eliminated", "round": round_no, "player": loser})
                    # penalizaciones por jugadas forzadas (modo lose_die), aplicadas al cerrar la ronda
                    for pn in pending_penalties:
                        if dice_left[pn] > 0:
                            dice_left[pn] -= 1
                            stats[pn]["penalty_dice_lost"] += 1
                            emit({"type": "penalty", "round": round_no, "player": pn,
                                  "dice_left": dice_left[pn]})
                            if dice_left[pn] == 0:
                                emit({"type": "eliminated", "round": round_no, "player": pn})
                    break

                if len(alive()) > 1:
                    starter = loser if dice_left[loser] > 0 else next_alive(loser)

        except InfrastructureError as exc:
            aborted = str(exc)
            emit({"type": "game_aborted", "round": round_no, "reason": aborted})

        winner = alive()[0] if alive() and aborted is None else None
        emit({"type": "game_end", "winner": winner, "rounds": round_no, "stats": stats,
              "aborted": aborted})
        return GameResult(game_id, seed, [{"name": p.name, "spec": p.spec} for p in players],
                          winner, round_no, stats, events, aborted)

    # ------------------------------------------------------------ internals
    @staticmethod
    def _turn_order(names, dice_left, first) -> list[str]:
        i = names.index(first)
        rot = names[i:] + names[:i]
        return [n for n in rot if dice_left[n] > 0]

    @staticmethod
    def _rejection_feedback(error: Optional[str], prev: Optional[Bid], total_dice: int) -> str:
        """Mensaje que recibe el modelo tras una respuesta rechazada: motivo + qué sí es legal."""
        mb = min_legal_bid(prev, total_dice)
        if mb is None:
            legal = "Ya no es posible subir la apuesta: sólo puedes dudar."
        else:
            legal = f"La apuesta legal más baja ahora es {mb.quantity}x{mb.face} (cantidad {mb.quantity}, cara {mb.face})"
            legal += " (o puedes dudar)." if prev is not None else "."
        return f"Tu respuesta anterior fue RECHAZADA: {error} {legal} Responde de nuevo con una jugada legal."

    def _ask(self, player: Player, obs: Observation, prev: Optional[Bid], total_dice: int, st: dict,
             infra_sink: Optional[Callable[[list], None]] = None):
        """Pide jugada con reintentos. Devuelve (Action, intentos_log, forzada?).
        `infra_sink` recibe las incidencias de infraestructura que declare el jugador."""
        def infra(events) -> None:
            if infra_sink and events:
                infra_sink([e for e in events if isinstance(e, dict)])

        attempts: list[dict] = []
        feedback: Optional[str] = None
        for _ in range(self.max_retries + 1):
            t0 = time.perf_counter()
            raw: Optional[str] = None
            meta: dict = {}
            error: Optional[str] = None
            action: Optional[Action] = None
            try:
                reply = player.act(obs, feedback)
            except InfrastructureError as exc:
                infra(getattr(exc, "events", None))
                raise  # servidor caído/mal configurado: aborta la partida, no es culpa del modelo
            except Exception as exc:  # timeout, bug del jugador...: cuenta como respuesta inválida
                infra(getattr(exc, "events", None))
                error = f"El jugador lanzó una excepción: {type(exc).__name__}: {exc}"
            else:
                if isinstance(reply, PlayerReply):  # str o PlayerReply: los dos son válidos
                    raw, meta = reply.text, dict(reply.meta or {})
                    infra(meta.pop("infra_events", None))  # va al log como evento, no dentro de attempts
                else:
                    raw = reply
                res = parse_response(raw)
                if res.error:
                    error = res.error
                    if not str(raw or "").strip() and meta.get("empty_reason"):
                        error = f"Respuesta vacía: {meta['empty_reason']}"
                else:
                    error = validate_action(res.action, prev, total_dice)
                    if error is None:
                        action = res.action
            attempts.append({"raw": raw, "error": error,
                             "latency_s": round(time.perf_counter() - t0, 4), "meta": meta})
            if action is not None:
                st["retries"] += len(attempts) - 1
                return action, attempts, False
            st["invalid_responses"] += 1
            feedback = self._rejection_feedback(error, prev, total_dice)
        # reintentos agotados -> jugada forzada
        st["retries"] += len(attempts) - 1
        st["forced_moves"] += 1
        mb = min_legal_bid(prev, total_dice)
        forced_action = Action.make_bid(mb.quantity, mb.face) if mb else Action.dudo()
        return forced_action, attempts, True
