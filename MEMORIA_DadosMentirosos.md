# 🎲 Memoria: Dados mentirosos entre modelos locales

Árbitro del juego de los Dados Mentirosos (Liar's Dice o Perudo) en el que los jugadores pueden ser modelos de lenguaje locales (pesos abiertos, servidos con Ollama) o bots sin inteligencia artificial.

---

## Índice

1. [En qué consiste el juego](#1-en-qué-consiste-el-juego)
2. [Cómo funciona el programa](#2-cómo-funciona-el-programa)
3. [Experimento](#3-experimento)
4. [Resultados de la prueba piloto](#4-resultados-de-la-prueba-piloto)
5. [Conclusiones](#5-conclusiones)
6. [Anexo: ejemplos reales sacados de los registros](#anexo-ejemplos-reales-sacados-de-los-registros)

---

## 1. En qué consiste el juego

Hemos programado un árbitro del juego de los Dados Mentirosos en el que los jugadores pueden ser modelos de lenguaje locales. El programa reparte los dados, da los turnos, valida las jugadas, resuelve las dudas y registra todo, para poder medir cómo juega cada modelo.

Cada jugador empieza con cinco dados que solo ve él. Por turnos, cada jugador **apuesta** una cantidad y una cara (*«hay al menos 3 dados con un 4 entre todos los jugadores»*, contando los suyos y los ajenos) subiendo la apuesta anterior, o **duda** de la apuesta anterior.

Al dudar se destapan todos los dados:

- Si hay al menos los dados apostados, la apuesta era cierta y **pierde un dado quien dudó**.
- Si no, **pierde un dado quien apostó**.

Gana el último jugador con dados.

---

## 2. Cómo funciona el programa

### Qué sabe cada jugador
El árbitro construye para cada turno un objeto `Observation` que contiene solo los dados del propio jugador. Los dados ajenos de la ronda en curso no existen dentro de ese objeto, así que ningún modelo puede verlos. Sí ve la información pública: dados vivos de cada jugador, apuestas de la ronda, la apuesta legal más baja y las últimas tres rondas ya destapadas, que se entregan como copias para que nadie pueda alterar el estado del árbitro.

### Cómo se verifica que una jugada es legal
Las reglas están en un módulo aparte, sin estado y sin inteligencia artificial (`rules.py`), que comprueba la cara, la cantidad, el máximo de dados y que la apuesta supere a la anterior. El árbitro no se fía de ningún jugador: toda respuesta pasa por esa validación.

### Qué pasa cuando un modelo responde algo imposible
Un lector de respuestas (`parser.py`) extrae la última jugada válida del texto, admite varios formatos y descarta el razonamiento entre etiquetas de pensamiento.

Si la jugada es ilegal, está mal formada o llega vacía:

1. El árbitro la rechaza y le explica al modelo el motivo y cuál es la apuesta legal más baja.
2. Le deja reintentar hasta dos veces.
3. Si se agotan los intentos, el árbitro juega por él la apuesta legal más baja (o duda si ya no se puede subir); opcionalmente, además, le quita un dado.

Todo queda contado en las estadísticas. Si falla el servidor y no el modelo (Ollama apagado, modelo inexistente), la partida se aborta y se anota sin ganador, para no atribuir al modelo un fallo que no es suyo.

### Cómo se mide si un modelo calcula probabilidades, farolea o solo repite
En cada jugada el árbitro calcula, con la distribución binomial, la probabilidad de que la apuesta sea cierta dados los dados de quien la hace, y la guarda en el registro (`p_bid_true`; al dudar, `p_challenged_bid_true`).

- Una apuesta de probabilidad baja se interpreta como un **farol** (o como un error de cálculo).
- Una duda sobre una apuesta muy probable se interpreta como una **mala duda**.

Además se registran los intentos inválidos, las jugadas forzadas y el texto crudo de cada respuesta.

### Reproducibilidad
Cada partida usa una semilla para los dados, y el registro guarda la configuración de cada jugador. Con `--rotate` se rotan los asientos entre partidas para anular la ventaja de salir primero. Cada partida se guarda en un archivo con un evento por línea y una tabla resumen (`games.csv`).

### Ejecución
Se puede lanzar desde la línea de comandos (`run_games.py`) o con una aplicación local con interfaz en el navegador, que muestra la partida en una mesa animada y guarda los mismos registros. Antes de la primera partida se «calienta» cada modelo para que la carga en frío no cuente como un fallo del modelo.

### Prompts (estilos)
Cada modelo recibe las reglas, sus dados y la situación de la mesa, y se le pide que responda con una jugada en formato JSON. Sobre ese prompt base añadimos estilos que cambian la «personalidad» del modelo:

| Estilo | Qué pide al modelo |
|---|---|
| `basic` | Nada extra (grupo de control) |
| `cot` | Razonar paso a paso antes de jugar |
| `agresivo` | Farolear con frecuencia |
| `conservador` | Apostar solo con seguridad |
| `probabilista` | Comparar la apuesta con el número esperado de dados |

Se elige con `@` en el nombre del jugador, por ejemplo `ollama:gemma4:e2b@agresivo`.

---

## 3. Experimento

### Prueba piloto (realizada)

> **Pregunta:** ¿cómo juega un modelo pequeño (`qwen2.5:3b`) frente a dos referencias sin inteligencia artificial: un bot que juega al azar entre las jugadas legales y un bot estadístico que duda cuando la apuesta es poco probable?

- Partidas de tres jugadores con cinco dados cada uno, con rotación de asientos (en la piloto, el modelo ocupó cada asiento una vez).
- Jugadores de la piloto: `probbot`, `random` y `qwen2.5:3b`.
- Métricas: victorias, orden de eliminación, probabilidad de las apuestas (`p_bid_true`), dudas hechas y acertadas, y respuestas inválidas.

---

## 4. Resultados de la prueba piloto

Partidas:

| Partida | J1 | J2 | J3 |
|---|---|---|---|
| 1 | probbot | random | qwen |
| 2 | random | qwen | probbot |
| 3 | qwen | probbot | random |

Resultados por jugador:

| Jugador | Victorias | Apuestas | Probabilidad media de sus apuestas | Apuestas con prob. ≤ 20 % | Dudas (acertadas) | Respuestas inválidas |
|---|---|---|---|---|---|---|
| `probbot` | 3 de 3 | 31 | 87,8 % | 1 (3 %) | 28 (24, 86 %) | 0 |
| `random` | 0 de 3 | 38 | 50,7 % | 13 (34 %) | 6 (0, 0 %) | 0 |
| `qwen2.5:3b` | 0 de 3 | 34 | 43,3 % | 12 (35 %) | 0 | 4 |

*«Dudas acertadas» son aquellas en las que la apuesta dudada resultó falsa.*

**Orden de eliminación:** en las tres partidas qwen fue el primer jugador eliminado (rondas 6, 9 y 8) y random el segundo.

### Tres observaciones sobre qwen2.5:3b

1. **Nunca dudó.** Tuvo 21 turnos en los que podía dudar y en los 21 subió la apuesta. Sus 13 apuestas restantes fueron aperturas de ronda, donde no se puede dudar.
2. **Apostó con probabilidades bajas.** Su probabilidad media (43 %) fue incluso inferior a la del bot aleatorio (51 %), y un 35 % de sus apuestas tenían una probabilidad del 20 % o menos. En la partida 3 abrió dos rondas seguidas con una apuesta de cantidad 4 y probabilidad cercana al 3 %.
3. **Cometió errores de formato o legalidad.** Cuatro de sus respuestas fueron inválidas y hubo que reintentar; los bots no tuvieron ninguna.

El bot estadístico ganó las tres partidas apostando casi siempre con alta probabilidad (el 81 % de sus apuestas, con probabilidad del 80 % o más) y dudando con acierto en 24 de 28 ocasiones.

---

## 5. Conclusiones

Que `probbot` ganase 3 de 3 es compatible con que sea mejor, pero también con la suerte de los dados. Lo que sí muestra la piloto es que el sistema de medida funciona y detecta diferencias de comportamiento. Lo que sí muestran los datos y los ejemplos del [anexo](#anexo-ejemplos-reales-sacados-de-los-registros):

- **`qwen2.5:3b` se comporta como un modelo que no calcula probabilidades.** En los 21 turnos en que podía dudar, no dudó ninguna vez, y la probabilidad media de sus apuestas (43 %) fue inferior a la de un bot aleatorio (51 %). Se parece más a «repetir y subir jugadas» que a razonar sobre los dados.
- **Una regla estadística sencilla basta para superar a un modelo pequeño sin razonamiento.** `probbot` duda cuando la apuesta anterior es cierta con menos de un 35 % de probabilidad: el 81 % de sus apuestas tuvo una probabilidad del 80 % o más y acertó 24 de 28 dudas. Será la línea base del experimento principal: un estilo de prompt solo cuenta como mejora si se acerca a ese bot.
- **Las métricas del registro separan lo que pedía el enunciado.** Un farol aparece como `p_bid_true` bajo (anexo, ejemplo 1: apuesta de dos cuatros sin tener ninguno, 2,8 %) y la calidad de una duda como `p_challenged_bid_true`. El ejemplo 4 muestra además que una duda bien calculada puede salir mal por azar (dudó con un 16,7 % y la apuesta era cierta), así que un modelo debe juzgarse por muchas partidas, no por una.
- **El sistema es robusto y reproducible.** Un timeout se reintenta y no se cuenta como derrota (ejemplo 2), un modelo mal escrito se detecta en el calentamiento y no se atribuye a ningún jugador (ejemplo 3), y con la misma semilla salen las mismas manos y las mismas jugadas (ejemplo 5).

---

## Anexo: ejemplos reales sacados de los registros

> Los ejemplos siguientes salen de ejecuciones de prueba (2 dados por jugador, una partida cada una), **no de la prueba piloto**. Sirven para ver cómo queda cada cosa en los archivos de registro.

### 1. Una partida leída del registro

Partida de `gemma4:e2b` (J1) contra `qwen3.5:4b` (J2), con 2 dados cada uno y semilla 0. En la ronda 1, J1 tiene [4, 4] y J2 tiene [1, 3]. Cada turno queda guardado así (turno 4 de esa ronda, jugado por J2):

```json
{"type": "turn", "round": 1, "turn": 4, "player": "J2", "your_dice": [1, 3],
 "current_bid": {"quantity": 1, "face": 4},
 "action": {"action": "bid", "quantity": 2, "face": 4},
 "own_count_of_face": 0, "p_bid_true": 0.027778}
```

J2 apuesta «dos cuatros» sin tener ninguno: para que fuera cierta, los dos dados de J1 tendrían que ser cuatros, algo que pasa con probabilidad 1/36 (≈ 2,8 %). **Es un farol.** Después J1 sube a 2 × 5, también con un 2,8 %. J2 duda y el registro guarda `p_challenged_bid_true` = 0,027778: **una buena duda**. Al destapar no hay ningún cinco, así que pierde un dado J1. En la ronda 2 ocurre algo parecido (J2 vuelve a dudar una apuesta con probabilidad 0 %) y J1 queda eliminado: gana J2 en 2 rondas.

### 2. Un fallo de un modelo, con reintento

En otra ejecución, `qwen3.5:4b` jugó sin `think=false`, es decir, razonando. En uno de sus turnos el primer intento quedó registrado con el error *«Timeout de 120.0s esperando a qwen3.5:4b»* y el árbitro le dejó reintentar. El segundo intento fue válido (`{"action": "dudo"}`), pero llegó tras 651 tokens de razonamiento y unos 86 s más: entre los dos intentos, una sola jugada tardó más de 3 minutos. Por eso existe el «modo rápido» con `think=false`.

### 3. Un fallo de infraestructura

Al lanzar una ejecución con un nombre de modelo mal escrito (`qwen2.5:lastest`), el calentamiento previo registró en `infrastructure.jsonl` `ok: false` con el mensaje *«Ollama respondió HTTP 404 ... model 'qwen2.5:lastest' not found»*. Como el fallo es de la configuración y no del modelo, no se jugó ninguna partida ni se creó `games.csv`, y no se le atribuyó nada a ningún jugador.

### 4. El bot estadístico en acción

`probbot` tiene un umbral de duda de 0,35 (`dudo_threshold` en el registro): duda cuando la apuesta anterior es cierta con menos de un 35 % de probabilidad. En una partida contra `gemma4:e2b`:

- En la ronda 1 dudó una apuesta con un 30,6 % y acertó.
- En la ronda 2 dudó otra con un 16,7 % y la apuesta resultó cierta (J1 tenía un 1), así que perdió un dado: **una decisión bien calculada puede salir mal por azar**.

Ganó en 3 rondas. En ese registro `gemma4:e2b` no dudó nunca.

### 5. Reproducibilidad

Dos ejecuciones distintas (7 y 8 de noviembre) con la misma configuración (`gemma4:e2b` contra `probbot`, semilla 1) dieron las mismas manos en cada ronda y la misma secuencia de jugadas, incluidas las del modelo.
