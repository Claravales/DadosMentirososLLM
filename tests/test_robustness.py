"""Tests de robustez (ronda 1): parser, fallos de infraestructura, aislamiento y logs."""
import csv
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

import run_games
from liars_dice.game import Referee
from liars_dice.parser import parse_response
from liars_dice.players import OllamaPlayer, ProbabilityBot
from liars_dice.players.base import InfrastructureError, Player
from liars_dice.rules import Action


# ---------------------------------------------------------------- parser (2.1 y 2.2)
def test_parser_nested_json_object():
    # fallaba: la regex \{[^{}]*\} no admitía objetos anidados
    r = parse_response('{"action":"bid","quantity":3,"face":4,"extra":{"a":1}}')
    assert r.error is None and r.action == Action.make_bid(3, 4)


def test_parser_valid_move_not_overridden_by_later_malformed_one():
    # fallaba: la jugada mal formada posterior anulaba la buena anterior
    r = parse_response('{"action":"bid","quantity":3,"face":4} luego {"action":"bid"}')
    assert r.error is None and r.action == Action.make_bid(3, 4)


def test_parser_action_nested_inside_other_object():
    r = parse_response('{"decision": {"action": "dudo"}}')
    assert r.error is None and r.action == Action.dudo()


def test_parser_ignores_closed_think_block():
    txt = '<think>Quizá {"action":"bid","quantity":9,"face":1}... mejor dudar</think>{"action":"dudo"}'
    assert parse_response(txt).action == Action.dudo()


def test_parser_think_block_with_only_draft_moves_is_not_executed():
    # truncada por num_predict: el borrador NO debe ejecutarse
    r = parse_response('<think>Podría apostar {"action":"bid","quantity":2,"face":3} pero antes')
    assert r.action is None and "razonamiento" in r.error


def test_parser_think_closing_tag_without_opening():
    txt = 'Razono: {"action":"bid","quantity":9,"face":1} no.</think>\n{"action":"bid","quantity":2,"face":5}'
    assert parse_response(txt).action == Action.make_bid(2, 5)


def test_parser_think_closed_but_no_answer():
    assert parse_response("<think>hmm</think>").error is not None


# ---------------------------------------------------------------- infraestructura (2.4)
class Dead(Player):
    """Simula Ollama caído desde el primer turno."""
    def __init__(self, name):
        super().__init__(name, "dead")

    def act(self, obs, feedback=None):
        raise InfrastructureError("Ollama caído")


def test_referee_aborts_game_on_infrastructure_error():
    res = Referee().play([Dead("A"), ProbabilityBot("B", seed=0)], seed=1)
    assert res.aborted == "Ollama caído"
    assert res.winner is None
    types = [e["type"] for e in res.events]
    assert "game_aborted" in types and types[-1] == "game_end"
    assert res.stats["A"]["forced_moves"] == 0 and res.stats["A"]["invalid_responses"] == 0
    assert not any(e["type"] == "turn" for e in res.events)  # no se inventó ninguna jugada forzada


def _server(handler_cls):
    srv = HTTPServer(("127.0.0.1", 0), handler_cls)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def _obs():
    seen = []

    class Spy(ProbabilityBot):
        def act(self, obs, feedback=None):
            seen.append(obs)
            return super().act(obs, feedback)

    Referee(dice_per_player=1).play([Spy("A", seed=1), ProbabilityBot("B", seed=2)], seed=1)
    return seen[0]


def test_ollama_http_404_is_infrastructure_error():
    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            out = b'{"error":"model not found"}'
            self.send_response(404); self.send_header("Content-Length", str(len(out))); self.end_headers()
            self.wfile.write(out)

        def log_message(self, *a):
            pass

    srv = _server(H)
    try:
        p = OllamaPlayer("A", "nope", host=f"http://127.0.0.1:{srv.server_port}")
        with pytest.raises(InfrastructureError, match="404"):
            p.act(_obs())
    finally:
        srv.shutdown()


def test_ollama_connection_refused_is_infrastructure_error():
    srv = _server(BaseHTTPRequestHandler)
    port = srv.server_port
    srv.shutdown(); srv.server_close()  # el puerto queda cerrado
    p = OllamaPlayer("A", "x", host=f"http://127.0.0.1:{port}", timeout=2)
    with pytest.raises(InfrastructureError):
        p.act(_obs())


def test_ollama_slow_response_is_model_failure_not_infrastructure():
    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            time.sleep(0.6)

        def log_message(self, *a):
            pass

    srv = _server(H)
    try:
        p = OllamaPlayer("A", "slow", host=f"http://127.0.0.1:{srv.server_port}", timeout=0.2)
        with pytest.raises(RuntimeError) as ei:
            p.act(_obs())
        assert not isinstance(ei.value, InfrastructureError)
    finally:
        srv.shutdown()


def test_ollama_empty_content_returns_empty_string():
    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            out = b'{"message":{"content":""}}'
            self.send_response(200); self.send_header("Content-Length", str(len(out))); self.end_headers()
            self.wfile.write(out)

        def log_message(self, *a):
            pass

    srv = _server(H)
    try:
        p = OllamaPlayer("A", "m", host=f"http://127.0.0.1:{srv.server_port}")
        assert p.act(_obs()).text == ""
    finally:
        srv.shutdown()


# ---------------------------------------------------------------- aislamiento (deepcopy)
def test_mutating_observation_cannot_corrupt_referee_state_or_log():
    class Mutator(ProbabilityBot):
        def act(self, obs, feedback=None):
            for r in obs.past_rounds:
                r["real_count"] = 999
                r["revealed_hands"].clear()
                r["bids"].clear()
            return super().act(obs, feedback)

    def run(cls):
        ps = [cls("A", seed=1), ProbabilityBot("B", seed=2), ProbabilityBot("C", seed=3)]
        return Referee(history_rounds=5).play(ps, seed=7)

    clean = [e for e in run(ProbabilityBot).events if e["type"] == "challenge"]
    dirty = [e for e in run(Mutator).events if e["type"] == "challenge"]
    assert len(clean) > 2
    assert clean == dirty


# ---------------------------------------------------------------- run_games (2.5)
class Chatty(Player):
    """Responde con caracteres fuera de cp1252 (la codificación por defecto en Windows)."""
    def __init__(self, name, spec="chatty"):
        super().__init__(name, spec)
        self.inner = ProbabilityBot(name)

    def act(self, obs, feedback=None):
        return "Pienso → 🎲 así: " + self.inner.act(obs, feedback)


def _read_rows(out_dir):
    with open(out_dir / "games.csv", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _only_run_dir(tmp_path):
    (d,) = [p for p in tmp_path.iterdir() if p.is_dir()]
    return d


def test_run_games_writes_utf8_logs(tmp_path, monkeypatch):
    monkeypatch.setattr(run_games, "make_player", lambda spec, name, seed=None: Chatty(name, spec))
    code = run_games.main(["--players", "a", "b", "--games", "2", "--out", str(tmp_path), "--quiet"])
    assert code == 0
    d = _only_run_dir(tmp_path)
    raw = (d / "game_0001.jsonl").read_text(encoding="utf-8")
    assert "→" in raw and "🎲" in raw
    assert [r["status"] for r in _read_rows(d)] == ["ok", "ok"]


def test_run_games_stops_on_infrastructure_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(run_games, "make_player",
                        lambda spec, name, seed=None: Dead(name) if spec == "dead" else ProbabilityBot(name, seed=seed))
    code = run_games.main(["--players", "dead", "probbot", "--games", "5", "--out", str(tmp_path), "--quiet"])
    assert code == 2
    rows = _read_rows(_only_run_dir(tmp_path))
    assert len(rows) == 1 and rows[0]["status"] == "aborted" and "Ollama caído" in rows[0]["detail"]
    assert rows[0]["winner_name"] == ""  # no cuenta como victoria de nadie


def test_run_games_unexpected_error_does_not_kill_the_run(tmp_path, monkeypatch):
    calls = {"n": 0}

    def flaky(spec, name, seed=None):
        if name in ("check", "warmup"):  # chequeo previo y calentamiento de run_games: siempre bien
            return ProbabilityBot(name)
        calls["n"] += 1
        if calls["n"] == 1:  # la 1ª partida falla al construir su primer jugador
            raise ValueError("spec rota")
        return ProbabilityBot(name, seed=seed)

    monkeypatch.setattr(run_games, "make_player", flaky)
    code = run_games.main(["--players", "x", "y", "--games", "2", "--out", str(tmp_path), "--quiet"])
    assert code == 1
    d = _only_run_dir(tmp_path)
    assert [r["status"] for r in _read_rows(d)] == ["error", "ok"]
    assert "spec rota" in (d / "game_0001.error.txt").read_text(encoding="utf-8")


def test_run_games_flushes_events_per_line(tmp_path, monkeypatch):
    # si la partida revienta a mitad, el log parcial ya está en disco
    class Boom(Player):
        def __init__(self, name):
            super().__init__(name, "boom")
            self.n = 0

        def act(self, obs, feedback=None):
            self.n += 1
            if self.n > 1:
                raise KeyboardInterrupt  # no es Exception: se propaga como un fallo grave
            return '{"action":"bid","quantity":1,"face":1}'

    monkeypatch.setattr(run_games, "make_player",
                        lambda spec, name, seed=None: Boom(name) if spec == "boom" else ProbabilityBot(name, seed=seed))
    with pytest.raises(KeyboardInterrupt):
        run_games.main(["--players", "boom", "probbot", "--games", "1", "--out", str(tmp_path), "--quiet"])
    d = _only_run_dir(tmp_path)
    lines = (d / "game_0001.jsonl").read_text(encoding="utf-8").splitlines()
    assert json.loads(lines[0])["type"] == "game_start" and len(lines) >= 3


@pytest.mark.parametrize("bad", [["--dice", "0"], ["--retries", "-1"], ["--games", "0"]])
def test_run_games_validates_arguments(tmp_path, bad):
    with pytest.raises(SystemExit):
        run_games.main(["--players", "random", "probbot", "--out", str(tmp_path), *bad])


def test_run_games_two_runs_same_second_do_not_overwrite(tmp_path, monkeypatch):
    monkeypatch.setattr(run_games.time, "strftime", lambda fmt: "20260101-000000")
    for _ in range(2):
        run_games.main(["--players", "random", "probbot", "--games", "1", "--out", str(tmp_path), "--quiet"])
    assert len([p for p in tmp_path.iterdir() if p.is_dir()]) == 2
