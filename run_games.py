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
import csv
import json
import sys
import time
import traceback
from collections import Counter
from pathlib import Path

from liars_dice.game import PENALTIES, Referee
from liars_dice.players import make_player


def _warmup(specs: list[str], out: Path) -> int:
    """Calienta cada spec distinto UNA vez antes de la primera partida (carga el modelo en memoria).
    Guarda los eventos en <out>/infrastructure.jsonl. Devuelve 0 si todo va bien, 2 si el servidor falla."""
    with open(out / "infrastructure.jsonl", "w", encoding="utf-8", newline="\n") as f:
        def log(spec: str, ev: dict) -> None:
            f.write(json.dumps({"type": "infrastructure", "spec": spec, **ev}, ensure_ascii=False, default=str) + "\n")
            f.flush()

        for spec in dict.fromkeys(specs):
            player = make_player(spec, "warmup")
            try:
                events = player.warmup()
            except Exception as exc:  # InfrastructureError u otro fallo: no se juega con un servidor roto
                events = list(getattr(exc, "events", []))
                if not any(e.get("kind") == "warmup" for e in events):
                    events.append({"kind": "warmup", "ok": False, "detail": f"{type(exc).__name__}: {exc}"})
                for ev in events:
                    log(spec, ev)
                print(f"CALENTAMIENTO FALLIDO para {spec}: {exc}", file=sys.stderr)
                return 2
            for ev in events:
                log(spec, ev)
                if ev.get("kind") == "warmup" and ev.get("ok"):
                    print(f"Modelo calentado: {spec} ({ev.get('seconds')} s)")
                elif ev.get("kind") == "retry":
                    print(f"  (reintento tras HTTP {ev.get('http_status')} durante el calentamiento)")
    return 0


def _positive(name: str, minimum: int):
    def conv(text: str) -> int:
        value = int(text)
        if value < minimum:
            raise argparse.ArgumentTypeError(f"{name} debe ser >= {minimum} (recibido {value})")
        return value
    return conv


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
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--no-warmup", action="store_true",
                    help="no calentar los modelos antes de la primera partida")
    args = ap.parse_args(argv)
    if len(args.players) < 2:
        ap.error("--players necesita al menos 2 jugadores")
    for spec in args.players:  # falla rápido ante un spec o una opción mal escrita, antes de gastar tiempo
        try:
            make_player(spec, "check")
        except ValueError as exc:
            ap.error(f"spec de jugador inválido {spec!r}: {exc}")

    run_id = time.strftime("%Y%m%d-%H%M%S")
    out = Path(args.out) / run_id
    suffix = 1
    while out.exists():  # dos ejecuciones en el mismo segundo no se pisan
        suffix += 1
        out = Path(args.out) / f"{run_id}-{suffix}"
    out.mkdir(parents=True)
    (out / "run_config.json").write_text(json.dumps(vars(args), indent=2, ensure_ascii=False), encoding="utf-8")

    if not args.no_warmup:
        code = _warmup(args.players, out)
        if code:
            return code
    referee = Referee(dice_per_player=args.dice, max_retries=args.retries, penalty=args.penalty)
    n = len(args.players)
    wins: Counter = Counter()
    played: Counter = Counter()
    exit_code = 0

    with open(out / "games.csv", "w", newline="", encoding="utf-8") as fcsv:
        w = csv.writer(fcsv)
        w.writerow(["game_id", "seed", "winner_name", "winner_spec", "rounds", "seating", "status", "detail"])
        for g in range(args.games):
            shift = g % n if args.rotate else 0
            specs = args.players[shift:] + args.players[:shift]
            seed = args.seed + g
            gid = f"game_{g + 1:04d}"
            seating = "|".join(f"J{i + 1}={s}" for i, s in enumerate(specs))
            try:
                players = [make_player(s, f"J{i + 1}", seed=seed * 100 + i) for i, s in enumerate(specs)]
                # utf-8 explícito (en Windows el defecto es cp1252 y rompe con ->, emojis...);
                # flush por evento: si algo falla, el log parcial queda en disco.
                with open(out / f"{gid}.jsonl", "w", encoding="utf-8", newline="\n") as flog:
                    def show_event(ev: dict) -> None:
                        if args.quiet:
                            return
                        event_type = ev.get("type")
                        if event_type == "turn":
                            print(f"\nRonda {ev['round']} | Turno {ev['turn']} | {ev['player']}")
                            previous = ev.get("current_bid")
                            if previous:
                                print(f"  Apuesta actual: {previous['quantity']} dados de cara {previous['face']}")
                            for attempt in ev.get("attempts", []):
                                if attempt.get("error"):
                                    print(f"  Respuesta inválida: {attempt['error']}")
                            action = ev.get("action", {})
                            if action.get("action") == "bid":
                                print(f"  {ev['player']} apuesta: {action['quantity']} dados de cara {action['face']}")
                            else:
                                print(f"  {ev['player']} duda")
                            if ev.get("forced"):
                                print("  (jugada aplicada por el árbitro tras agotar reintentos)")
                        elif event_type == "challenge":
                            bid = ev["challenged_bid"]
                            result = "cierta" if ev["bid_was_true"] else "falsa"
                            print(f"  Resultado: había {ev['real_count']} dados de cara {bid['face']}; "
                                  f"la apuesta era {result}. Pierde un dado {ev['loser']}.")
                        elif event_type == "penalty":
                            print(f"  Penalización: {ev['player']} pierde un dado ({ev['dice_left']} restantes).")
                        elif event_type == "eliminated":
                            print(f"  {ev['player']} queda eliminado.")

                    def sink(ev: dict, f=flog) -> None:
                        f.write(json.dumps(ev, ensure_ascii=False, default=str) + "\n")
                        f.flush()
                        show_event(ev)
                    res = referee.play(players, seed=seed, game_id=gid, on_event=sink)
            except Exception:  # un fallo inesperado no tumba toda la ejecución
                tb = traceback.format_exc()
                (out / f"{gid}.error.txt").write_text(tb, encoding="utf-8")
                w.writerow([gid, seed, "", "", "", seating, "error", tb.strip().splitlines()[-1]])
                fcsv.flush()
                print(f"{gid}: ERROR inesperado (ver {gid}.error.txt)", file=sys.stderr)
                exit_code = 1
                continue

            if res.aborted:
                w.writerow([gid, seed, "", "", res.rounds, "|".join(f"{p['name']}={p['spec']}" for p in res.players),
                            "aborted", res.aborted])
                fcsv.flush()
                print(f"{gid}: ABORTADA por fallo de infraestructura: {res.aborted}", file=sys.stderr)
                print("Se detiene la ejecución: no tiene sentido seguir con el servidor del modelo roto.",
                      file=sys.stderr)
                exit_code = 2
                break

            spec_of = {p["name"]: p["spec"] for p in res.players}
            wspec = spec_of.get(res.winner, "")
            w.writerow([gid, seed, res.winner, wspec, res.rounds,
                        "|".join(f"{p['name']}={p['spec']}" for p in res.players), "ok", ""])
            fcsv.flush()
            for s in specs:
                played[s] += 1
            wins[wspec] += 1
            if not args.quiet:
                print(f"{gid}: gana {res.winner} ({wspec}) en {res.rounds} rondas")

    print(f"\nRegistros en {out}")
    for s in dict.fromkeys(args.players):
        print(f"  {s:30s} victorias {wins[s]:4d} / {played[s]:4d} asientos")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())


