# DadosMentirososLLM

Árbitro de Liar's Dice (Perudo) en el que todos los jugadores son modelos de lenguaje locales (pesos abiertos, vía Ollama).
Sólo usa la librería estándar de Python (3.10+). Para los tests: `pip install -r requirements-dev.txt`.

## Interfaz web local

Instala las dependencias de la interfaz y arráncala desde la raíz del proyecto:

```bash
pip install -r requirements-ui.txt
streamlit run streamlit_app.py
```

La aplicación permite configurar y ejecutar lotes de partidas, y consultar las ejecuciones guardadas en `logs/`: victorias, duración, estados, actividad y eventos de cada partida. Para jugadores Ollama, el servidor local debe estar en marcha y el modelo disponible.

## Uso rápido

```bash
python -m pytest -q                                   # tests (usad `python -m pytest`, no `pytest` a secas)
python run_games.py --players random probbot probbot:0.3 --games 20 --seed 1 --rotate
# con Ollama en marcha (comillas: el spec lleva '?'):
python run_games.py --players "ollama:gemma4:e2b?think=false" probbot --games 10 --rotate
```

Cada ejecución crea `logs/<fecha>/` con `game_XXXX.jsonl` (todos los eventos), `games.csv` y `run_config.json`.
Un ejemplo ya generado (sin Ollama) está en [examples/sample_run/](examples/sample_run/).

## Spec de jugador (`--players`)

| Spec | Jugador |
|---|---|
| `random` / `random:<p_dudo>` | al azar entre jugadas legales |
| `probbot` / `probbot:<bluff>` | línea base estadística |
| `ollama:<modelo>[@<estilo>][?clave=valor+clave=valor]` | modelo local de Ollama |

Opciones de Ollama (separadas por `+`, o `&` si lo entrecomilláis; el `+` evita problemas de quoting en PowerShell):

| Opción | Tipo | Defecto | Efecto |
|---|---|---|---|
| `think` | true/false | (lo decide el modelo) | `false` envía `think:false` a la API (sin razonamiento; mucho más rápido) |
| `temperature` | float ≥ 0 | 0.7 | |
| `num_predict` | int > 0 | sin límite | máx. tokens. Con `think` activo, el razonamiento puede agotarlo y `content` llega vacío (el error lo indica) |
| `timeout` | float > 0 | 120 | segundos esperando la respuesta |
| `json_mode` | true/false | false | `format:"json"` |
| `host` | URL | `http://localhost:11434` | |
| `retry_wait` | float ≥ 0 | 2 | segundos de espera antes del reintento tras HTTP 500 |
| `warmup_timeout` | float > 0 | 600 | segundos máx. para cargar el modelo en el calentamiento |

Ejemplo: `"ollama:gemma4:e2b@basic?think=false+temperature=0.2+num_predict=300"`.
El **spec completo** es la identidad del jugador: dos specs distintos se cuentan como jugadores distintos en `games.csv` y en el resumen.
Un spec o una opción mal escritos hacen fallar `run_games.py` al arrancar, antes de jugar nada.

## Reglas implementadas (decisiones del árbitro)

- 5 dados por jugador (`--dice`), caras 1–6, **sin comodines** (los 1 no valen por otra cara).
- Apuesta = (cantidad, cara). Subir = más cantidad (cualquier cara) o misma cantidad con cara mayor. Repetir la apuesta es ilegal.
- La cantidad no puede superar los dados vivos en la mesa. No se puede dudar en el primer turno.
- Dudar: se destapan todos los dados; si hay ≥ cantidad, pierde un dado el que dudó; si no, el que apostó.
- Empieza la ronda siguiente quien perdió el dado (o el siguiente vivo si fue eliminado). Gana el último con dados.
- Las partidas siempre terminan: cada ronda quita ≥ 1 dado y las apuestas sólo pueden subir.

## Qué sabe cada jugador

El árbitro construye una `Observation` (`liars_dice/observation.py`) con **sólo los dados del propio jugador**; los ajenos de la ronda
en curso no están en el objeto. Sí ve lo público: dados vivos de cada uno, apuestas de la ronda, la apuesta legal más baja (`min_bid`)
y las últimas rondas ya destapadas (copias profundas: modificarlas no afecta al árbitro ni al log).

## Respuestas imposibles y fallos (robustez)

1. El jugador devuelve texto crudo (o un `PlayerReply`). `parser.py` extrae la **última jugada válida** (JSON, también anidado, o
   `[APUESTA: n, f]` / `[DUDO]`) y descarta los bloques `<think>…</think>`.
2. `rules.validate_action` comprueba legalidad. Si falla (formato, ilegal, respuesta vacía, excepción/timeout del modelo), se reintenta hasta
   `--retries` veces (2 por defecto) **diciéndole al modelo el motivo y cuál es la apuesta legal más baja** (o que sólo puede dudar).
3. Si se agotan: `--penalty forced_move` (por defecto) el árbitro juega la apuesta legal más baja (o dudar si ya no se puede subir);
   `lose_die` además le quita un dado al cerrar la ronda. Todo queda contado en las estadísticas (`invalid_responses`, `retries`, `forced_moves`).
4. **Fallo de infraestructura** (Ollama caído, modelo inexistente, HTTP 4xx/5xx, no conecta): no es culpa del modelo. El árbitro emite
   `game_aborted`, la partida queda con `status=aborted` en `games.csv` (sin ganador) y `run_games.py` se detiene con código 2.
   Un timeout *leyendo* la respuesta sí cuenta como fallo del modelo.
   - **Reintento único ante HTTP 500:** el `llama-server` de Ollama a veces se cae en la primera petición tras cargar el modelo
     (se ha visto en Windows con `gemma4:e2b`) y Ollama lo reinicia solo. `OllamaPlayer` espera `retry_wait` s (2 por defecto) y
     reintenta **una vez**; si vuelve a fallar, aborta como arriba. Cada reintento queda en el log como evento `infrastructure`.
   - **Calentamiento:** antes de la primera partida `run_games.py` carga cada modelo distinto una vez (una generación mínima), para
     que la carga en frío no gaste el `timeout` de un turno real. Sus eventos van a `infrastructure.jsonl` (ver abajo). Si falla, no se
     juega nada (código 2). Se desactiva con `--no-warmup`.
5. Un error inesperado en una partida queda como `status=error` (+ `game_XXXX.error.txt`) y la ejecución sigue.

---

## Quién toca qué

| Archivo / carpeta | Dueño | Notas |
|---|---|---|
| `liars_dice/rules.py`, `game.py`, `parser.py`, `observation.py`, `probability.py` | **M1** (árbitro) | Si necesitáis un cambio aquí, pedídselo a M1 |
| `liars_dice/players/base.py`, `players/__init__.py`, `run_games.py` | **M1** | `make_player` ya acepta opciones nuevas de Ollama sin tocarlo |
| `liars_dice/prompts.py` | **M2** | `build_messages(obs, feedback, style)`; estilos nuevos en `STYLES` |
| `liars_dice/players/ollama_player.py` | **M2** | Opción nueva = parámetro en `__init__` + `describe()` + entrada en `OPTION_PARSERS` |
| Jugadores nuevos (`players/<nombre>.py`) | **M2** | Registrarlos en `make_player` es cambio de M1 (una línea) |
| `analysis/` | **M3** | Ver [analysis/README.md](analysis/README.md) |
| `tests/test_all.py`, `test_robustness.py`, `test_contract.py` | **M1** | M2 y M3: tests nuevos en archivos propios (`tests/test_prompts.py`, `tests/test_analysis.py`...) |
| `examples/` | **M1** | Se regenera cuando sube `SCHEMA_VERSION` |

Regla: si vuestro cambio obliga a editar un archivo de otro, no lo editéis; abrid un aviso al dueño. Los tests deben pasar antes de
integrar (`python -m pytest -q`).

## Qué es contrato estable

Estas piezas **no cambian sin avisar** al resto. Si cambian, se actualiza el test correspondiente y (si rompe a quien lee logs) se sube `SCHEMA_VERSION`.

### 1. `Player` (M1 ↔ M2) — `liars_dice/players/base.py`
- `act(obs: Observation, feedback: str | None) -> str | PlayerReply`. Devolver `str` sigue valiendo (los bots lo hacen).
- `PlayerReply(text, meta)`: `meta` es un dict JSON-serializable que el árbitro copia a `attempts[i].meta`.
  Clave con significado: `empty_reason` (por qué `text` vino vacío; se añade al error). `OllamaPlayer` rellena además
  `thinking`, `done_reason`, `prompt_tokens`, `completion_tokens`, `generation_s`, `total_s`, `load_s`.
- `describe() -> dict`: parámetros del jugador, se guardan en `game_start.players[i].params`.
- `InfrastructureError`: lanzadla cuando falle el *servidor*, no el modelo (aborta la partida).

### 2. `Observation` (M1 ↔ M2) — `liars_dice/observation.py`
`you, your_dice, round_number, turn_order, dice_counts, total_dice, current_bid, current_bidder, bids_this_round, past_rounds, can_dudo, min_bid`
(+ `to_dict()`, `unknown_dice`). Nunca contiene los dados ajenos de la ronda en curso.

### 3. Prompt (M2) — `prompts.build_messages(obs, feedback, style) -> [{"role","content"}]`

### 4. Formato de los logs (M1 → M3) — `schema_version = 1`
`logs/<fecha>/game_XXXX.jsonl`: un JSON por línea, siempre con `game_id` y `type`. **Añadir campos no rompe el contrato; quitar o renombrar sí.**
El detalle ejecutable está en `tests/test_contract.py` (`REQUIRED`).

| `type` | Campos principales |
|---|---|
| `game_start` | `schema_version`, `seed`, `dice_per_player`, `max_retries`, `penalty`, `history_rounds`, `players[{name, spec, params}]` |
| `round_start` | `round`, `starter`, `dice_counts`, `hands` (todas las manos; **sólo para análisis, nunca a un prompt**) |
| `turn` | `round`, `turn`, `player`, `your_dice`, `current_bid`, `attempts`, `forced`, `action`; y `own_count_of_face` + `p_bid_true` (si apuesta) o `p_challenged_bid_true` (si duda) |
| `challenge` | `bids`, `challenger`, `bidder`, `challenged_bid`, `real_count`, `bid_was_true`, `loser`, `revealed_hands`, `dice_counts_after` |
| `penalty` | `player`, `dice_left` (sólo `--penalty lose_die`) |
| `eliminated` | `player` |
| `infrastructure` | `round`, `turn`, `player`, `kind` (`retry`), `http_status`, `detail`, `recovered` (true si el reintento funcionó), `wait_s`. Va justo antes del `turn` al que pertenece |
| `game_aborted` | `reason` (fallo de infraestructura) |
| `game_end` | `winner` (null si abortó), `rounds`, `stats` por jugador, `aborted` |

Cada elemento de `attempts`: `{raw, error, latency_s, meta}` — `raw` es el texto, `error` es null si fue válida, `meta` es lo que devolvió el jugador (p. ej. `thinking` y tokens).
Interpretación para análisis: **farol** ≈ `p_bid_true` baja; **calidad de la duda** = `p_challenged_bid_true` (probabilidad de que la apuesta dudada fuera cierta).
`stats[jugador]`: `bids, dudos, invalid_responses, retries, forced_moves, penalty_dice_lost, dice_lost_in_challenges, infra_retries`.

`logs/<fecha>/infrastructure.jsonl` (nivel de ejecución, no de partida): eventos del calentamiento previo, uno por línea:
`{"type":"infrastructure","spec":…,"kind":"warmup","ok":true,"seconds":…,"load_s":…}`, más los `retry` que ocurran al calentar.
Está vacío si sólo juegan bots.

### 5. `games.csv`
Columnas: `game_id, seed, winner_name, winner_spec, rounds, seating, status, detail`. `status` ∈ `ok | aborted | error`.
`seating` = `J1=<spec>|J2=<spec>|…` (partid cada elemento por el **primer** `=`: el spec puede contener `=`).
Las filas no-`ok` no tienen ganador y **no deben contarse como victorias**.

## Estructura

```
liars_dice/rules.py        reglas puras (Bid, Action, legalidad, resolución)
liars_dice/probability.py  binomial: P(apuesta cierta)
liars_dice/parser.py       texto del modelo -> Action
liars_dice/observation.py  lo que ve un jugador
liars_dice/game.py         Referee: rondas, reintentos, penalización, eventos, SCHEMA_VERSION
liars_dice/players/        base (Player, PlayerReply), baselines (random, probbot), ollama_player, make_player
liars_dice/prompts.py      prompts (M2)
run_games.py               N partidas + logs
examples/sample_run/       log de ejemplo (random y probbot)
analysis/                  análisis (M3)
tests/                     pytest
TODO.md                    pendientes conocidos
```
