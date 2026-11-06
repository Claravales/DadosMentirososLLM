# examples/

`sample_run/` es una ejecución real del árbitro con jugadores sin IA (no necesita Ollama), para poder
desarrollar el análisis sin esperar a los modelos:

```bash
python run_games.py --players random probbot probbot:0.3 --games 2 --seed 1 --dice 3 --rotate --out examples
# renombrad la carpeta con fecha que se crea a examples/sample_run
```

Contiene `game_0001.jsonl`, `game_0002.jsonl` (un evento por línea), `games.csv` y `run_config.json`.
Los campos `meta` de `attempts` salen vacíos porque los bots no devuelven metadatos; un modelo de Ollama
rellena ahí `thinking`, tokens y tiempos (ver el README principal, "Formato de los logs").

`tests/test_contract.py` valida este directorio: si cambia el formato del log, el test falla hasta que se
regenere el ejemplo (y, si el cambio rompe a quien lee logs, se suba `SCHEMA_VERSION`).
