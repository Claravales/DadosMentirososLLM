"""Ejecución de series de partidas y escritura de registros. Lo comparten run_games.py (consola) y web_app.py (interfaz),
para que ambos produzcan exactamente los mismos archivos y se comporten igual ante fallos.

Salida (carpeta <base>/<run_id>/): run_config.json, infrastructure.jsonl (si hay calentamiento), game_XXXX.jsonl,
games.csv (status ok | aborted | error) y game_XXXX.error.txt si una partida revienta.
"""
from __future__ import annotations

import csv
import json
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Sequence

CSV_HEADER = ["game_id", "seed", "winner_name", "winner_spec", "rounds", "seating", "status", "detail"]


class Stopped(Exception):
    """Lanzada desde un callback (on_event) para detener la serie a petición del usuario."""


class WarmupError(Exception):
    def __init__(self, spec: str, cause: Exception):
        super().__init__(f"{spec}: {cause}")
        self.spec, self.cause = spec, cause


@dataclass
class SeriesResult:
    aborted: Optional[str] = None  # motivo si una partida se abortó por fallo de infraestructura
    aborted_game: Optional[str] = None
    errors: int = 0  # partidas con error inesperado (la serie continúa)
    stopped: bool = False  # detenida por el usuario


def check_specs(specs: Sequence[str], make_player: Callable) -> None:
    """Falla rápido ante un spec o una opción mal escritos, antes de gastar tiempo. Lanza ValueError con el motivo."""
    for spec in specs:
        try:
            make_player(spec, "check")
        except ValueError as exc:
            raise ValueError(f"spec de jugador inválido {spec!r}: {exc}") from exc


def new_run_dir(*bases: Path) -> Path:
    """Crea <base>/<fecha> en la primera base escribible. Dos ejecuciones en el mismo segundo no se pisan."""
    run_id = time.strftime("%Y%m%d-%H%M%S")
    last: Exception = OSError("sin carpeta de registros")
    for base in bases:
        base = Path(base)
        out, suffix = base / run_id, 1
        while out.exists():
            suffix += 1
            out = base / f"{run_id}-{suffix}"
        try:
            out.mkdir(parents=True)
            return out
        except OSError as exc:
            last = exc
    raise last


def write_run_config(out: Path, cfg: dict) -> None:
    (out / "run_config.json").write_text(json.dumps(cfg, indent=2, ensure_ascii=False, default=str), encoding="utf-8")


def warmup(specs: Sequence[str], out: Path, make_player: Callable,
           on_start: Callable[[str], None] | None = None,
           on_event: Callable[[str, dict], None] | None = None) -> None:
    """Calienta cada spec distinto UNA vez antes de la primera partida (carga el modelo en memoria).
    Guarda los eventos en <out>/infrastructure.jsonl. Lanza WarmupError si el servidor falla: no se juega con él roto."""
    with open(out / "infrastructure.jsonl", "w", encoding="utf-8", newline="\n") as f:
        def log(spec: str, ev: dict) -> None:
            f.write(json.dumps({"type": "infrastructure", "spec": spec, **ev}, ensure_ascii=False, default=str) + "\n")
            f.flush()
            if on_event:
                on_event(spec, ev)

        for spec in dict.fromkeys(specs):
            if on_start:
                on_start(spec)
            try:
                events = make_player(spec, "warmup").warmup()
            except Exception as exc:  # InfrastructureError u otro fallo
                events = list(getattr(exc, "events", []))
                if not any(e.get("kind") == "warmup" for e in events):
                    events.append({"kind": "warmup", "ok": False, "detail": f"{type(exc).__name__}: {exc}"})
                for ev in events:
                    log(spec, ev)
                raise WarmupError(spec, exc) from exc
            for ev in events:
                log(spec, ev)


def run_series(
    *,
    specs: Sequence[str],
    games: int,
    seed: int,
    referee,
    out: Path,
    make_player: Callable,
    rotate: bool = False,
    on_game_start: Callable[[int, str, list], None] | None = None,
    on_event: Callable[[dict], None] | None = None,
    on_game_end: Callable[[int, str, object], None] | None = None,
    on_error: Callable[[str, str], None] | None = None,
) -> SeriesResult:
    """Juega `games` partidas seguidas escribiendo game_XXXX.jsonl (un evento por línea, con flush) y games.csv.

    - Fallo inesperado en una partida: queda como status=error (+ .error.txt) y la serie sigue.
    - Fallo de infraestructura (res.aborted): status=aborted y la serie se detiene.
    - `on_event` puede lanzar Stopped para detener la serie (la partida en curso queda como error 'detenida').
    """
    specs = list(specs)
    n = len(specs)
    result = SeriesResult()
    with open(out / "games.csv", "w", newline="", encoding="utf-8") as fcsv:
        w = csv.writer(fcsv)
        w.writerow(CSV_HEADER)
        for g in range(games):
            shift = g % n if rotate else 0
            gspecs = specs[shift:] + specs[:shift]
            game_seed = seed + g
            gid = f"game_{g + 1:04d}"
            seating = "|".join(f"J{i + 1}={s}" for i, s in enumerate(gspecs))
            try:
                players = [make_player(s, f"J{i + 1}", seed=game_seed * 100 + i) for i, s in enumerate(gspecs)]
                # utf-8 explícito (en Windows el defecto es cp1252 y rompe con ->, emojis...);
                # flush por evento: si algo falla, el log parcial queda en disco.
                with open(out / f"{gid}.jsonl", "w", encoding="utf-8", newline="\n") as flog:
                    def sink(ev: dict, f=flog) -> None:
                        f.write(json.dumps(ev, ensure_ascii=False, default=str) + "\n")
                        f.flush()
                        if on_event:
                            on_event(ev)

                    if on_game_start:
                        on_game_start(g, gid, gspecs)
                    res = referee.play(players, seed=game_seed, game_id=gid, on_event=sink)
            except Stopped:
                w.writerow([gid, game_seed, "", "", "", seating, "error", "detenida por el usuario"])
                fcsv.flush()
                result.stopped = True
                break
            except Exception:  # un fallo inesperado no tumba toda la ejecución
                tb = traceback.format_exc()
                (out / f"{gid}.error.txt").write_text(tb, encoding="utf-8")
                last = tb.strip().splitlines()[-1]
                w.writerow([gid, game_seed, "", "", "", seating, "error", last])
                fcsv.flush()
                result.errors += 1
                if on_error:
                    on_error(gid, last)
                continue

            real_seating = "|".join(f"{p['name']}={p['spec']}" for p in res.players)
            if res.aborted:
                w.writerow([gid, game_seed, "", "", res.rounds, real_seating, "aborted", res.aborted])
                fcsv.flush()
                result.aborted, result.aborted_game = res.aborted, gid
                break
            spec_of = {p["name"]: p["spec"] for p in res.players}
            w.writerow([gid, game_seed, res.winner, spec_of.get(res.winner, ""), res.rounds, real_seating, "ok", ""])
            fcsv.flush()
            if on_game_end:
                on_game_end(g, gid, res)
    return result
