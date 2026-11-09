# Notas de M2: estilos de prompt

Trabajo sobre `liars_dice/prompts.py`. Rama: `miembro2-prompts`.

> `liars_dice/players/ollama_player.py` (también parte de M2) lo lleva otro compañero del equipo.
> Este documento solo recoge la parte de prompts.

## Hecho

Se han añadido cuatro estilos al diccionario `STYLES`, además del `basic` que ya existía.
No se ha tocado `RULES`, `FORMAT`, `describe_state` ni `build_messages` (contrato con M1).

| Estilo | Qué pide al modelo | Qué esperamos medir |
|---|---|---|
| `basic` | Nada extra (grupo de control) | Comportamiento por defecto |
| `cot` | Razonar brevemente paso a paso y acabar con el JSON en la última línea | ¿Razonar mejora el resultado? |
| `agresivo` | Farolear con frecuencia y dudar poco | ¿Farolea más? (`p_bid_true` bajo en las apuestas) |
| `conservador` | Apostar solo con seguridad y dudar ante apuestas exageradas | ¿Farolea menos y duda más? |
| `probabilista` | Comparar la apuesta con el número esperado de dados (N/6) | ¿Decide con sentido estadístico? (`p_challenged_bid_true`) |

Los tests existentes (`python -m pytest -q`) pasan con los cambios.

## Cómo se usa

El estilo se elige con `@` en el spec del jugador:

```
python run_games.py --players "ollama:gemma4:e2b@agresivo?think=false" "ollama:gemma4:e2b@conservador?think=false" --games 20 --rotate
```

Para que la comparación sea justa, entre dos jugadores debe cambiar **solo el estilo**. El modelo y las demás
opciones (`think`, `temperature`, `num_predict`...) deben ser iguales.

## Avisos para quien ejecute el experimento

- **Un estilo mal escrito no da error.** `build_messages` usa `STYLES.get(style, "")`, así que un nombre como
  `agresiov` se ejecuta como `basic` sin avisar. Revisar los nombres antes de lanzar partidas.
- **`cot` y `num_predict`.** `cot` necesita espacio para razonar antes del JSON. Con un `num_predict` bajo la
  respuesta se corta sin jugada. Probar con `num_predict=400` o más.
- **`cot` y `json_mode`.** `json_mode=true` obliga a responder solo con JSON y no combina con razonar antes.
  No usarlos juntos.
- **`cot` y `think`.** Si el modelo ya razona por su cuenta (`think=true`), la comparación `cot` frente a `basic`
  se mezcla con ese razonamiento interno. Para aislar el efecto del prompt, usar `think=false` en todos los jugadores.

## Pendiente

- [ ] Probar cada estilo con un modelo real (necesita Ollama) y comprobar en el log (`game_XXXX.jsonl`) que
      responde con jugadas válidas.
- [ ] Comprobar que `cot` acaba siempre con el JSON y no se corta por `num_predict`.
- [ ] Ajustar el texto de los estilos si el modelo no los sigue (por ejemplo, si `agresivo` no farolea).
- [ ] Crear `tests/test_prompts.py` (archivo propio, según el reparto del README) comprobando que cada estilo de
      `STYLES` genera mensajes válidos y que el texto del estilo aparece en el mensaje de sistema.
- [ ] Proponer a M1 que un estilo desconocido falle al arrancar `run_games.py` en vez de usar `basic` en silencio
      (el cambio iría en `players/__init__.py`, que es de M1).

## Para el experimento (con M3)

- Preguntas posibles: ¿`cot` gana a `basic`? ¿`agresivo` farolea más que `conservador`?
- Ejecutar siempre con `--rotate` para anular la ventaja de salir primero.
- Usar las mismas semillas y el mismo número de partidas en cada comparación.
