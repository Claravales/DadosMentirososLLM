#!/usr/bin/env python3
"""Ejecuta N partidas seguidas y guarda los registros.

Ejemplos:
  python run_games.py --players random probbot probbot:0.3 --games 20 --seed 1
  python run_games.py --players ollama:llama3.2:3b ollama:qwen2.5:7b --games 50 --rotate

Salida (carpeta --out/<run_id>/):
  game_0001.jsonl ...  eventos de cada partida (1 JSON por línea)
  games.csv            una fila por partida (ganador, rondas, status ok|aborted|error, ...)
  run_config.json      argumentos usados, para reproducir
"""
from __future__ import annotations

import argparse
import sys
import time

from liars_dice.game import PENALTIES, Referee
from liars_dice.players import make_player
from liars_dice.runner import WarmupError, check_specs, new_run_dir, run_series, warmup, write_run_config
from liars_dice.ui import (ConsoleView, RunStats, Style, detect_color, detect_unicode, fmt_time,
                           render_summary, setup_console)


def _positive(name: str, minimum: int):
    def conv(text: str) -> int:
        value = int(text)
        if value < minimum:
            raise argparse.ArgumentTypeError(f"{name} debe ser >= {minimum} (recibido {value})")
        return value
    return conv


def _print_warmup_event(spec: str, ev: dict) -> None:
    if ev.get("kind") == "warmup" and ev.get("ok"):
        print(f"Modelo calentado: {spec} ({ev.get('seconds')} s)")
    elif ev.get("kind") == "retry":
        print(f"  (reintento tras HTTP {ev.get('http_status')} durante el calentamiento)")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--players", nargs="+", required=True, help="specs, ver make_player()")
    ap.add_argument("--games", type=_positive("--games", 1), default=10)
    ap.add_argument("--seed", type=int, default=0, help="semilla base (partida i usa seed+i)")
    ap.add_argument("--dice", type=_positive("--dice", 1), default=5, help="dados por jugador")
    ap.add_argument("--retries", type=_positive("--retries", 0), default=2)
    ap.add_argument("--penalty", choices=PENALTIES, default="forced_move")
    ap.add_argument("--rotate", action="store_true",
                    help="rota los asientos cada partida para anular la ventaja de salir primero")
    ap.add_argument("--out", default="logs")
    ap.add_argument("--quiet", action="store_true", help="solo una línea de progreso por partida y el resumen")
    ap.add_argument("--verbose", "-v", action="store_true",
                    help="añade manos, probabilidades P(cierta) y latencias (modo espectador)")
    ap.add_argument("--no-color", action="store_true", help="sin colores ANSI (también con NO_COLOR=1 o al redirigir)")
    ap.add_argument("--no-warmup", action="store_true",
                    help="no calentar los modelos antes de la primera partida")
    args = ap.parse_args(argv)
    setup_console()
    style = Style(detect_color(args.no_color), detect_unicode())
    view = ConsoleView(style, level=0 if args.quiet else (2 if args.verbose else 1))
    if len(args.players) < 2:
        ap.error("--players necesita al menos 2 jugadores")
    try:
        check_specs(args.players, make_player)
    except ValueError as exc:
        ap.error(str(exc))

    out = new_run_dir(args.out)
    write_run_config(out, vars(args))

    if not args.no_warmup:
        try:
            warmup(args.players, out, make_player, on_event=_print_warmup_event)
        except WarmupError as exc:
            print(f"CALENTAMIENTO FALLIDO para {exc.spec}: {exc.cause}", file=sys.stderr)
            return 2

    referee = Referee(dice_per_player=args.dice, max_retries=args.retries, penalty=args.penalty)
    run_stats = RunStats()
    t0 = time.monotonic()
    if not args.quiet:
        print(style.box([style.c("DADOS MENTIROSOS", "bold", "white") + style.c("  ·  Liar's Dice", "grey"),
                         style.c(f"{args.games} {'partida' if args.games == 1 else 'partidas'} · {args.dice} dados · penalización {args.penalty}"
                                 + (" · rotando asientos" if args.rotate else ""), "grey")]))

    def on_game_start(g: int, gid: str, specs: list[str]) -> None:
        eta = f"ETA {fmt_time((time.monotonic() - t0) / g * (args.games - g))}" if g else ""
        view.game_header(g + 1, args.games, gid, specs, eta)

    def on_game_end(g: int, gid: str, res) -> None:
        spec_of = {p["name"]: p["spec"] for p in res.players}
        run_stats.add_game(res.players, res.winner, res.rounds, res.stats)
        elapsed = time.monotonic() - t0
        remaining = elapsed / (g + 1) * (args.games - g - 1)
        view.game_line(g + 1, args.games, gid, res.winner, spec_of.get(res.winner, ""), res.rounds, elapsed, remaining)

    def on_error(gid: str, _last_line: str) -> None:
        print(f"{gid}: ERROR inesperado (ver {gid}.error.txt)", file=sys.stderr)

    result = run_series(specs=args.players, games=args.games, seed=args.seed, referee=referee, out=out,
                        make_player=make_player, rotate=args.rotate, on_game_start=on_game_start,
                        on_event=view.handle, on_game_end=on_game_end, on_error=on_error)

    exit_code = 1 if result.errors else 0
    if result.aborted:
        print(f"{result.aborted_game}: ABORTADA por fallo de infraestructura: {result.aborted}", file=sys.stderr)
        print("Se detiene la ejecución: no tiene sentido seguir con el servidor del modelo roto.", file=sys.stderr)
        exit_code = 2
    print(render_summary(style, run_stats, args.players, out, time.monotonic() - t0,
                         result.errors, 1 if result.aborted else 0))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
