import json

import pytest

from liars_dice.game import Referee
from liars_dice.observation import Observation
from liars_dice.parser import parse_response
from liars_dice.players import ProbabilityBot, RandomPlayer
from liars_dice.players.base import Player
from liars_dice.probability import p_bid_true, prob_at_least
from liars_dice.rules import Action, Bid, is_higher, legal_bids, min_legal_bid, resolve_challenge, validate_action


# ---------------------------------------------------------------- reglas
def test_is_higher():
    assert is_higher(Bid(3, 2), None)
    assert is_higher(Bid(4, 1), Bid(3, 6))      # más cantidad, cualquier cara
    assert is_higher(Bid(3, 5), Bid(3, 4))      # misma cantidad, cara mayor
    assert not is_higher(Bid(3, 4), Bid(3, 4))  # igual
    assert not is_higher(Bid(3, 3), Bid(3, 4))  # misma cantidad, cara menor
    assert not is_higher(Bid(2, 6), Bid(3, 1))  # menos cantidad


def test_validate_action():
    prev = Bid(3, 4)
    assert validate_action(Action.dudo(), None, 10) is not None  # no se puede dudar el primero
    assert validate_action(Action.dudo(), prev, 10) is None
    assert validate_action(Action.make_bid(1, 7), None, 10) is not None  # cara 7
    assert validate_action(Action.make_bid(0, 3), None, 10) is not None
    assert validate_action(Action.make_bid(11, 3), None, 10) is not None  # más que dados en mesa
    assert validate_action(Action.make_bid(3, 4), prev, 10) is not None
    assert validate_action(Action.make_bid(3, 5), prev, 10) is None


def test_legal_bids_and_min():
    assert min_legal_bid(None, 4) == Bid(1, 1)
    assert min_legal_bid(Bid(3, 4), 10) == Bid(3, 5)
    assert min_legal_bid(Bid(3, 6), 10) == Bid(4, 1)
    assert min_legal_bid(Bid(4, 6), 4) is None  # ya no se puede subir
    assert all(is_higher(b, Bid(2, 3)) for b in legal_bids(Bid(2, 3), 5))


def test_resolve_challenge():
    hands = [[1, 2, 3], [3, 3, 6]]
    assert resolve_challenge(Bid(3, 3), hands) == (3, True)
    assert resolve_challenge(Bid(4, 3), hands) == (3, False)


def test_probability():
    assert prob_at_least(0, 5) == 1.0
    assert prob_at_least(6, 5) == 0.0
    assert prob_at_least(1, 1) == pytest.approx(1 / 6)
    assert p_bid_true(Bid(2, 3), [3, 3, 1], 5) == 1.0  # ya tengo 2 treses
    assert p_bid_true(Bid(3, 3), [3, 1, 1], 5) == pytest.approx(prob_at_least(2, 5))


# ---------------------------------------------------------------- parser
@pytest.mark.parametrize("txt,expected", [
    ('{"action":"bid","quantity":3,"face":4}', Action.make_bid(3, 4)),
    ('```json\n{"action": "bid", "quantity": "2", "face": 5}\n```', Action.make_bid(2, 5)),
    ('{"action":"dudo"}', Action.dudo()),
    ('{"accion":"apuesta","cantidad":4,"valor":2}', Action.make_bid(4, 2)),
    ('[APUESTA: 3, 4]', Action.make_bid(3, 4)),
    ('[dudo]', Action.dudo()),
    # razonamiento antes de la decisión: gana la ÚLTIMA jugada
    ('Podría decir {"action":"bid","quantity":9,"face":1} pero mejor: {"action":"dudo"}', Action.dudo()),
])
def test_parser_ok(txt, expected):
    r = parse_response(txt)
    assert r.error is None and r.action == expected


@pytest.mark.parametrize("txt", [None, "", "   ", "no sé qué hacer", '{"action":"volar"}',
                                 '{"action":"bid","quantity":"muchos","face":2}', "{rota"])
def test_parser_errors(txt):
    assert parse_response(txt).error is not None


# ---------------------------------------------------------------- árbitro
class Scripted(Player):
    """Devuelve respuestas predefinidas; luego cae a un bot fiable."""
    def __init__(self, name, responses):
        super().__init__(name, "scripted")
        self.responses, self.i, self.fallback = list(responses), 0, ProbabilityBot(name)
        self.feedbacks = []

    def act(self, obs, feedback=None):
        self.feedbacks.append(feedback)
        if self.i < len(self.responses):
            r = self.responses[self.i]
            self.i += 1
            if isinstance(r, Exception):
                raise r
            return r
        return self.fallback.act(obs, feedback)


def test_full_games_terminate_and_conserve_dice():
    for seed in range(30):
        ps = [RandomPlayer("A", seed), ProbabilityBot("B", bluff=0.3, seed=seed), RandomPlayer("C", seed + 1)]
        res = Referee().play(ps, seed=seed)
        assert res.winner in {"A", "B", "C"}
        end = [e for e in res.events if e["type"] == "game_end"][0]
        assert end["rounds"] == res.rounds
        # conservación: dados perdidos = dados iniciales - dados que le quedan al ganador
        last = [e for e in res.events if e["type"] == "challenge"][-1]["dice_counts_after"]
        assert sum(last.values()) == last[res.winner] >= 1
        lost = sum(s["dice_lost_in_challenges"] for s in res.stats.values())
        assert lost == 3 * 5 - last[res.winner]


def test_same_seed_same_dice():
    mk = lambda: [ProbabilityBot("A", seed=1), ProbabilityBot("B", seed=2)]
    a = Referee().play(mk(), seed=42)
    b = Referee().play(mk(), seed=42)
    assert [e for e in a.events if e["type"] == "round_start"] == [e for e in b.events if e["type"] == "round_start"]


def test_invalid_then_retry_with_feedback():
    bad = Scripted("A", ["lo que sea", '{"action":"dudo"}', '{"action":"bid","quantity":1,"face":1}'])
    ps = [bad, ProbabilityBot("B", seed=0)]
    res = Referee(max_retries=2).play(ps, seed=1)
    first_turn = next(e for e in res.events if e["type"] == "turn")
    # 1er intento basura, 2º intento 'dudo' (ilegal el primer turno), 3º legal
    assert len(first_turn["attempts"]) == 3 and not first_turn["forced"]
    assert bad.feedbacks[0] is None and "RECHAZADA" in bad.feedbacks[1]
    assert res.stats["A"]["invalid_responses"] == 2


def test_forced_move_when_retries_exhausted():
    bad = Scripted("A", ["x", "y", RuntimeError("modelo caído")])
    res = Referee(max_retries=2, penalty="forced_move").play([bad, ProbabilityBot("B", seed=0)], seed=1)
    t = next(e for e in res.events if e["type"] == "turn")
    assert t["forced"] and t["action"] == {"action": "bid", "quantity": 1, "face": 1}
    assert "excepción" in t["attempts"][2]["error"]
    assert res.stats["A"]["forced_moves"] >= 1


def test_lose_die_penalty_still_terminates():
    bad = Scripted("A", ["x"] * 3)
    res = Referee(max_retries=2, penalty="lose_die").play([bad, ProbabilityBot("B", seed=0)], seed=3)
    assert res.stats["A"]["penalty_dice_lost"] >= 1
    assert any(e["type"] == "penalty" for e in res.events)


def test_observation_never_contains_other_hands():
    seen = []

    class Spy(ProbabilityBot):
        def act(self, obs, feedback=None):
            seen.append(obs)
            return super().act(obs, feedback)

    Referee().play([Spy("A", seed=1), ProbabilityBot("B", seed=2), ProbabilityBot("C", seed=3)], seed=5)
    assert seen
    for obs in seen:
        assert isinstance(obs, Observation)
        d = obs.to_dict()
        assert len(d["your_dice"]) == d["dice_counts"][obs.you]
        assert "hands" not in d  # la ronda en curso no expone manos ajenas
        for pr in d["past_rounds"]:  # sólo rondas ya destapadas
            assert pr["round"] < obs.round_number


def test_cannot_bid_more_than_table_dice_and_no_dudo_first():
    # 2 jugadores x 1 dado = 2 dados en mesa
    bad = Scripted("A", ['{"action":"dudo"}', '{"action":"bid","quantity":3,"face":1}'])
    res = Referee(dice_per_player=1, max_retries=1).play([bad, ProbabilityBot("B", seed=0)], seed=1)
    t = next(e for e in res.events if e["type"] == "turn")
    assert t["forced"]


# ------------------------------------------------- OllamaPlayer contra un servidor falso
def test_ollama_player_against_fake_server():
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    from liars_dice.players import OllamaPlayer

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            assert body["model"] == "fake" and body["messages"][0]["role"] == "system"
            out = json.dumps({"message": {"content": 'Pienso... {"action":"bid","quantity":1,"face":2}'}}).encode()
            self.send_response(200); self.send_header("Content-Length", str(len(out))); self.end_headers()
            self.wfile.write(out)

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        p = OllamaPlayer("A", "fake", host=f"http://127.0.0.1:{srv.server_port}")
        res = Referee(dice_per_player=2).play([p, ProbabilityBot("B", seed=0)], seed=1)
        t = next(e for e in res.events if e["type"] == "turn")
        assert t["action"] == {"action": "bid", "quantity": 1, "face": 2} and not t["forced"]
    finally:
        srv.shutdown()
