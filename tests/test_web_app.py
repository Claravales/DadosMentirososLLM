"""Tests de la app web (web_app.py), la interfaz de consola (ui.py) y el host de Ollama."""
import json
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

import web_app
from liars_dice.players import make_player
from liars_dice.players.baselines import RandomPlayer
from liars_dice.ui import RunStats, Style, render_summary


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setattr(web_app, "LOGS", tmp_path / "logs")
    web_app.stop_run()
    web_app.RUN.events, web_app.RUN.error, web_app.RUN.out_dir = [], None, None
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), web_app.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    web_app.stop_run()
    httpd.shutdown()


def call(base, path, body=None, headers=None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(base + path, data=data, headers=headers or {}, method="POST" if data is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as exc:
        return exc.code, json.load(exc)


def wait_done(timeout=20):
    t = time.time()
    while web_app.RUN.running and time.time() - t < timeout:
        time.sleep(0.05)
    assert not web_app.RUN.running


def test_serves_index_and_ping(server):
    with urllib.request.urlopen(server + "/") as r:
        assert b"Dados Mentirosos" in r.read()
    assert call(server, "/api/ping")[1] == {"app": web_app.APP_ID}


@pytest.mark.parametrize("headers", [{"Host": "evil.com"}, {"Origin": "http://evil.com"}])
def test_rejects_foreign_hosts_and_origins(server, headers):
    assert call(server, "/api/ping", headers=headers)[0] == 403
    assert call(server, "/api/start", {"players": ["random", "probbot"]}, headers=headers)[0] == 403
    assert not web_app.RUN.running


@pytest.mark.parametrize("body, fragment", [
    ({"players": ["random"]}, "al menos 2"),
    ({"players": ["random"] * 7}, "Máximo 6"),
    ({"players": ["random", "nada:x"]}, "inválido"),
    ({"players": ["random", "probbot"], "penalty": "xx"}, "Penalización"),
    ({"players": ["random", "probbot"], "games": "abc"}, "Parámetros"),
])
def test_start_validates_input(server, body, fragment):
    code, payload = call(server, "/api/start", body)
    assert code == 400 and fragment in payload["error"]


def test_full_run_writes_logs_like_run_games(server, tmp_path):
    assert call(server, "/api/start", {"players": ["random", "probbot"], "games": 3, "dice": 2, "seed": 1})[0] == 200
    wait_done()
    snap = call(server, "/api/events?since=0")[1]
    assert snap["error"] is None and snap["running"] is False
    types = [e["type"] for e in snap["events"]]
    assert types.count("game_start") == 3 and types.count("game_end") == 3
    out = web_app.RUN.out_dir
    assert (out / "run_config.json").exists() and (out / "game_0003.jsonl").exists()
    rows = (out / "games.csv").read_text(encoding="utf-8").splitlines()
    assert rows[0].startswith("game_id,seed,winner_name") and len(rows) == 4 and all(",ok," in r for r in rows[1:])


class Slow(RandomPlayer):
    def act(self, obs, feedback=None):
        time.sleep(0.3)
        return super().act(obs, feedback)


def test_second_start_is_refused_while_running(server, monkeypatch):
    monkeypatch.setattr(web_app, "make_player", lambda spec, name, seed=None: Slow(name, seed=seed, spec=spec))
    assert call(server, "/api/start", {"players": ["a", "b"], "games": 50})[0] == 200
    time.sleep(0.5)
    assert call(server, "/api/start", {"players": ["a", "b"]})[0] == 409


def test_stop_is_immediate_and_does_not_leak_into_the_next_run(server, monkeypatch):
    """Parar libera la interfaz al instante aunque la hebra siga esperando a un modelo, y la ejecución siguiente
    no recibe eventos ni el estado de la anterior."""
    monkeypatch.setattr(web_app, "make_player", lambda spec, name, seed=None: Slow(name, seed=seed, spec=spec))
    assert call(server, "/api/start", {"players": ["a", "b"], "games": 50})[0] == 200
    time.sleep(0.6)
    t = time.time()
    assert call(server, "/api/stop", {})[0] == 200
    snap = call(server, "/api/events?since=0")[1]
    assert time.time() - t < 1 and snap["running"] is False
    assert snap["events"][-1] == {"type": "notice", "text": "Detenido."}
    n_stopped = len(snap["events"])

    monkeypatch.setattr(web_app, "make_player", make_player)  # bots rápidos
    assert call(server, "/api/start", {"players": ["random", "probbot"], "games": 2, "dice": 2})[0] == 200
    wait_done()
    time.sleep(0.8)  # la hebra antigua ya habrá despertado y debe haberse callado
    snap = call(server, "/api/events?since=0")[1]
    assert snap["error"] is None and snap["running"] is False
    assert {e.get("_g") for e in snap["events"] if "_g" in e} == {1, 2}
    assert [e["type"] for e in snap["events"]].count("game_end") == 2 and n_stopped > 0


@pytest.mark.parametrize("spec, host", [
    ("random", None),
    ("ollama:gemma4:e2b?think=false", "http://localhost:11434"),
    ("ollama:m?host=http://10.0.0.5:11434", "http://10.0.0.5:11434"),
])
def test_ollama_host_from_spec(spec, host):
    assert web_app._ollama_host(spec) == host


def test_missing_model_gives_a_clear_error(monkeypatch):
    monkeypatch.setattr(web_app, "_ollama_models", lambda host, timeout=3: ["gemma4:e2b"])
    web_app._ensure_ollama(["ollama:gemma4:e2b@basic?think=false", "probbot"])  # instalado: no falla
    with pytest.raises(RuntimeError, match="ollama pull qwen9"):
        web_app._ensure_ollama(["ollama:qwen9", "probbot"])


def test_ollama_down_and_not_startable_is_a_clear_error(monkeypatch):
    monkeypatch.setattr(web_app, "_ollama_models", lambda host, timeout=3: None)
    monkeypatch.setattr(web_app, "_start_ollama", lambda: False)
    with pytest.raises(RuntimeError, match="No se puede conectar con Ollama"):
        web_app._ensure_ollama(["ollama:gemma4:e2b", "probbot"])


def test_ollama_connects_via_ipv4_but_keeps_spec_host():
    """'localhost' tarda ~2 s por petición en Windows: se conecta a 127.0.0.1 sin cambiar la identidad registrada."""
    p = make_player("ollama:gemma4:e2b", "J1")
    assert p._connect_host() == "http://127.0.0.1:11434"
    assert p.host == "http://localhost:11434" and p.describe()["host"] == "http://localhost:11434"
    assert make_player("ollama:m?host=http://10.0.0.5:11434", "J1")._connect_host() == "http://10.0.0.5:11434"


def test_summary_counts_a_mirror_match_once_per_game():
    stats = RunStats()
    players = [{"name": "J1", "spec": "probbot"}, {"name": "J2", "spec": "probbot"}, {"name": "J3", "spec": "random"}]
    stats.add_game(players, "J1", 5, {"J1": {"bids": 3}, "J2": {"bids": 2}, "J3": {"bids": 1, "invalid_responses": 2}})
    stats.add_game(players, "J3", 6, {})
    assert stats.games == {"probbot": 2, "random": 2} and stats.wins == {"probbot": 1, "random": 1}
    text = render_summary(Style(False, False), stats, ["probbot", "random"], "logs/x", 3.0, 0, 0)
    assert "probbot" in text and "50%" in text and "2 ok" in text and "inválidas" in text


def test_num_ctx_and_keep_alive_reach_ollama_in_turns_and_warmup(monkeypatch):
    """Con poca memoria Ollama expulsa el modelo tras cada respuesta (recarga de ~40 s por turno): un contexto menor y
    keep_alive lo evitan, y deben enviarse también en el calentamiento para no forzar otra recarga."""
    p = make_player("ollama:m?think=false+num_ctx=2048+keep_alive=30m", "J1")
    assert p.describe()["num_ctx"] == 2048 and p.describe()["keep_alive"] == "30m"
    sent = []
    monkeypatch.setattr(p, "_send", lambda payload, timeout: sent.append(payload) or {"message": {"content": "ok"}})
    p.warmup()
    assert sent[0]["options"]["num_ctx"] == 2048 and sent[0]["keep_alive"] == "30m"
    with pytest.raises(ValueError, match="num_ctx"):
        make_player("ollama:m?num_ctx=0", "J1")


def test_models_endpoint_lists_name_and_size(server, monkeypatch):
    monkeypatch.setattr(web_app, "_ollama_tags", lambda host, timeout=3: [{"name": "m:1b", "size": 1_500_000_000}])
    assert call(server, "/api/models")[1] == {"ollama": True, "models": [{"name": "m:1b", "gb": 1.5}]}
    monkeypatch.setattr(web_app, "_ollama_tags", lambda host, timeout=3: None)
    assert call(server, "/api/models")[1] == {"ollama": False, "models": []}
