"""Tests del calentamiento del modelo y del reintento único ante HTTP 500 de Ollama."""
import csv
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

import run_games
from liars_dice.game import Referee
from liars_dice.observation import Observation
from liars_dice.players import OllamaPlayer, ProbabilityBot, make_player
from liars_dice.players.base import InfrastructureError, Player, PlayerReply
from liars_dice.rules import Bid

CRASH = '{"error":"llama-server process has terminated: exit status 0xc0000409"}'
OK = {"message": {"role": "assistant", "content": '{"action":"bid","quantity":1,"face":1}'},
      "done_reason": "stop", "load_duration": 2_000_000_000}


def scripted_server(statuses):
    """Servidor falso: responde con los códigos de `statuses` en orden (200 -> OK); luego repite el último."""
    state = {"calls": 0, "bodies": []}

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            state["bodies"].append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            code = statuses[min(state["calls"], len(statuses) - 1)]
            state["calls"] += 1
            out = (json.dumps(OK) if code == 200 else CRASH if code == 500 else '{"error":"nope"}').encode()
            self.send_response(code); self.send_header("Content-Length", str(len(out))); self.end_headers()
            self.wfile.write(out)

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, state, f"http://127.0.0.1:{srv.server_port}"


def obs():
    return Observation(you="A", your_dice=(1,), round_number=1, turn_order=("A", "B"), dice_counts={"A": 1, "B": 1},
                       total_dice=2, current_bid=None, current_bidder=None, bids_this_round=(),
                       can_dudo=False, min_bid=Bid(1, 1))


def player(host, **kw):
    return OllamaPlayer("A", "m", host=host, retry_wait=0, **kw)


# ---------------------------------------------------------------- reintento único ante HTTP 500
def test_500_then_ok_retries_once_and_reports_recovered_event():
    srv, st, host = scripted_server([500, 200])
    try:
        reply = player(host).act(obs())
    finally:
        srv.shutdown()
    assert st["calls"] == 2
    assert reply.text == OK["message"]["content"]
    (ev,) = reply.meta["infra_events"]
    assert ev["kind"] == "retry" and ev["http_status"] == 500 and ev["recovered"] is True
    assert "llama-server" in ev["detail"]


def test_500_twice_aborts_after_exactly_one_retry():
    srv, st, host = scripted_server([500])
    try:
        with pytest.raises(InfrastructureError, match="HTTP 500") as ei:
            player(host).act(obs())
    finally:
        srv.shutdown()
    assert st["calls"] == 2  # un intento + un reintento, no más
    (ev,) = ei.value.events
    assert ev["kind"] == "retry" and ev["recovered"] is False


def test_other_http_errors_are_not_retried():
    srv, st, host = scripted_server([404])
    try:
        with pytest.raises(InfrastructureError, match="404") as ei:
            player(host).act(obs())
    finally:
        srv.shutdown()
    assert st["calls"] == 1 and ei.value.events == []


def test_clean_response_has_no_infra_events():
    srv, st, host = scripted_server([200])
    try:
        reply = player(host).act(obs())
    finally:
        srv.shutdown()
    assert st["calls"] == 1 and "infra_events" not in reply.meta


def test_retry_wait_is_applied(monkeypatch):
    slept = []
    monkeypatch.setattr("liars_dice.players.ollama_player.time.sleep", slept.append)
    srv, _, host = scripted_server([500, 200])
    try:
        OllamaPlayer("A", "m", host=host, retry_wait=1.5).act(obs())
    finally:
        srv.shutdown()
    assert slept == [1.5]


def test_retry_options_in_spec():
    p = make_player("ollama:m?retry_wait=0.5+warmup_timeout=90", "J1")
    assert (p.retry_wait, p.warmup_timeout) == (0.5, 90.0)
    for bad in ("ollama:m?retry_wait=-1", "ollama:m?warmup_timeout=0"):
        with pytest.raises(ValueError):
            make_player(bad, "J1")


# ---------------------------------------------------------------- el árbitro registra el reintento en el log
def test_referee_logs_recovered_retry_as_infrastructure_event_and_game_continues():
    srv, st, host = scripted_server([500, 200])
    try:
        # dice_per_player=1 -> partida corta; el modelo falso siempre apuesta 1x1 (legal sólo la 1ª vez, el resto se fuerza)
        res = Referee(dice_per_player=1, max_retries=0).play([player(host), ProbabilityBot("B", seed=0)], seed=1)
    finally:
        srv.shutdown()
    infra = [e for e in res.events if e["type"] == "infrastructure"]
    assert len(infra) == 1
    ev = infra[0]
    assert (ev["player"], ev["round"], ev["turn"], ev["kind"], ev["recovered"]) == ("A", 1, 1, "retry", True)
    assert res.aborted is None and res.winner is not None
    assert res.stats["A"]["infra_retries"] == 1 and res.stats["B"]["infra_retries"] == 0
    # el evento va ANTES del turno al que pertenece, y no ensucia attempts
    types = [e["type"] for e in res.events]
    assert types.index("infrastructure") < types.index("turn")
    turn = next(e for e in res.events if e["type"] == "turn")
    assert "infra_events" not in turn["attempts"][0]["meta"]


def test_referee_logs_failed_retry_before_game_aborted():
    srv, st, host = scripted_server([500])
    try:
        res = Referee(dice_per_player=1).play([player(host), ProbabilityBot("B", seed=0)], seed=1)
    finally:
        srv.shutdown()
    assert res.aborted and "HTTP 500" in res.aborted and res.winner is None
    types = [e["type"] for e in res.events]
    assert types[-3:] == ["infrastructure", "game_aborted", "game_end"]
    ev = next(e for e in res.events if e["type"] == "infrastructure")
    assert ev["recovered"] is False and ev["http_status"] == 500
    assert not any(e["type"] == "turn" for e in res.events)  # ninguna jugada forzada inventada


# ---------------------------------------------------------------- calentamiento del modelo (OllamaPlayer)
def test_warmup_sends_a_tiny_request_and_reports_event():
    srv, st, host = scripted_server([200])
    try:
        events = player(host, think=False).warmup()
    finally:
        srv.shutdown()
    assert st["calls"] == 1
    body = st["bodies"][0]
    assert body["options"] == {"temperature": 0, "num_predict": 1} and body["think"] is False
    assert body["stream"] is False and body["model"] == "m"
    (ev,) = events
    assert ev["kind"] == "warmup" and ev["ok"] is True and ev["load_s"] == 2.0 and ev["seconds"] >= 0


def test_warmup_recovers_from_a_single_500():
    srv, st, host = scripted_server([500, 200])
    try:
        events = player(host).warmup()
    finally:
        srv.shutdown()
    assert [e["kind"] for e in events] == ["retry", "warmup"] and events[0]["recovered"] is True


def test_warmup_fails_with_infrastructure_error_and_events():
    srv, st, host = scripted_server([500])
    try:
        with pytest.raises(InfrastructureError) as ei:
            player(host).warmup()
    finally:
        srv.shutdown()
    assert [e["kind"] for e in ei.value.events] == ["retry", "warmup"]
    assert ei.value.events[-1]["ok"] is False and st["calls"] == 2


def test_warmup_timeout_is_infrastructure_not_model_failure():
    class Slow(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            import time
            time.sleep(0.6)

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), Slow)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        p = OllamaPlayer("A", "m", host=f"http://127.0.0.1:{srv.server_port}", timeout=0.1, warmup_timeout=0.2,
                         retry_wait=0)
        with pytest.raises(InfrastructureError, match="calentamiento") as ei:
            p.warmup()
    finally:
        srv.shutdown()
    assert ei.value.events[-1]["kind"] == "warmup" and ei.value.events[-1]["ok"] is False


def test_bots_have_a_noop_warmup():
    assert ProbabilityBot("A").warmup() == []


# ---------------------------------------------------------------- run_games: calentamiento antes de la 1ª partida
class Warm(Player):
    calls: list = []
    result = None  # excepción a lanzar, o None

    def __init__(self, name, spec="warm"):
        super().__init__(name, spec)
        self.inner = ProbabilityBot(name)

    def act(self, obs, feedback=None):
        return self.inner.act(obs, feedback)

    def warmup(self):
        Warm.calls.append(self.spec)
        if Warm.result:
            raise Warm.result
        return [{"kind": "warmup", "ok": True, "seconds": 0.5}]


@pytest.fixture
def warm(monkeypatch):
    Warm.calls, Warm.result = [], None
    monkeypatch.setattr(run_games, "make_player",
                        lambda spec, name, seed=None: Warm(name, spec) if name != "check" else ProbabilityBot(name))
    return Warm


def _run_dir(tmp):
    (d,) = [p for p in tmp.iterdir() if p.is_dir()]
    return d


def test_run_games_warms_each_distinct_spec_once_before_the_games(tmp_path, warm):
    code = run_games.main(["--players", "m1", "m2", "m1", "--games", "2", "--out", str(tmp_path), "--quiet"])
    assert code == 0 and warm.calls == ["m1", "m2"]  # m1 repetido, se calienta una sola vez
    d = _run_dir(tmp_path)
    lines = [json.loads(line) for line in (d / "infrastructure.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [(e["spec"], e["kind"], e["ok"]) for e in lines] == [("m1", "warmup", True), ("m2", "warmup", True)]


def test_run_games_no_warmup_flag(tmp_path, warm):
    assert run_games.main(["--players", "m1", "m2", "--games", "1", "--out", str(tmp_path), "--quiet",
                           "--no-warmup"]) == 0
    assert warm.calls == [] and not (_run_dir(tmp_path) / "infrastructure.jsonl").exists()


def test_run_games_failed_warmup_stops_before_any_game(tmp_path, warm):
    err = InfrastructureError("servidor roto")
    err.events = [{"kind": "retry", "http_status": 500, "recovered": False},
                  {"kind": "warmup", "ok": False, "detail": "servidor roto"}]
    warm.result = err
    code = run_games.main(["--players", "m1", "m2", "--games", "3", "--out", str(tmp_path), "--quiet"])
    assert code == 2
    d = _run_dir(tmp_path)
    assert not list(d.glob("game_*.jsonl")) and not (d / "games.csv").exists()
    kinds = [json.loads(line)["kind"] for line in (d / "infrastructure.jsonl").read_text(encoding="utf-8").splitlines()]
    assert kinds == ["retry", "warmup"]
    assert warm.calls == ["m1"]  # se detiene en el primero que falla


def test_run_games_failed_warmup_without_events_still_logs_failure(tmp_path, warm):
    warm.result = InfrastructureError("sin eventos")
    assert run_games.main(["--players", "m1", "m2", "--out", str(tmp_path), "--quiet"]) == 2
    (ev,) = [json.loads(line) for line in
             (_run_dir(tmp_path) / "infrastructure.jsonl").read_text(encoding="utf-8").splitlines()]
    assert ev["kind"] == "warmup" and ev["ok"] is False and "sin eventos" in ev["detail"]


def test_run_games_bots_only_run_writes_empty_infrastructure_log(tmp_path):
    assert run_games.main(["--players", "random", "probbot", "--games", "1", "--out", str(tmp_path), "--quiet"]) == 0
    d = _run_dir(tmp_path)
    assert (d / "infrastructure.jsonl").read_text(encoding="utf-8") == ""
    with open(d / "games.csv", newline="", encoding="utf-8") as f:
        assert [r["status"] for r in csv.DictReader(f)] == ["ok"]
