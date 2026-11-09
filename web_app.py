#!/usr/bin/env python3
"""Dados Mentirosos: aplicación con interfaz gráfica (se abre en el navegador, todo en local).

Uso normal: doble clic en "Dados Mentirosos.pyw" (o en el .exe). No hace falta terminal.
Desde terminal también vale:  python web_app.py [--port 9000] [--no-browser]

Solo librería estándar. El servidor escucha únicamente en 127.0.0.1 y se cierra solo al cerrar la pestaña.
Los registros de cada ejecución se guardan en  logs/<fecha>/  (mismo formato que run_games.py).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import traceback
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from liars_dice.game import PENALTIES, Referee
from liars_dice.players import make_player, split_spec
from liars_dice.runner import Stopped, WarmupError, check_specs, new_run_dir, run_series, warmup, write_run_config

FROZEN = getattr(sys, "frozen", False)  # True dentro del .exe de PyInstaller
RESOURCES = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
INDEX = RESOURCES / "liars_dice" / "web" / "index.html"
APP_DIR = Path(sys.executable).parent if FROZEN else Path(__file__).parent
LOGS = APP_DIR / "logs"
APP_ID = "dados-mentirosos"
IDLE_TIMEOUT = 120  # s sin señales de la pestaña antes de cerrar (las pestañas ocultas se ralentizan)
BYE_GRACE = 5       # s de espera tras cerrar la pestaña (por si solo era un F5)


def say(text: str) -> None:
    """print seguro: sin consola (pythonw / .exe --noconsole) stdout es None."""
    if sys.stdout:
        print(text)


class Run:
    """Estado de la ejecución en curso: lista de eventos que el navegador va consumiendo."""

    def __init__(self):
        self.lock = threading.Lock()
        self.events: list[dict] = []
        self.running = False
        self.error: str | None = None
        self.out_dir: Path | None = None
        self.stop = threading.Event()

    def push(self, ev: dict) -> None:
        with self.lock:
            self.events.append(ev)

    def snapshot(self, since: int) -> dict:
        with self.lock:
            return {"events": self.events[since:], "next": len(self.events), "running": self.running,
                    "error": self.error, "out_dir": str(self.out_dir) if self.out_dir else None}


RUN = Run()
LAST_SEEN = {"t": time.monotonic(), "bye": None}


DEFAULT_OLLAMA = "http://localhost:11434"


def _ollama_host(spec: str) -> str | None:
    """Host de Ollama de un spec 'ollama:...', o None si el spec no es de Ollama."""
    base, options = split_spec(spec)
    if not base.startswith("ollama:"):
        return None
    return options.get("host", DEFAULT_OLLAMA).rstrip("/")


def _ollama_tags(host: str, timeout: float = 3) -> list[dict] | None:
    """Modelos instalados ([{name, size}]), o None si el servidor no responde."""
    host = host.replace("localhost", "127.0.0.1")  # Ollama escucha en IPv4; 'localhost' puede probar IPv6 y tardar
    try:
        with urllib.request.urlopen(f"{host}/api/tags", timeout=timeout) as r:
            return json.load(r).get("models", [])
    except Exception:
        return None


def _ollama_models(host: str, timeout: float = 3) -> list[str] | None:
    """Nombres de los modelos instalados, o None si el servidor no responde."""
    tags = _ollama_tags(host, timeout)
    return None if tags is None else [m["name"] for m in tags]


def _start_ollama() -> bool:
    """Intenta arrancar Ollama en local. Devuelve False si no lo encuentra."""
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        if sys.platform == "win32":
            exe = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama app.exe"
            if exe.exists():
                subprocess.Popen([str(exe)], creationflags=flags)
                return True
        elif sys.platform == "darwin" and Path("/Applications/Ollama.app").exists():
            subprocess.Popen(["open", "-a", "Ollama"])
            return True
        binary = shutil.which("ollama")
        if binary:
            subprocess.Popen([binary, "serve"], creationflags=flags,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
    except OSError:
        pass
    return False


def _ensure_ollama(specs: list[str], stop: threading.Event | None = None, push=None) -> None:
    """Antes de jugar: Ollama en marcha (se arranca si hace falta) y modelos instalados."""
    stop = stop or RUN.stop
    push = push or RUN.push
    for host in dict.fromkeys(h for h in map(_ollama_host, specs) if h):
        models = _ollama_models(host)
        if models is None:
            local = "localhost" in host or "127.0.0.1" in host
            push({"type": "notice", "text": "Ollama no está en marcha. Intentando arrancarlo…"})
            if not (local and _start_ollama()):
                raise RuntimeError(f"No se puede conectar con Ollama en {host}. Ábrelo (icono de Ollama) y vuelve a intentarlo.")
            for _ in range(60):  # hasta ~60 s: la primera carga detecta la GPU
                time.sleep(1)
                if stop.is_set():
                    raise Stopped()
                models = _ollama_models(host)
                if models is not None:
                    break
            else:
                raise RuntimeError("Se intentó arrancar Ollama pero no responde. Ábrelo a mano y vuelve a intentarlo.")
        for spec in specs:
            if _ollama_host(spec) != host:
                continue
            model = split_spec(spec)[0][len("ollama:"):].partition("@")[0]
            wanted = model if ":" in model else model + ":latest"
            if wanted not in models:
                avail = ", ".join(models) or "ninguno"
                raise RuntimeError(f"El modelo '{model}' no está instalado en Ollama (instalados: {avail}). "
                                   f"Instálalo con:  ollama pull {model}")


def _new_out_dir() -> Path:
    """Carpeta de registros: junto a la app, o en ~/DadosMentirosos/logs si esa no es escribible."""
    global LOGS
    out = new_run_dir(LOGS, Path.home() / "DadosMentirosos" / "logs")
    LOGS = out.parent
    return out


def _worker(cfg: dict, stop: threading.Event) -> None:
    """Una ejecución. `stop` es propio de ESTA ejecución: tras pulsar Parar, esta hebra puede tardar en terminar
    (p. ej. esperando la respuesta de un modelo) sin afectar ni ensuciar la ejecución siguiente."""
    current = {"g": 0}

    def push(ev: dict) -> None:
        if not stop.is_set():
            RUN.push(ev)

    try:
        _ensure_ollama(cfg["players"], stop, push)
        out = _new_out_dir()
        RUN.out_dir = out
        write_run_config(out, {**cfg, "source": "web_app"})
        try:
            warmup(cfg["players"], out, make_player, on_start=lambda s: push({"type": "notice", "text": f"Preparando {s}…"}))
        except WarmupError as exc:
            raise RuntimeError(f"Calentamiento fallido para {exc.spec}: {exc.cause}") from exc

        def on_event(ev: dict) -> None:
            if stop.is_set():
                raise Stopped()
            RUN.push({**ev, "_g": current["g"], "_total": cfg["games"]})

        result = run_series(
            specs=cfg["players"], games=cfg["games"], seed=cfg["seed"], rotate=cfg["rotate"], out=out,
            referee=Referee(dice_per_player=cfg["dice"], max_retries=cfg["retries"], penalty=cfg["penalty"]),
            make_player=make_player,
            on_game_start=lambda g, gid, specs: current.update(g=g + 1),
            on_event=on_event,
            on_error=lambda gid, last: push({"type": "notice", "text": f"{gid}: error inesperado, se continúa ({last})"}),
        )
        if result.aborted:
            raise RuntimeError(f"Partida abortada por fallo de infraestructura: {result.aborted}")
    except Stopped:
        pass  # el aviso «Detenido» ya lo puso /api/stop
    except Exception as exc:  # noqa: BLE001 - se muestra en la interfaz
        if not stop.is_set():
            RUN.error = f"{type(exc).__name__}: {exc}"
    finally:
        if RUN.stop is stop:  # si ya hay otra ejecución nueva, no le toques su estado
            RUN.running = False


def stop_run() -> None:
    """Parar: efecto inmediato en la interfaz; la hebra termina sola en cuanto pueda."""
    with RUN.lock:
        if RUN.running:
            RUN.stop.set()
            RUN.running = False
            RUN.events.append({"type": "notice", "text": "Detenido."})


def start(body: dict) -> tuple[int, dict]:
    if RUN.running:
        return 409, {"error": "Ya hay una ejecución en marcha."}
    try:
        players = [str(p).strip() for p in body.get("players", []) if str(p).strip()]
        cfg = {
            "players": players,
            "games": max(1, min(int(body.get("games", 5)), 500)),
            "dice": max(1, min(int(body.get("dice", 5)), 10)),
            "seed": int(body.get("seed", 0)),
            "retries": max(0, min(int(body.get("retries", 2)), 10)),
            "penalty": body.get("penalty", "forced_move"),
            "rotate": bool(body.get("rotate", True)),
        }
    except (TypeError, ValueError) as exc:
        return 400, {"error": f"Parámetros inválidos: {exc}"}
    if len(players) < 2:
        return 400, {"error": "Hacen falta al menos 2 jugadores (uno por línea)."}
    if len(players) > 6:
        return 400, {"error": "Máximo 6 jugadores."}
    if cfg["penalty"] not in PENALTIES:
        return 400, {"error": f"Penalización inválida: {cfg['penalty']}"}
    try:
        check_specs(players, make_player)
    except ValueError as exc:
        return 400, {"error": str(exc)}
    with RUN.lock:
        RUN.events, RUN.error, RUN.out_dir = [], None, None
    RUN.stop = stop = threading.Event()  # un Event nuevo por ejecución
    RUN.running = True
    threading.Thread(target=_worker, args=(cfg, stop), daemon=True).start()
    return 200, {"ok": True}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):  # silencio
        pass

    def _send(self, code: int, payload, ctype: str = "application/json") -> None:
        data = payload if isinstance(payload, bytes) else json.dumps(payload, ensure_ascii=False, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", f"{ctype}; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _trusted(self) -> bool:
        """Solo aceptamos peticiones de nuestra propia página (evita que otra web abierta en el navegador
        maneje la app por CSRF o DNS rebinding)."""
        hostname = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]")
        if hostname not in ("127.0.0.1", "localhost"):
            return False
        origin = self.headers.get("Origin")
        return origin is None or (urlparse(origin).hostname in ("127.0.0.1", "localhost"))

    def do_GET(self):
        if not self._trusted():
            return self._send(403, {"error": "prohibido"})
        url = urlparse(self.path)
        LAST_SEEN["t"], LAST_SEEN["bye"] = time.monotonic(), None
        if url.path in ("/", "/index.html"):
            self._send(200, INDEX.read_bytes(), "text/html")
        elif url.path == "/api/ping":
            self._send(200, {"app": APP_ID})
        elif url.path == "/api/models":
            tags = _ollama_tags(DEFAULT_OLLAMA, timeout=2)
            self._send(200, {"ollama": tags is not None,
                             "models": [{"name": m["name"], "gb": round(m.get("size", 0) / 1e9, 1)} for m in tags or []]})
        elif url.path == "/api/events":
            try:
                since = int(parse_qs(url.query).get("since", ["0"])[0])
            except ValueError:
                since = 0
            self._send(200, RUN.snapshot(max(0, since)))
        else:
            self._send(404, {"error": "no encontrado"})

    def do_POST(self):
        if not self._trusted():
            return self._send(403, {"error": "prohibido"})
        url = urlparse(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            return self._send(400, {"error": "JSON inválido"})
        if url.path == "/api/start":
            code, payload = start(body)
            self._send(code, payload)
        elif url.path == "/api/stop":
            stop_run()
            self._send(200, {"ok": True})
        elif url.path == "/api/bye":  # la pestaña se está cerrando
            LAST_SEEN["bye"] = time.monotonic()
            self._send(200, {"ok": True})
        elif url.path == "/api/open_logs":
            target = RUN.out_dir or LOGS
            target.mkdir(parents=True, exist_ok=True)
            try:
                if sys.platform == "win32":
                    os.startfile(target)  # type: ignore[attr-defined]
                else:
                    subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(target)])
                self._send(200, {"ok": True})
            except Exception:
                self._send(200, {"ok": False, "path": str(target)})
        else:
            self._send(404, {"error": "no encontrado"})


def _watchdog(server: ThreadingHTTPServer) -> None:
    """Cierra la aplicación cuando ya no hay ninguna pestaña abierta."""
    while True:
        time.sleep(1)
        now = time.monotonic()
        bye = LAST_SEEN["bye"]
        if (bye and now - bye > BYE_GRACE) or now - LAST_SEEN["t"] > IDLE_TIMEOUT:
            stop_run()
            RUN.stop.set()
            server.shutdown()
            return


def _already_running(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/ping", timeout=1) as r:
            return json.load(r).get("app") == APP_ID
    except Exception:
        return False


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args(argv)
    if _already_running(args.port):  # segundo doble clic: solo vuelve a abrir la pestaña
        if not args.no_browser:
            webbrowser.open(f"http://127.0.0.1:{args.port}")
        return 0
    server = None
    for port in range(args.port, args.port + 20):
        try:
            server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
            break
        except OSError:
            continue
    if server is None:
        say("No hay ningún puerto libre.")
        return 1
    url = f"http://127.0.0.1:{server.server_address[1]}"
    say(f"Dados Mentirosos en {url}  (Ctrl+C para salir)")
    LAST_SEEN["t"] = time.monotonic()
    threading.Thread(target=_watchdog, args=(server,), daemon=True).start()
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:  # sin consola el error se perdería: déjalo en un archivo
        (APP_DIR / "error.log").write_text(traceback.format_exc(), encoding="utf-8")
        raise
