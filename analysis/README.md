# analysis/

Carpeta del **Miembro 3** (experimento y análisis). Todo lo que lea los logs va aquí.

- Entrada: `logs/<fecha>/game_*.jsonl` y `games.csv`. Mientras no haya partidas con modelos, usad
  [`../examples/sample_run/`](../examples/sample_run/).
- El formato de los logs es contrato estable: ver "Formato de los logs" en el [README principal](../README.md).
  Comprobad `schema_version` en el evento `game_start` antes de leer.
- Ignorad en `games.csv` las filas con `status` distinto de `ok` (partidas abortadas o con error).
- `round_start.hands` y `turn.your_dice` existen sólo para analizar: no los uséis para construir prompts.
- Tests de vuestro código en `tests/test_analysis.py` (archivo propio, para no pisar los de M1).
