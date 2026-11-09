"""Interfaz de consola: colores ANSI, dados Unicode, cajas, progreso y resumen final.

Solo librería estándar. Se degrada sola: sin color si no hay TTY / NO_COLOR / --no-color,
y con ASCII si la consola no admite Unicode.
"""
from __future__ import annotations

import os
import re
import shutil
import sys

_ANSI = re.compile(r"\x1b\[[0-9;]*m")

# paleta (códigos SGR); 256 colores para un aspecto más moderno
_STYLES = {
    "dim": "2", "bold": "1", "ital": "3",
    "red": "38;5;203", "green": "38;5;78", "yellow": "38;5;221", "blue": "38;5;75",
    "magenta": "38;5;177", "cyan": "38;5;80", "grey": "38;5;244", "white": "38;5;255",
}
# un color distinto por jugador (J1, J2, ...)
_PLAYER_COLORS = ["cyan", "magenta", "yellow", "green", "blue", "red"]


def setup_console() -> None:
    """Fuerza UTF-8 y activa las secuencias ANSI en la consola de Windows. Nunca lanza."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    if os.name == "nt":
        try:  # truco clásico: cmd/PowerShell activan el modo VT tras una llamada a system()
            os.system("")
        except Exception:
            pass


def detect_color(force_off: bool = False, stream=None) -> bool:
    if force_off or os.environ.get("NO_COLOR"):
        return False
    stream = stream or sys.stdout
    try:
        return bool(stream.isatty())
    except Exception:
        return False


def detect_unicode(stream=None) -> bool:
    stream = stream or sys.stdout
    enc = (getattr(stream, "encoding", None) or "").lower()
    return "utf" in enc


def fmt_time(seconds: float) -> str:
    seconds = int(max(0, seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}h{m:02d}m" if h else (f"{m}m{s:02d}s" if m else f"{s}s")


class Style:
    def __init__(self, color: bool, unicode_ok: bool):
        self.color = color
        self.u = unicode_ok
        self.width = max(60, min(shutil.get_terminal_size((100, 20)).columns, 100))
        if unicode_ok:
            self.dice = "⚀⚁⚂⚃⚄⚅"
            self.h, self.v = "─", "│"
            self.tl, self.tr, self.bl, self.br = "╭", "╮", "╰", "╯"
            self.full, self.empty = "●", "○"
            self.bar_full, self.bar_empty = "█", "░"
            self.arrow, self.times, self.dot, self.ok, self.bad = "▸", "×", "·", "✔", "✘"
            self.crown, self.skull = "♛", "☠"
        else:
            self.dice = "123456"
            self.h, self.v = "-", "|"
            self.tl = self.tr = self.bl = self.br = "+"
            self.full, self.empty = "o", "."
            self.bar_full, self.bar_empty = "#", "-"
            self.arrow, self.times, self.dot, self.ok, self.bad = ">", "x", "-", "OK", "X"
            self.crown, self.skull = "*", "x"

    def c(self, text, *styles: str) -> str:
        if not self.color or not styles:
            return str(text)
        codes = ";".join(_STYLES[s] for s in styles)
        return f"\x1b[{codes}m{text}\x1b[0m"

    @staticmethod
    def _color_of(name: str) -> str:
        try:
            idx = int(str(name).lstrip("J")) - 1
        except ValueError:
            idx = 0
        return _PLAYER_COLORS[idx % len(_PLAYER_COLORS)]

    def player(self, name: str, bold: bool = True) -> str:
        col = self._color_of(name)
        return self.c(name, col, "bold") if bold else self.c(name, col)

    def die(self, face: int, hot: bool = False, dim: bool = False) -> str:
        glyph = self.dice[face - 1] if 1 <= face <= 6 else "?"
        if not self.u:
            glyph = f"[{glyph}]"
        if hot:
            return self.c(glyph, "yellow", "bold")
        if dim:
            return self.c(glyph, "grey")
        return self.c(glyph, "white")

    def dice_row(self, dice, hot_face: int | None = None) -> str:
        return " ".join(self.die(d, hot=(d == hot_face), dim=(hot_face is not None and d != hot_face)) for d in dice)

    def pips(self, left: int, total: int, name: str) -> str:
        col = self._color_of(name)
        return self.c(self.full * left, col) + self.c(self.empty * max(0, total - left), "grey")

    def bar(self, frac: float, width: int = 20, color: str = "green") -> str:
        frac = max(0.0, min(1.0, frac))
        n = round(frac * width)
        return self.c(self.bar_full * n, color) + self.c(self.bar_empty * (width - n), "grey")

    def rule(self, label: str = "", color: str = "grey") -> str:
        if not label:
            return self.c(self.h * self.width, color)
        lab = f" {label} "
        rest = self.width - 3 - _vlen(lab)
        return self.c(self.h * 3, color) + lab + self.c(self.h * max(0, rest), color)

    def box(self, lines: list[str], color: str = "cyan") -> str:
        inner = max(_vlen(l) for l in lines) + 2
        inner = max(inner, 40)
        top = self.c(self.tl + self.h * inner + self.tr, color)
        bot = self.c(self.bl + self.h * inner + self.br, color)
        body = [self.c(self.v, color) + " " + l + " " * (inner - 1 - _vlen(l)) + self.c(self.v, color) for l in lines]
        return "\n".join([top, *body, bot])


def _vlen(text: str) -> int:
    return len(_ANSI.sub("", text))


def _pad(text: str, width: int, right: bool = False) -> str:
    gap = " " * max(0, width - _vlen(text))
    return gap + text if right else text + gap


class ConsoleView:
    """Convierte los eventos del árbitro en una salida legible. `level`: 0 silencioso, 1 normal, 2 detallado."""

    def __init__(self, style: Style, level: int = 1, out=None):
        self.s = style
        self.level = level
        self.out = out or sys.stdout
        self.current_bid: dict | None = None
        self.players: list[dict] = []
        self.dice_total = 0

    # ------------------------------------------------------------------ utilidades
    def p(self, text: str = "") -> None:
        print(text, file=self.out)

    def _bid(self, bid: dict) -> str:
        s = self.s
        return f"{s.c(bid['quantity'], 'bold', 'white')} {s.c(s.times, 'grey')} {s.die(bid['face'], hot=True)} {s.c('(cara ' + str(bid['face']) + ')', 'grey')}"

    # ------------------------------------------------------------------ eventos
    def game_header(self, index: int, total: int, game_id: str, specs: list[str], eta: str = "") -> None:
        if self.level == 0:
            return
        s = self.s
        seats = [f"{s.player(f'J{i + 1}')} {s.c(sp, 'grey')}" for i, sp in enumerate(specs)]
        title = s.c(f"PARTIDA {index}/{total}", "bold", "white") + s.c(f"  {s.dot}  {game_id}", "grey")
        if eta:
            title += s.c(f"  {s.dot}  {eta}", "grey")
        lines = [title, *seats]
        self.p()
        self.p(s.box(lines))

    def handle(self, ev: dict) -> None:
        if self.level == 0:
            return
        s = self.s
        t = ev.get("type")
        if t == "game_start":
            self.players = ev.get("players", [])
            self.dice_total = ev.get("dice_per_player", 0)
            self.current_bid = None
        elif t == "round_start":
            self.current_bid = None
            counts = ev["dice_counts"]
            self.p()
            self.p(s.rule(s.c(f"RONDA {ev['round']}", "bold", "white") + s.c(f"  empieza {ev['starter']}", "grey")))
            row = "   ".join(f"{s.player(n)} {s.pips(c, self.dice_total, n)}" for n, c in counts.items() if c > 0)
            out = [n for n, c in counts.items() if c <= 0]
            if out:
                row += "   " + s.c(f"{s.skull} " + ", ".join(out), "grey")
            self.p("  " + row)
            if self.level >= 2:
                for n, hand in ev.get("hands", {}).items():
                    if hand:
                        self.p(f"  {s.c('mano', 'grey')} {s.player(n)} {s.dice_row(sorted(hand))}")
        elif t == "infrastructure":
            if ev.get("kind") == "retry":
                self.p(s.c(f"  ! reintento tras HTTP {ev.get('http_status')} ({ev.get('player')})", "yellow"))
        elif t == "turn":
            self._turn(ev)
        elif t == "challenge":
            self._challenge(ev)
        elif t == "penalty":
            self.p(s.c(f"  ⚖ penalización: {ev['player']} pierde un dado ({ev['dice_left']} restantes)" if s.u
                       else f"  penalty: {ev['player']} loses a die ({ev['dice_left']} left)", "yellow"))
        elif t == "eliminated":
            self.p("  " + s.c(f"{s.skull} {ev['player']} queda eliminado", "red", "bold"))
        elif t == "game_end" and ev.get("winner"):
            self.p()
            self.p("  " + s.c(f"{s.crown} Gana ", "yellow", "bold") + s.player(ev["winner"]) +
                   s.c(f" en {ev['rounds']} rondas", "grey"))

    def _turn(self, ev: dict) -> None:
        s = self.s
        name = ev["player"]
        action = ev.get("action", {})
        latency = sum(a.get("latency_s") or 0 for a in ev.get("attempts", []))
        for attempt in ev.get("attempts", []):
            if attempt.get("error"):
                self.p("      " + s.c(f"{s.bad} respuesta inválida: {attempt['error']}", "red"))
        if action.get("action") == "bid":
            body = f"{s.c('apuesta', 'blue')} {self._bid(action)}"
            self.current_bid = {"quantity": action["quantity"], "face": action["face"]}
            extra = ""
            if self.level >= 2 and ev.get("p_bid_true") is not None:
                pb = ev["p_bid_true"]
                col = "green" if pb >= 0.5 else ("yellow" if pb >= 0.25 else "red")
                extra = "  " + s.c(f"P(cierta)={pb * 100:.0f}%", col)
        else:
            body = s.c("¡DUDO!", "red", "bold")
            extra = ""
            if self.level >= 2 and ev.get("p_challenged_bid_true") is not None:
                extra = "  " + s.c(f"P(apuesta cierta)={ev['p_challenged_bid_true'] * 100:.0f}%", "grey")
        if ev.get("forced"):
            extra += "  " + s.c("[jugada forzada por el árbitro]", "yellow")
        if latency >= 1:
            extra += "  " + s.c(f"{latency:.1f}s", "grey")
        self.p(f"  {_pad(s.player(name), 3)} {s.c(s.arrow, 'grey')} {body}{extra}")

    def _challenge(self, ev: dict) -> None:
        s = self.s
        bid = ev["challenged_bid"]
        face = bid["face"]
        true = ev["bid_was_true"]
        self.p(s.c("  " + s.h * 3 + " destapan los dados " + s.h * 3, "grey"))
        for n, hand in ev.get("revealed_hands", {}).items():
            if hand:
                hit = sum(1 for d in hand if d == face)
                self.p(f"  {_pad(s.player(n), 3)} {s.dice_row(sorted(hand), hot_face=face)}  " + s.c(f"{hit} × {face}", "yellow" if hit else "grey"))
        verdict = (s.c(f"{s.ok} apuesta CIERTA", "green", "bold") if true else s.c(f"{s.bad} apuesta FALSA", "red", "bold"))
        self.p(f"  {s.c('Total', 'grey')} {s.c(ev['real_count'], 'bold', 'white')} {s.c(s.times, 'grey')} {s.die(face, hot=True)}"
               f"  {s.c('vs apuesta', 'grey')} {s.c(bid['quantity'], 'bold')}  {verdict}")
        self.p(f"  {s.c('Pierde un dado', 'grey')} {s.player(ev['loser'])}")

    # ------------------------------------------------------------------ partida terminada
    def game_line(self, index: int, total: int, game_id: str, winner: str, wspec: str, rounds: int,
                  elapsed: float, eta: float | None) -> None:
        """Línea de progreso al final de cada partida (única línea visible con --quiet)."""
        s = self.s
        bar = s.bar(index / total, 16, "cyan")
        eta_txt = f"  ETA {fmt_time(eta)}" if eta is not None and index < total else ""
        self.p(f"{s.c(f'{index:>3}/{total}', 'bold')} {bar} {s.c(game_id, 'grey')} {s.c(s.crown, 'yellow')} "
               f"{s.player(winner)} {s.c(wspec, 'grey')} {s.c(f'{rounds} rondas', 'grey')}"
               f"{s.c(f'  {fmt_time(elapsed)}{eta_txt}', 'grey')}")


# ---------------------------------------------------------------------- resumen final
class RunStats:
    """Acumula resultados por spec: partidas, victorias, asientos y fallos."""

    def __init__(self):
        self.games: dict[str, int] = {}
        self.wins: dict[str, int] = {}
        self.seats: dict[str, int] = {}
        self.expected: dict[str, float] = {}  # victorias esperadas por puro azar (asientos / n)
        self.fields = ("bids", "dudos", "invalid_responses", "retries", "forced_moves", "penalty_dice_lost")
        self.agg: dict[str, dict[str, int]] = {}
        self.rounds: list[int] = []
        self.ok = 0

    def add_game(self, players: list[dict], winner: str | None, rounds: int, stats: dict) -> None:
        spec_of = {p["name"]: p["spec"] for p in players}
        n = len(players)
        self.ok += 1
        self.rounds.append(rounds)
        for spec in dict.fromkeys(spec_of.values()):
            self.games[spec] = self.games.get(spec, 0) + 1
        for name, spec in spec_of.items():
            self.seats[spec] = self.seats.get(spec, 0) + 1
            self.expected[spec] = self.expected.get(spec, 0.0) + 1.0 / n
            a = self.agg.setdefault(spec, dict.fromkeys(self.fields, 0))
            for f in self.fields:
                a[f] += (stats.get(name) or {}).get(f, 0)
        if winner in spec_of:
            w = spec_of[winner]
            self.wins[w] = self.wins.get(w, 0) + 1


def render_summary(s: Style, stats: RunStats, specs: list[str], out_dir, elapsed: float, errors: int, aborted: int) -> str:
    lines: list[str] = []
    total = stats.ok
    lines.append("")
    lines.append(s.rule(s.c("RESUMEN", "bold", "white"), "cyan"))
    ranking = sorted(dict.fromkeys(specs), key=lambda sp: (-(stats.wins.get(sp, 0) / max(1, stats.games.get(sp, 0))), sp))
    namew = min(34, max(12, *(len(sp) for sp in ranking)))
    head = (f"  {_pad(s.c('#', 'grey'), 2)} {_pad(s.c('jugador', 'grey'), namew)} {_pad(s.c('vict.', 'grey'), 9, True)}"
            f" {_pad(s.c('% vict.', 'grey'), 7, True)} {_pad(s.c('azar', 'grey'), 6, True)}   {s.c('rendimiento', 'grey')}")
    lines.append(head)
    for i, sp in enumerate(ranking, 1):
        g = stats.games.get(sp, 0)
        w = stats.wins.get(sp, 0)
        pct = w / g if g else 0.0
        chance = stats.expected.get(sp, 0.0) / g if g else 0.0
        col = "green" if pct > chance + 0.02 else ("red" if pct < chance - 0.02 else "yellow")
        medal = s.c(s.crown, "yellow") if i == 1 and total else " "
        name = sp if len(sp) <= namew else sp[: namew - 1] + "…"
        lines.append(f"  {_pad(s.c(str(i), 'bold'), 2)} {_pad(s.c(name, 'white', 'bold'), namew)}"
                     f" {_pad(f'{w}/{g}', 9, True)} {_pad(s.c(f'{pct * 100:.0f}%', col, 'bold'), 7, True)}"
                     f" {_pad(s.c(f'{chance * 100:.0f}%', 'grey'), 6, True)}   {s.bar(pct, 18, col)} {medal}")
    # fallos de formato / jugadas forzadas (clave para comparar LLMs)
    if any(any(stats.agg.get(sp, {}).get(k, 0) for k in ("invalid_responses", "forced_moves", "retries", "penalty_dice_lost")) for sp in ranking):
        lines.append("")
        lines.append("  " + s.c("calidad de las respuestas", "grey"))
        lines.append(f"  {_pad('', namew + 3)} {_pad(s.c('apuestas', 'grey'), 9, True)} {_pad(s.c('dudas', 'grey'), 7, True)}"
                     f" {_pad(s.c('inválidas', 'grey'), 10, True)} {_pad(s.c('forzadas', 'grey'), 9, True)}")
        for sp in ranking:
            a = stats.agg.get(sp, {})
            inv, forced = a.get("invalid_responses", 0), a.get("forced_moves", 0)
            name = sp if len(sp) <= namew else sp[: namew - 1] + "…"
            lines.append(f"  {_pad('', 3)}{_pad(s.c(name, 'white'), namew)} {_pad(str(a.get('bids', 0)), 9, True)}"
                         f" {_pad(str(a.get('dudos', 0)), 7, True)}"
                         f" {_pad(s.c(str(inv), 'red' if inv else 'grey'), 10, True)}"
                         f" {_pad(s.c(str(forced), 'red' if forced else 'grey'), 9, True)}")
    lines.append("")
    avg = sum(stats.rounds) / len(stats.rounds) if stats.rounds else 0
    parts = [s.c(f"{s.ok} {total} ok", "green")]
    if aborted:
        parts.append(s.c(f"{aborted} abortadas", "red"))
    if errors:
        parts.append(s.c(f"{errors} con error", "red"))
    lines.append("  " + f"  {s.c(s.dot, 'grey')}  ".join(parts + [s.c(f"{avg:.1f} rondas de media", "grey"), s.c(fmt_time(elapsed), "grey")]))
    lines.append("  " + s.c("registros ", "grey") + s.c(str(out_dir), "cyan"))
    lines.append("")
    return "\n".join(lines)

