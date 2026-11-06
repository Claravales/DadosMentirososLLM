# TODO (pendientes conocidos, M1)

## Anotados a petición (no hechos)
- [ ] **Paralelismo**: las partidas van en serie; con ~40 s por turno (modelo razonando) una tirada larga es lenta. Valorar varias
      partidas a la vez (Ollama admite `OLLAMA_NUM_PARALLEL`) con un pool de hilos y escritura de logs por partida.
- [ ] **Reanudar**: si se interrumpe una ejecución hay que empezarla de cero. Permitir `--resume <carpeta>` saltando las partidas
      con `status=ok` ya en `games.csv`.
- [ ] **Timeout total**: `timeout` de `OllamaPlayer` es por lectura de socket, no límite del turno (3 intentos × timeout en el peor caso),
      y no hay límite por partida ni por árbitro. Añadir presupuesto de tiempo por turno/partida en el árbitro (hilo con cancelación).

## Otros
- [ ] Partidas espejo (dos specs iguales): `played` cuenta por asiento y `wins` por spec, así que las cifras del resumen no cuadran.
- [ ] Registrar la versión de Ollama y el digest del modelo en `game_start` (reproducibilidad).
- [x] ~~Calentar el modelo antes de la primera partida~~ (hecho: `run_games.py`, `--no-warmup` para saltarlo).
- [x] ~~Reintento único ante HTTP 500 de Ollama~~ (hecho, registrado como evento `infrastructure`).
- [ ] Si un modelo se descarga de memoria a mitad de una ejecución larga (`keep_alive`), no se vuelve a calentar.
- [ ] Configuración de jugadores con nombre en un archivo (JSON) para no repetir specs largos en la línea de comandos.
- [ ] `run_config.json` debería incluir `schema_version`.
- [ ] Ronda 3: repositorio git (`git init`, rama por miembro, remoto). **Pendiente de que M1 lo pida.**
