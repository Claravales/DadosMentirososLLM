"""Tests del contrato entre miembros (ronda 2): PlayerReply, feedback, prompts, spec de
Ollama, schema del log y log de ejemplo. Si alguno falla tras un cambio, el contrato se ha
roto: o se arregla el cambio o se sube SCHEMA_VERSION y se regenera examples/sample_run."""
import csv
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

import run_games
from liars_dice import prompts
from liars_dice.game import SCHEMA_VERSION, Referee
from liars_dice.observation import Observation
from liars_dice.players import OllamaPlayer, ProbabilityBot, make_player, split_spec
from liars_dice.players.base import Player, PlayerReply
from liars_dice.rules import Bid

ROOT = Path(__file__).resolve().parent.parent
BID11 = '{"action":"bid","quantity":1,"face":1}'


class Rec(Player):
    """Devuelve respuestas predefinidas (str o PlayerReply), recuerda el feedback y luego cae a un bot."""
    def __init__(self, name, responses=(), params=None):
        super().__init__(name, "rec")
        self.responses, self.i, self.feedbacks = list(responses), 0, []
        self.fallback, self.params = ProbabilityBot(name), params

    def describe(self):
        return self.params if self.params is not None else super().describe()

    def act(self, obs, feedback=None):
        self.feedbacks.append(feedback)
        if self.i < len(self.responses):
            self.i += 1
            return self.responses[self.i - 1]
        return self.fallback.act(obs, feedback)


def first_turn(res):
    return next(e for e in res.events if e["type"] == "turn")


# ---------------------------------------------------------------- PlayerReply + meta en attempts
def test_player_reply_meta_is_stored_in_every_attempt():
    bad = Rec("A", [PlayerReply("basura", {"k": 1}), PlayerReply(BID11, {"k": 2, "thinking": "hmm"})])
    res = Referee(max_retries=2).play([bad, ProbabilityBot("B", seed=0)], seed=1)
    att = first_turn(res)["attempts"]
    assert [a["meta"] for a in att] == [{"k": 1}, {"k": 2, "thinking": "hmm"}]
    assert att[0]["error"] is not None and att[1]["error"] is None
    assert att[1]["raw"] == BID11  # el árbitro parsea `text`, no el PlayerReply entero


def test_plain_str_players_still_work_and_get_empty_meta():
    res = Referee().play([Rec("A", [BID11]), ProbabilityBot("B", seed=0)], seed=1)
    att = first_turn(res)["attempts"]
    assert att[0]["raw"] == BID11 and att[0]["meta"] == {}
    for e in res.events:
        if e["type"] == "turn":
            assert all("meta" in a for a in e["attempts"])


def test_empty_reason_from_meta_goes_into_the_error():
    bad = Rec("A", [PlayerReply("", {"empty_reason": "agotó num_predict razonando"})])
    res = Referee(max_retries=0).play([bad, ProbabilityBot("B", seed=0)], seed=1)
    err = first_turn(res)["attempts"][0]["error"]
    assert err.startswith("Respuesta vacía: ") and "num_predict" in err


# ---------------------------------------------------------------- feedback de rechazo
def test_rejection_feedback_includes_lowest_legal_bid():
    a = Rec("A", ['{"action":"bid","quantity":2,"face":3}'])
    b = Rec("B", [BID11, '{"action":"dudo"}'])  # 1x1 no supera a 2x3 -> rechazada
    Referee(max_retries=1).play([a, b], seed=1)
    fb = b.feedbacks[1]
    assert fb is not None and "RECHAZADA" in fb
    assert "2x4" in fb and "cantidad 2, cara 4" in fb and "(o puedes dudar)" in fb  # sobre 2x3, la más baja es 2x4


def test_rejection_feedback_when_only_dudo_is_possible():
    a = Rec("A", ['{"action":"bid","quantity":2,"face":6}'])  # 2 dados en mesa: el máximo posible
    b = Rec("B", ["x", '{"action":"dudo"}'])
    Referee(dice_per_player=1, max_retries=1).play([a, b], seed=1)
    assert "sólo puedes dudar" in b.feedbacks[1]


def test_rejection_feedback_first_turn_suggests_opening_bid():
    a = Rec("A", ['{"action":"dudo"}', BID11])  # dudar en el primer turno es ilegal
    Referee(max_retries=1).play([a, ProbabilityBot("B", seed=0)], seed=1)
    assert "1x1" in a.feedbacks[1] and "(o puedes dudar)" not in a.feedbacks[1]


# ---------------------------------------------------------------- prompt base
def _obs(min_bid, current=None):
    return Observation(
        you="A", your_dice=(1, 2), round_number=1, turn_order=("A", "B"), dice_counts={"A": 2, "B": 2},
        total_dice=4, current_bid=current, current_bidder="B" if current else None,
        bids_this_round=({"player": "B", "quantity": current.quantity, "face": current.face},) if current else (),
        can_dudo=current is not None, min_bid=min_bid)


def test_prompt_shows_minimum_legal_bid_and_warns_about_repeating():
    text = prompts.describe_state(_obs(Bid(2, 4), Bid(2, 3)))
    assert "cantidad 2, cara 4" in text
    assert "REPETIR" in prompts.RULES and "ILEGAL" in prompts.RULES


def test_prompt_says_only_dudo_when_no_raise_possible():
    assert "sólo puedes dudar" in prompts.describe_state(_obs(None, Bid(4, 6)))


def test_prompt_format_example_uses_concrete_numbers_not_letters():
    assert not re.search(r'"quantity":\s*N|"face":\s*F', prompts.FORMAT)
    assert re.search(r'"quantity":\s*\d+,\s*"face":\s*\d+', prompts.FORMAT)
    msgs = prompts.build_messages(_obs(Bid(1, 1)))
    assert msgs[0]["role"] == "system" and "ilustrativos" in msgs[0]["content"]


# ---------------------------------------------------------------- OllamaPlayer contra servidor falso
def fake_ollama(response: dict):
    bodies = []

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            bodies.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            out = json.dumps(response).encode()
            self.send_response(200); self.send_header("Content-Length", str(len(out))); self.end_headers()
            self.wfile.write(out)

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, bodies, f"http://127.0.0.1:{srv.server_port}"


GOOD = {"message": {"role": "assistant", "content": '{"action":"dudo"}', "thinking": "Pienso mucho."},
        "done": True, "done_reason": "stop", "prompt_eval_count": 100, "eval_count": 12,
        "eval_duration": 2_000_000_000, "total_duration": 3_000_000_000, "load_duration": 1_000_000}


@pytest.mark.parametrize("think,expected", [(False, False), (True, True), (None, "absent")])
def test_ollama_think_option_is_sent_to_the_api(think, expected):
    srv, bodies, host = fake_ollama(GOOD)
    try:
        OllamaPlayer("A", "m", host=host, think=think).act(_obs(Bid(1, 1)))
    finally:
        srv.shutdown()
    if expected == "absent":
        assert "think" not in bodies[0]
    else:
        assert bodies[0]["think"] is expected


def test_ollama_options_reach_the_request_and_meta_is_returned():
    srv, bodies, host = fake_ollama(GOOD)
    try:
        p = OllamaPlayer("A", "m", host=host, temperature=0.2, num_predict=300, json_mode=True, seed=7)
        reply = p.act(_obs(Bid(1, 1)))
    finally:
        srv.shutdown()
    b = bodies[0]
    assert b["options"] == {"temperature": 0.2, "num_predict": 300, "seed": 7} and b["format"] == "json"
    assert isinstance(reply, PlayerReply) and reply.text == '{"action":"dudo"}'
    assert reply.meta == {"done_reason": "stop", "prompt_tokens": 100, "completion_tokens": 12,
                          "generation_s": 2.0, "total_s": 3.0, "load_s": 0.001, "thinking": "Pienso mucho."}


def test_ollama_empty_content_after_thinking_hitting_num_predict_is_explained():
    resp = {"message": {"role": "assistant", "content": "", "thinking": "x" * 250},
            "done_reason": "length", "eval_count": 50}
    srv, _, host = fake_ollama(resp)
    try:
        p = OllamaPlayer("A", "m", host=host, num_predict=50)
        reason = p.act(_obs(Bid(1, 1))).meta["empty_reason"]
        assert "num_predict=50" in reason and "razonando" in reason and "250" in reason
        # y llega hasta el log del árbitro
        res = Referee(max_retries=0, dice_per_player=1).play([p, ProbabilityBot("B", seed=0)], seed=1)
    finally:
        srv.shutdown()
    err = first_turn(res)["attempts"][0]["error"]
    assert err.startswith("Respuesta vacía:") and "num_predict=50" in err and "think=false" in err
    assert first_turn(res)["attempts"][0]["meta"]["thinking"] == "x" * 250


@pytest.mark.parametrize("resp,fragment", [
    ({"message": {"content": "", "thinking": "solo pienso"}, "done_reason": "stop"}, "sólo devolvió razonamiento"),
    ({"message": {"content": ""}, "done_reason": "stop"}, "'content' vacío"),
])
def test_ollama_other_empty_reasons(resp, fragment):
    srv, _, host = fake_ollama(resp)
    try:
        meta = OllamaPlayer("A", "m", host=host).act(_obs(Bid(1, 1))).meta
    finally:
        srv.shutdown()
    assert fragment in meta["empty_reason"]


def test_ollama_nonempty_content_has_no_empty_reason():
    srv, _, host = fake_ollama(GOOD)
    try:
        assert "empty_reason" not in OllamaPlayer("A", "m", host=host).act(_obs(Bid(1, 1))).meta
    finally:
        srv.shutdown()


# ---------------------------------------------------------------- spec: ollama:<modelo>[@estilo][?opciones]
def test_spec_options_are_parsed_without_touching_make_player():
    spec = "ollama:gemma4:e2b@cot?think=false+temperature=0.2+num_predict=300+timeout=30+json_mode=true"
    p = make_player(spec, "J1", seed=5)
    assert isinstance(p, OllamaPlayer)
    assert (p.model, p.style, p.think, p.temperature, p.num_predict, p.timeout, p.json_mode, p.seed) == \
           ("gemma4:e2b", "cot", False, 0.2, 300, 30.0, True, 5)
    assert p.spec == spec  # el spec completo es la identidad del jugador en logs y resúmenes


def test_spec_defaults_and_alternative_separator():
    p = make_player("ollama:llama3.2:3b", "J1")
    assert (p.model, p.style, p.think, p.temperature, p.num_predict) == ("llama3.2:3b", "basic", None, 0.7, None)
    q = make_player("ollama:m?think=true&num_predict=10", "J1")
    assert q.think is True and q.num_predict == 10


def test_spec_host_option_with_url():
    p = make_player("ollama:m?host=http://otra-maquina:11434", "J1")
    assert p.host == "http://otra-maquina:11434"


def test_split_spec():
    assert split_spec("probbot:0.3") == ("probbot:0.3", {})
    assert split_spec("ollama:m@s?a=1+b=2") == ("ollama:m@s", {"a": "1", "b": "2"})


@pytest.mark.parametrize("spec", [
    "ollama:m?bogus=1",           # opción desconocida
    "ollama:m?think=maybe",       # booleano inválido
    "ollama:m?temperature=-1",
    "ollama:m?num_predict=0",
    "ollama:m?timeout=abc",
    "ollama:m?think",             # sin valor
    "random?x=1", "probbot:0.3?x=1",
    "ollama:", "ollama:?think=false", "desconocido:x",
])
def test_spec_invalid_raises_value_error(spec):
    with pytest.raises(ValueError):
        make_player(spec, "J1")


def test_unknown_option_error_lists_valid_ones():
    with pytest.raises(ValueError, match="think"):
        make_player("ollama:m?bogus=1", "J1")


@pytest.mark.parametrize("bad", ["ollama:m?bogus=1", "ollama:m?think=maybe", "random?x=1"])
def test_run_games_rejects_bad_specs_before_playing(tmp_path, bad):
    with pytest.raises(SystemExit):
        run_games.main(["--players", bad, "probbot", "--out", str(tmp_path)])
    assert not any(tmp_path.iterdir())  # ni siquiera crea la carpeta de la ejecución


# ---------------------------------------------------------------- game_start: schema_version y params
def test_game_start_has_schema_version_and_player_params():
    ps = [Rec("A", params={"class": "X", "think": False}), ProbabilityBot("B", bluff=0.3, seed=4),
          make_player("random:0.5", "C", seed=2)]
    gs = next(e for e in Referee().play(ps, seed=1).events if e["type"] == "game_start")
    assert gs["schema_version"] == SCHEMA_VERSION == 1
    params = {p["name"]: p["params"] for p in gs["players"]}
    assert params["A"] == {"class": "X", "think": False}
    assert params["B"] == {"class": "ProbabilityBot", "dudo_threshold": 0.35, "bluff": 0.3, "seed": 4}
    assert params["C"] == {"class": "RandomPlayer", "p_dudo": 0.5, "seed": 2}
    assert gs["history_rounds"] == 3


def test_ollama_describe_lists_every_option_including_think():
    p = make_player("ollama:m@basic?think=false+num_predict=64", "J1", seed=3)
    d = p.describe()
    assert d == {"class": "OllamaPlayer", "model": "m", "style": "basic", "temperature": 0.7, "think": False,
                 "json_mode": False, "num_predict": 64, "timeout": 120.0, "host": "http://localhost:11434",
                 "seed": 3, "retry_wait": 2.0, "warmup_timeout": 600.0, "num_ctx": None, "keep_alive": None}
    json.dumps(d)  # serializable


# ---------------------------------------------------------------- schema del log (documentación ejecutable)
COMMON = {"game_id", "type"}
REQUIRED = {
    "game_start": COMMON | {"schema_version", "seed", "dice_per_player", "max_retries", "penalty",
                            "history_rounds", "players"},
    "round_start": COMMON | {"round", "starter", "dice_counts", "hands"},
    "turn": COMMON | {"round", "turn", "player", "your_dice", "current_bid", "attempts", "forced", "action"},
    "challenge": COMMON | {"round", "bids", "challenger", "bidder", "challenged_bid", "real_count",
                           "bid_was_true", "loser", "revealed_hands", "dice_counts_after"},
    "penalty": COMMON | {"round", "player", "dice_left"},
    "eliminated": COMMON | {"round", "player"},
    "infrastructure": COMMON | {"round", "turn", "player", "kind"},
    "game_aborted": COMMON | {"round", "reason"},
    "game_end": COMMON | {"winner", "rounds", "stats", "aborted"},
}
ATTEMPT_KEYS = {"raw", "error", "latency_s", "meta"}


def check_schema(events):
    for e in events:
        assert e["type"] in REQUIRED, f"tipo de evento no documentado: {e['type']}"
        missing = REQUIRED[e["type"]] - set(e)
        assert not missing, f"{e['type']} sin campos {missing}"
        if e["type"] == "turn":
            assert e["attempts"] and all(ATTEMPT_KEYS <= set(a) for a in e["attempts"])
            assert ("p_bid_true" in e) != ("p_challenged_bid_true" in e)
        if e["type"] == "game_start":
            assert all({"name", "spec", "params"} <= set(p) for p in e["players"])


class Dead(Player):
    def __init__(self, name):
        super().__init__(name, "dead")

    def act(self, obs, feedback=None):
        from liars_dice.players.base import InfrastructureError
        raise InfrastructureError("caído")


def test_fresh_games_follow_the_documented_schema_for_every_event_type():
    seen = set()
    runs = [
        Referee().play([ProbabilityBot("A", seed=1), ProbabilityBot("B", seed=2), ProbabilityBot("C", seed=3)], seed=1),
        Referee(max_retries=0, penalty="lose_die").play([Rec("A", ["x"] * 3), ProbabilityBot("B", seed=0)], seed=3),
        Referee().play([Dead("A"), ProbabilityBot("B", seed=0)], seed=1),
        Referee(dice_per_player=1).play(
            [Rec("A", [PlayerReply(BID11, {"infra_events": [{"kind": "retry", "http_status": 500,
                                                             "detail": "x", "recovered": True}]})]),
             ProbabilityBot("B", seed=0)], seed=1),
    ]
    for res in runs:
        check_schema(res.events)
        seen |= {e["type"] for e in res.events}
    assert seen == set(REQUIRED)  # los 9 tipos de evento quedan cubiertos


def test_example_log_is_valid_and_not_stale():
    d = ROOT / "examples" / "sample_run"
    files = sorted(d.glob("game_*.jsonl"))
    assert files, "falta examples/sample_run (regenerar: ver examples/README.md)"
    for f in files:
        events = [json.loads(line) for line in f.read_text(encoding="utf-8").splitlines()]
        check_schema(events)
        assert events[0]["type"] == "game_start" and events[0]["schema_version"] == SCHEMA_VERSION
        assert events[-1]["type"] == "game_end"
    with open(d / "games.csv", newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert list(rows[0]) == ["game_id", "seed", "winner_name", "winner_spec", "rounds", "seating",
                             "status", "detail"]
    assert {r["status"] for r in rows} == {"ok"}
