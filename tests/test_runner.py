"""Tests de liars_dice/runner.py: lo que comparten run_games.py (consola) y web_app.py (interfaz)."""
import csv
import json
import time

import pytest

import run_games
import web_app
from liars_dice.game import Referee
from liars_dice.players import make_player
from liars_dice.players.base import InfrastructureError
from liars_dice.runner import Stopped, WarmupError, check_specs, new_run_dir, run_series, warmup


def rows(out):
    with open(out / "games.csv", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def events_without_timing(path):
    """Eventos de una partida sin las latencias medidas (lo único que varía entre ejecuciones)."""
    evs = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    for ev in evs:
        for attempt in ev.get("attempts", []):
            attempt.pop("latency_s", None)
    return evs


def test_console_and_web_produce_identical_files(tmp_path, monkeypatch):
    """Mismos argumentos -> mismos game_XXXX.jsonl, games.csv y claves de run_config (sin duplicar lógica)."""
    args = ["--players", "random", "probbot", "probbot:0.3", "--games", "4", "--seed", "7", "--dice", "3", "--rotate"]
    run_games.main([*args, "--out", str(tmp_path / "cli"), "--quiet"])
    cli = next((tmp_path / "cli").iterdir())

    monkeypatch.setattr(web_app, "LOGS", tmp_path / "web")
    web_app.start({"players": ["random", "probbot", "probbot:0.3"], "games": 4, "seed": 7, "dice": 3, "rotate": True})
    for _ in range(200):
        if not web_app.RUN.running:
            break
        time.sleep(0.05)
    assert web_app.RUN.error is None
    web = web_app.RUN.out_dir

    assert (cli / "games.csv").read_text(encoding="utf-8") == (web / "games.csv").read_text(encoding="utf-8")
    for i in range(1, 5):
        # la partida es determinista dada la semilla; el formato es idéntico
        assert events_without_timing(cli / f"game_{i:04d}.jsonl") == events_without_timing(web / f"game_{i:04d}.jsonl")
    assert (web / "infrastructure.jsonl").exists() and (cli / "infrastructure.jsonl").exists()
    assert {"players", "games", "seed", "dice"} <= set(json.loads((web / "run_config.json").read_text(encoding="utf-8")))


def test_check_specs_reports_the_bad_spec():
    check_specs(["random", "probbot:0.3"], make_player)
    with pytest.raises(ValueError, match="spec de jugador inválido 'nada:x'"):
        check_specs(["random", "nada:x"], make_player)


def test_new_run_dir_never_overwrites_and_falls_back(tmp_path, monkeypatch):
    monkeypatch.setattr(time, "strftime", lambda fmt: "20260101-000000")
    a, b = new_run_dir(tmp_path), new_run_dir(tmp_path)
    assert a != b and a.exists() and b.exists()
    blocked = tmp_path / "es_un_archivo"
    blocked.write_text("x")
    ok = new_run_dir(blocked, tmp_path / "respaldo")  # la 1ª base no se puede crear: usa la 2ª
    assert ok.parent == tmp_path / "respaldo"


def test_warmup_failure_logs_events_and_raises(tmp_path):
    class Bad:
        def warmup(self):
            err = InfrastructureError("servidor roto")
            err.events = [{"kind": "retry", "http_status": 500, "recovered": False}]
            raise err

    with pytest.raises(WarmupError) as ei:
        warmup(["m1", "m2"], tmp_path, lambda spec, name, seed=None: Bad())
    assert ei.value.spec == "m1"
    lines = [json.loads(x) for x in (tmp_path / "infrastructure.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [e["kind"] for e in lines] == ["retry", "warmup"] and lines[-1]["ok"] is False


def test_stop_from_callback_ends_series_and_marks_the_game(tmp_path):
    seen = []

    def on_event(ev):
        seen.append(ev)
        if len(seen) == 3:
            raise Stopped()

    res = run_series(specs=["random", "probbot"], games=5, seed=1, referee=Referee(dice_per_player=3), out=tmp_path,
                     make_player=make_player, on_event=on_event)
    assert res.stopped and not res.aborted
    r = rows(tmp_path)
    assert len(r) == 1 and r[0]["status"] == "error" and "detenida" in r[0]["detail"]
    assert len((tmp_path / "game_0001.jsonl").read_text(encoding="utf-8").splitlines()) == 3  # log parcial en disco


def test_unexpected_error_continues_and_is_reported(tmp_path):
    calls = {"n": 0}

    def flaky(spec, name, seed=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ValueError("spec rota")
        return make_player(spec, name, seed=seed)

    reported = []
    res = run_series(specs=["random", "probbot"], games=2, seed=1, referee=Referee(dice_per_player=2), out=tmp_path,
                     make_player=flaky, on_error=lambda gid, last: reported.append((gid, last)))
    assert res.errors == 1 and [r["status"] for r in rows(tmp_path)] == ["error", "ok"]
    assert reported == [("game_0001", "ValueError: spec rota")] and (tmp_path / "game_0001.error.txt").exists()
