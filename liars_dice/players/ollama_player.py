"""Jugador que consulta un modelo local servido por Ollama (http://localhost:11434).

Sólo usa la librería estándar. Probado contra Ollama real con gemma4:e2b.

TERRITORIO DEL MIEMBRO 2. Para añadir una opción nueva basta con tocar ESTE archivo:
1) un parámetro en `OllamaPlayer.__init__` (y en `describe()` para que se registre en el log),
2) una entrada en `OPTION_PARSERS` (cómo convertir el texto del spec a su tipo).
`make_player` la acepta sola en el spec: ollama:<modelo>[@<estilo>][?clave=valor+clave=valor].

Sobre `think`: modelos como gemma4 o qwen3 razonan por defecto y Ollama devuelve ese
razonamiento en `message.thinking` (no en `content`). Razonar cuesta ~25-50 s por turno en
un modelo de 2-4B. `think=false` lo desactiva; `think=true` lo fuerza; sin indicar, manda el
modelo. OJO: con `num_predict` bajo y razonamiento activo, el razonamiento puede consumir
todo el presupuesto y `content` llega vacío (el error lo indica).
"""
from __future__ import annotations

import json
import socket
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Optional

from .. import prompts
from ..observation import Observation
from .base import InfrastructureError, Player, PlayerReply


def _parse_bool(text: str) -> bool:
    t = str(text).strip().lower()
    if t in ("1", "true", "yes", "si", "sí", "on"):
        return True
    if t in ("0", "false", "no", "off"):
        return False
    raise ValueError(f"no es un booleano: {text!r} (usa true/false)")


def _positive(conv: Callable[[str], Any], what: str) -> Callable[[str], Any]:
    def parse(text: str):
        value = conv(text)
        if value <= 0:
            raise ValueError(f"{what} debe ser > 0 (recibido {value})")
        return value
    return parse


def _non_negative_float(text: str) -> float:
    value = float(text)
    if value < 0:
        raise ValueError(f"temperature debe ser >= 0 (recibido {value})")
    return value


# opción del spec -> conversor del texto. Añadid aquí las nuevas.
OPTION_PARSERS: dict[str, Callable[[str], Any]] = {
    "temperature": _non_negative_float,
    "json_mode": _parse_bool,
    "num_predict": _positive(int, "num_predict"),
    "timeout": _positive(float, "timeout"),
    "think": _parse_bool,
    "host": str,
    "retry_wait": _non_negative_float,        # segundos de espera antes del reintento tras HTTP 500
    "warmup_timeout": _positive(float, "warmup_timeout"),  # segundos máx. para cargar el modelo
}


def parse_options(raw: dict[str, str]) -> dict[str, Any]:
    """Convierte {"think": "false", "num_predict": "300"} en kwargs tipados de `OllamaPlayer`."""
    out: dict[str, Any] = {}
    for key, text in raw.items():
        if key not in OPTION_PARSERS:
            raise ValueError(f"opción de ollama desconocida: {key!r}. Válidas: {sorted(OPTION_PARSERS)}")
        try:
            out[key] = OPTION_PARSERS[key](text)
        except ValueError as exc:
            raise ValueError(f"valor inválido para {key}={text!r}: {exc}") from exc
    return out


def _secs(ns: Any) -> Optional[float]:
    return round(ns / 1e9, 4) if isinstance(ns, (int, float)) else None


class OllamaPlayer(Player):
    def __init__(
        self,
        name: str,
        model: str,
        style: str = "basic",
        temperature: float = 0.7,
        host: str = "http://localhost:11434",
        timeout: float = 120.0,
        json_mode: bool = False,
        num_predict: Optional[int] = None,
        think: Optional[bool] = None,
        seed: Optional[int] = None,
        spec: str = "",
        retry_wait: float = 2.0,
        warmup_timeout: float = 600.0,
    ):
        super().__init__(name, spec or f"ollama:{model}")
        self.model, self.style, self.host = model, style, host.rstrip("/")
        self.temperature, self.timeout = temperature, timeout
        self.json_mode, self.num_predict, self.seed = json_mode, num_predict, seed
        self.think = think
        self.retry_wait, self.warmup_timeout = retry_wait, warmup_timeout

    def describe(self) -> dict:
        return {
            "class": "OllamaPlayer", "model": self.model, "style": self.style,
            "temperature": self.temperature, "think": self.think, "json_mode": self.json_mode,
            "num_predict": self.num_predict, "timeout": self.timeout, "host": self.host,
            "seed": self.seed, "retry_wait": self.retry_wait, "warmup_timeout": self.warmup_timeout,
        }

    def act(self, obs: Observation, feedback: Optional[str] = None) -> PlayerReply:
        options: dict = {"temperature": self.temperature}
        if self.num_predict:
            options["num_predict"] = self.num_predict
        if self.seed is not None:
            options["seed"] = self.seed
        payload: dict = {
            "model": self.model,
            "messages": prompts.build_messages(obs, feedback, self.style),
            "stream": False,
            "options": options,
        }
        if self.think is not None:
            payload["think"] = self.think
        if self.json_mode:  # ojo: restringe la salida; no combina bien con razonamiento libre
            payload["format"] = "json"
        data, infra_events = self._post(payload, self.timeout)

        msg = data.get("message") or {}
        content = msg.get("content") or ""
        thinking = msg.get("thinking") or ""
        meta: dict = {
            "done_reason": data.get("done_reason"),
            "prompt_tokens": data.get("prompt_eval_count"),
            "completion_tokens": data.get("eval_count"),
            "generation_s": _secs(data.get("eval_duration")),
            "total_s": _secs(data.get("total_duration")),
            "load_s": _secs(data.get("load_duration")),
        }
        if thinking:
            meta["thinking"] = thinking
        if not content.strip():
            meta["empty_reason"] = self._empty_reason(data.get("done_reason"), thinking)
        if infra_events:  # el árbitro lo saca de `meta` y lo registra como evento "infrastructure"
            meta["infra_events"] = infra_events
        return PlayerReply(content, meta)

    # ------------------------------------------------------------ transporte
    def _send(self, payload: dict, timeout: float) -> dict:
        req = urllib.request.Request(
            f"{self.host}/api/chat",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _post(self, payload: dict, timeout: float) -> tuple[dict, list]:
        """POST a /api/chat con UN reintento ante HTTP 500 (el `llama-server` de Ollama a veces
        se cae en la primera petición tras cargar el modelo y Ollama lo reinicia solo).
        Devuelve (respuesta, eventos_de_infraestructura). Si el reintento también falla, lanza
        InfrastructureError. Las excepciones llevan `.events` para que el árbitro los registre."""
        events: list = []

        def fail(exc_type, message: str, cause: Exception):
            if events:
                events[-1]["recovered"] = False
            err = exc_type(message)
            err.events = events
            raise err from cause

        for attempt in (1, 2):
            try:
                data = self._send(payload, timeout)
            except urllib.error.HTTPError as exc:  # p.ej. 404 modelo inexistente: configuración, no el modelo
                detail = exc.read().decode("utf-8", "replace")[:300] if hasattr(exc, "read") else ""
                if exc.code == 500 and attempt == 1:
                    events.append({"kind": "retry", "http_status": 500, "detail": detail,
                                   "recovered": False, "wait_s": self.retry_wait})
                    time.sleep(self.retry_wait)
                    continue
                fail(InfrastructureError, f"Ollama respondió HTTP {exc.code} ({self.model}): {detail}", exc)
            except (TimeoutError, socket.timeout) as exc:  # lectura demasiado lenta: fallo del modelo
                fail(RuntimeError, f"Timeout de {timeout}s esperando a {self.model}", exc)
            except urllib.error.URLError as exc:
                # Incluye el timeout al CONECTAR (en Windows un puerto cerrado tarda en rechazar):
                # servidor inalcanzable = infraestructura. Sólo el timeout al LEER es culpa del modelo.
                fail(InfrastructureError, f"No se pudo conectar con Ollama en {self.host}: {exc}", exc)
            if not isinstance(data, dict) or "error" in data:
                fail(InfrastructureError,
                     f"Ollama devolvió un error: {data.get('error') if isinstance(data, dict) else data!r}",
                     RuntimeError("respuesta de error"))
            if events:
                events[-1]["recovered"] = True
            return data, events
        raise AssertionError("inalcanzable")  # el bucle siempre devuelve o lanza

    def warmup(self) -> list:
        """Carga el modelo y hace una generación mínima ANTES de la primera partida, para que la
        carga en frío no consuma el `timeout` de un turno real ni se cuente como fallo del modelo.
        Devuelve eventos de infraestructura (reintentos y un evento `warmup`); lanza InfrastructureError."""
        payload: dict = {
            "model": self.model, "stream": False,
            "messages": [{"role": "user", "content": "Responde únicamente: ok"}],
            "options": {"temperature": 0, "num_predict": 1},
        }
        if self.think is not None:
            payload["think"] = self.think
        t0 = time.perf_counter()
        try:
            data, events = self._post(payload, max(self.timeout, self.warmup_timeout))
        except InfrastructureError as exc:
            exc.events = list(getattr(exc, "events", [])) + [
                {"kind": "warmup", "ok": False, "detail": str(exc), "model": self.model}]
            raise
        except RuntimeError as exc:  # no cargó en `warmup_timeout`: el servidor no está sano
            err = InfrastructureError(f"El calentamiento de {self.model} no terminó: {exc}")
            err.events = list(getattr(exc, "events", [])) + [
                {"kind": "warmup", "ok": False, "detail": str(err), "model": self.model}]
            raise err from exc
        events.append({"kind": "warmup", "ok": True, "model": self.model,
                       "seconds": round(time.perf_counter() - t0, 3), "load_s": _secs(data.get("load_duration"))})
        return events

    def _empty_reason(self, done_reason: Optional[str], thinking: str) -> str:
        if done_reason == "length":
            limit = f" (num_predict={self.num_predict})" if self.num_predict else ""
            if thinking:
                return (f"el modelo agotó el límite de tokens{limit} razonando ({len(thinking)} caracteres "
                        "en 'thinking') antes de escribir la jugada; sube num_predict o usa think=false.")
            return f"el modelo agotó el límite de tokens{limit} antes de escribir la jugada."
        if thinking:
            return (f"el modelo sólo devolvió razonamiento ({len(thinking)} caracteres en 'thinking') "
                    "y 'content' llegó vacío.")
        return "el modelo devolvió 'content' vacío."
