"""Panel local para ejecutar y explorar experimentos de DadosMentirososLLM."""
from __future__ import annotations

import csv
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parent
LOGS = ROOT / "logs"
FACES = {1: "⚀", 2: "⚁", 3: "⚂", 4: "⚃", 5: "⚄", 6: "⚅"}

st.set_page_config(page_title="Dados Mentirosos · Laboratorio", page_icon="🎲", layout="wide")
st.markdown("""<style>
.block-container {padding-top: 2rem; max-width: 1440px}
[data-testid="stMetric"] {background: #111827; padding: 16px 18px; border: 1px solid #283449; border-radius: 14px}
[data-testid="stSidebar"] {border-right: 1px solid #283449}
</style>""", unsafe_allow_html=True)
st.title("🎲 Dados Mentirosos")
st.caption("Laboratorio local para ejecutar partidas y analizar el comportamiento de tus jugadores.")


def discover_runs() -> list[Path]:
    return sorted((p for p in LOGS.iterdir() if p.is_dir() and (p / "games.csv").exists()), reverse=True) if LOGS.exists() else []


def load_run(folder: Path) -> tuple[pd.DataFrame, list[dict]]:
    with (folder / "games.csv").open(encoding="utf-8", newline="") as f:
        games = pd.DataFrame(list(csv.DictReader(f)))
    if not games.empty:
        games["rounds"] = pd.to_numeric(games["rounds"], errors="coerce")
        games["seed"] = pd.to_numeric(games["seed"], errors="coerce")
    events = []
    for path in sorted(folder.glob("game_*.jsonl")):
        try:
            with path.open(encoding="utf-8") as f:
                events.extend(json.loads(line) for line in f if line.strip())
        except (OSError, json.JSONDecodeError):
            continue
    return games, events


def count_stats(events: list[dict]) -> Counter:
    result = Counter()
    for event in events:
        if event.get("type") == "turn":
            result["turns"] += 1
            result["retries"] += max(0, len(event.get("attempts", [])) - 1)
            result["invalid"] += sum(bool(a.get("error")) for a in event.get("attempts", []))
            result["forced"] += bool(event.get("forced"))
        elif event.get("type") == "challenge":
            result["challenges"] += 1
    return result

runs = discover_runs()
with st.sidebar:
    st.header("Ejecución")
    st.caption("Configura la simulación. Los modelos Ollama deben estar disponibles en este equipo.")
    player_text = st.text_area("Jugadores (uno por línea)", value="probbot\nrandom", height=110,
                               help="Ej.: probbot, random, ollama:gemma4:e2b?think=false")
    games_n = st.number_input("Partidas", min_value=1, max_value=10000, value=20, step=1)
    dice = st.number_input("Dados por jugador", min_value=1, max_value=20, value=5, step=1)
    seed = st.number_input("Semilla", value=1, step=1)
    retries = st.number_input("Reintentos", min_value=0, max_value=10, value=2, step=1)
    rotate = st.checkbox("Rotar asientos", value=True)
    warmup = st.checkbox("Calentar modelos Ollama", value=True)
    penalty = st.selectbox("Penalización por respuesta inválida", ["forced_move", "lose_die"])
    launch = st.button("▶ Ejecutar partidas", type="primary", use_container_width=True)
    st.divider()
    st.caption("También puedes inspeccionar las ejecuciones guardadas en logs/.")

if launch:
    players = [line.strip() for line in player_text.splitlines() if line.strip()]
    if len(players) < 2:
        st.error("Añade al menos dos jugadores, uno por línea.")
    else:
        cmd = [sys.executable, str(ROOT / "run_games.py"), "--players", *players,
               "--games", str(games_n), "--dice", str(dice), "--seed", str(seed),
               "--retries", str(retries), "--penalty", penalty, "--out", str(LOGS), "--quiet"]
        if rotate:
            cmd.append("--rotate")
        if not warmup:
            cmd.append("--no-warmup")
        with st.status("Preparando ejecución…", expanded=True) as status:
            try:
                proc = subprocess.Popen(cmd, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                        text=True, encoding="utf-8", errors="replace")
                output_box = st.empty()
                lines = []
                while proc.poll() is None:
                    if proc.stdout is not None:
                        line = proc.stdout.readline()
                        if line:
                            lines.append(line.rstrip())
                            output_box.code("\n".join(lines[-14:]), language="text")
                if proc.stdout is not None:
                    lines.extend(line.rstrip() for line in proc.stdout)
                output_box.code("\n".join(lines[-14:]) or "Ejecución terminada.", language="text")
                if proc.returncode == 0:
                    status.update(label="Ejecución completada", state="complete")
                    st.success("Partidas completadas. Actualiza la vista de resultados para explorarlas.")
                    st.rerun()
                else:
                    status.update(label="Ejecución terminada con incidencias", state="error")
                    st.error(f"run_games.py terminó con código {proc.returncode}. Revisa el detalle arriba y los logs.")
            except OSError as exc:
                status.update(label="No se pudo iniciar la ejecución", state="error")
                st.error(str(exc))

runs = discover_runs()
if not runs:
    st.info("Aún no hay ejecuciones guardadas. Configura los jugadores en la barra lateral y lanza la primera.")
    st.stop()

labels = {p.name: p for p in runs}
selected = st.selectbox("Ejecución para analizar", list(labels), index=0)
folder = labels[selected]
games, events = load_run(folder)
if games.empty:
    st.warning("Esta ejecución aún no tiene filas en games.csv.")
    st.stop()
ok = games[games["status"] == "ok"]
st.subheader(f"Resultados · {selected}")
cols = st.columns(4)
cols[0].metric("Partidas", len(games))
cols[1].metric("Completadas", len(ok))
cols[2].metric("Rondas promedio", f"{ok.rounds.mean():.1f}" if ok.rounds.notna().any() else "—")
stats = count_stats(events)
cols[3].metric("Movimientos forzados", stats["forced"])

if not ok.empty:
    wins = ok["winner_spec"].value_counts().rename_axis("Jugador").reset_index(name="Victorias")
    wins["Porcentaje"] = (wins["Victorias"] / len(ok) * 100).round(1)
    left, right = st.columns([1, 1.2])
    with left:
        st.markdown("#### Victorias")
        st.bar_chart(wins.set_index("Jugador")["Victorias"], color="#8b5cf6")
        st.dataframe(wins, hide_index=True, use_container_width=True)
    with right:
        st.markdown("#### Duración de las partidas")
        rounds = ok[["winner_spec", "rounds"]].dropna().rename(columns={"winner_spec": "Ganador", "rounds": "Rondas"})
        st.scatter_chart(rounds, x="Ganador", y="Rondas")

st.markdown("#### Actividad y calidad de las respuestas")
quality = st.columns(3)
quality[0].metric("Turnos registrados", stats["turns"])
quality[1].metric("Respuestas inválidas", stats["invalid"])
quality[2].metric("Dudas", stats["challenges"])

with st.expander("Partidas y estado"):
    st.dataframe(games, hide_index=True, use_container_width=True)

with st.expander("Explorar una partida"):
    game_ids = games["game_id"].tolist()
    game_id = st.selectbox("Partida", game_ids)
    game_events = [e for e in events if e.get("game_id") == game_id]
    if not game_events:
        st.caption("No hay eventos disponibles para esta partida.")
    else:
        config = next((e for e in game_events if e.get("type") == "game_start"), {})
        if config.get("players"):
            st.caption(" · ".join(f"{p.get('name')}: {p.get('spec')}" for p in config["players"]))
        for event in game_events:
            kind = event.get("type")
            if kind == "round_start":
                st.markdown(f"**Ronda {event.get('round')}** · Dados vivos: " + " | ".join(f"{p}: {n}" for p, n in event.get("dice_counts", {}).items()))
            elif kind == "turn":
                action = event.get("action", {})
                if action.get("action") == "bid":
                    summary = f"Apuesta {action.get('quantity')} {FACES.get(action.get('face'), '')} (cara {action.get('face')})"
                else:
                    summary = "¡Dudo!"
                st.write(f"**{event.get('player')}** · {summary}" + (" · movimiento forzado" if event.get("forced") else ""))
            elif kind == "challenge":
                bid = event.get("challenged_bid", {})
                truth = "cierta" if event.get("bid_was_true") else "falsa"
                st.info(f"Se revelan los dados: {event.get('real_count')} de cara {bid.get('face')}. La apuesta era {truth}; pierde un dado {event.get('loser')}.")
            elif kind == "eliminated":
                st.warning(f"{event.get('player')} queda eliminado.")
            elif kind == "game_end":
                st.success(f"Ganador: {event.get('winner') or 'sin ganador'} · {event.get('rounds')} rondas")

with st.expander("Configuración de la ejecución"):
    config_path = folder / "run_config.json"
    if config_path.exists():
        st.json(json.loads(config_path.read_text(encoding="utf-8")))
    else:
        st.caption("No hay run_config.json.")
