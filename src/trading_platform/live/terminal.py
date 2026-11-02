"""Rich live terminal for the NIFTY options virtual paper-trading application.

The terminal renders ONLY from the runtime snapshot dict (schema in
``docs/live_app_architecture.md``) so it is fully decoupled from the engine:

    ui.update(snapshot, events)          # periodic, throttled
    ui.show_trade_opened(position_dict)  # prominent panel, kept in scrollback
    ui.show_trade_closed(trade_dict)

Safety properties:
* Pure display.  No broker access, no orders, no files, no network.
* Every string is passed through ``core.redaction.redact_sensitive_text`` before it is
  displayed, so a token or JWT that leaks into an event/reason can never reach the screen.
* Missing or malformed snapshot fields degrade to ``n/a``; rendering never raises.
* Output derived from simulated data is always labelled (REPLAY / SIMULATED banner).

Run ``python -m trading_platform.live.terminal`` for a demo using obviously fake numbers
labelled ``ILLUSTRATIVE SYNTHETIC``.
"""

from __future__ import annotations

import io
import math
import re
import time
from collections import deque
from collections.abc import Callable, Iterable, Mapping
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from rich import box
from rich.console import Console, Group, RenderableType
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from trading_platform.core.redaction import redact_sensitive_text
from trading_platform.live import labels

IST = ZoneInfo("Asia/Kolkata")

NA = "n/a"
DASH = "-"
STALE_AGE_SECONDS = 5.0  # display threshold only; the engine enforces the real limit
STALE_SNAPSHOT_SECONDS = 5.0
RECENT_EVENT_LINES = 8
MAX_EVENT_LINES = 14  # cap when the events panel is sized to the remaining terminal height
LEVEL_KEYS = ("PDH", "PDL", "PDC", "EMA200", "IRH", "IRL")
SIMULATED_WORDS = ("SIMULATED", "SYNTHETIC", "REPLAY")
STRATEGY_PREFIXES = {"ORB", "PDS", "PDR", "IRF", "RF"}
TITLE_LIVE = "NIFTY OPTIONS — LIVE PAPER TRADING"
TITLE_REPLAY = "NIFTY OPTIONS — REPLAY (SIMULATED)"

# Restrained palette: green profit, red loss, yellow warning, neutral otherwise.
GOOD = "green"
BAD = "red"
WARN = "yellow"
DIM = "dim"


# --------------------------------------------------------------------------- helpers


def _s(value: object) -> str:
    """Redact credential-shaped text.  Every displayed string goes through here."""
    try:
        return redact_sensitive_text(value)
    except Exception:  # redaction must never take the display down
        return "[unprintable]"


def _t(value: object, style: str = "") -> Text:
    """Redacted ``Text`` (``Text`` never interprets markup, so no injection through data)."""
    return Text(_s(value), style=style)


def _get(data: object, *path: str, default: Any = None) -> Any:
    """Safe nested ``dict`` lookup; ``None`` values count as missing."""
    cur: Any = data
    for key in path:
        if not isinstance(cur, Mapping):
            return default
        cur = cur.get(key)
        if cur is None:
            return default
    return cur


def _f(value: object) -> float | None:
    """Parse a finite float or return None."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _i(value: object) -> int | None:
    number = _f(value)
    return None if number is None else int(round(number))


def parse_dt(value: object) -> datetime | None:
    """Parse an ISO string / datetime into tz-aware IST (naive input is assumed IST)."""
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str) and value.strip():
        try:
            dt = datetime.fromisoformat(value.strip())
        except ValueError:
            return None
    else:
        return None
    return dt.replace(tzinfo=IST) if dt.tzinfo is None else dt.astimezone(IST)


def fmt_time(value: object, *, seconds: bool = True) -> str:
    """IST ``HH:MM:SS`` (or ``HH:MM``) for a datetime / ISO string."""
    dt = parse_dt(value)
    if dt is None:
        return NA
    return dt.strftime("%H:%M:%S" if seconds else "%H:%M")


def fmt_inr(value: object, *, decimals: int = 2, signed: bool = False) -> str:
    """Consistent INR formatting with thousands grouping: ``₹250,000.00`` / ``-₹1,234.50``."""
    number = _f(value)
    if number is None:
        return NA
    body = f"{abs(number):,.{decimals}f}"
    if number < 0 and float(body.replace(",", "")) != 0.0:
        return f"-₹{body}"
    return f"+₹{body}" if signed and number > 0 else f"₹{body}"


def fmt_num(value: object, decimals: int = 2) -> str:
    number = _f(value)
    return NA if number is None else f"{number:,.{decimals}f}"


def fmt_pct(value: object, *, signed: bool = True) -> str:
    number = _f(value)
    if number is None:
        return NA
    return f"{number:+.2f}%" if signed else f"{number:.2f}%"


def fmt_duration(seconds: object) -> str:
    number = _f(seconds)
    if number is None or number < 0:
        return NA
    total = int(number)
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{hours:d}:{minutes:02d}:{secs:02d}"


def fmt_expiry(value: object) -> str:
    if isinstance(value, datetime):
        return value.strftime("%d%b%Y").upper()
    if isinstance(value, date):
        return value.strftime("%d%b%Y").upper()
    if isinstance(value, str) and value.strip():
        try:
            return date.fromisoformat(value.strip()[:10]).strftime("%d%b%Y").upper()
        except ValueError:
            return value.strip()
    return NA


def fmt_strike(value: object) -> str:
    number = _f(value)
    if number is None:
        return NA
    return f"{int(number)}" if number == int(number) else f"{number:g}"


def pnl_style(value: object) -> str:
    number = _f(value)
    if number is None or number == 0:
        return ""
    return GOOD if number > 0 else BAD


def method_display(method: object) -> str:
    """``METHOD_1`` -> ``Method 1``; ``RETEST_BOUNCE`` -> ``Retest Bounce`` (legacy helper)."""
    text = str(method or "").strip()
    if not text:
        return ""
    return text.replace("_", " ").title()


def looks_like_rule_id(value: object) -> bool:
    """``ORB_METHOD_1_PDH_BREAKOUT``-shaped machine id (not a sentence, not ``METHOD_1``)."""
    text = str(value or "").strip()
    return bool(
        re.fullmatch(r"[A-Z0-9]+(?:_[A-Z0-9]+)+", text)
        and text.split("_", 1)[0] in STRATEGY_PREFIXES
    )


def rule_id_of(record: Mapping[str, Any]) -> str | None:
    """The raw engine rule id recorded for a position / trade / signal, if any."""
    for key in ("rule_id", "entry_reason", "reason", "method"):
        value = record.get(key)
        if looks_like_rule_id(value):
            return str(value).strip()
    return None


def reason_text(record: Mapping[str, Any]) -> str:
    """Human sentence for why a trade opened (``reason_text`` field, else derived from the id)."""
    given = record.get("reason_text")
    if isinstance(given, str) and given.strip():
        return given.strip()
    rid = rule_id_of(record)
    if rid:
        return labels.rule_sentence(rid)
    for key in ("entry_reason", "reason"):
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return NA


def strategy_display(strategy: object, method: object = None, *, given: object = None) -> str:
    """Short human label such as ``ORB M1`` (never the title-cased rule id)."""
    if isinstance(given, str) and given.strip():
        return given.strip()
    name = str(strategy or "").strip()
    if not name:
        return NA
    return labels.strategy_label(name, str(method) if method else None)


def record_strategy_label(record: Mapping[str, Any]) -> str:
    return strategy_display(
        record.get("strategy"), rule_id_of(record) or record.get("method"),
        given=record.get("strategy_label"),
    )


def is_simulated_text(value: object) -> bool:
    upper = str(value or "").upper()
    return any(word in upper for word in SIMULATED_WORDS)


def option_side(position: Mapping[str, Any]) -> str | None:
    """``CE`` / ``PE`` from option_type, else inferred from direction."""
    option_type = str(position.get("option_type") or "").upper()
    if option_type in {"CE", "PE"}:
        return option_type
    direction = str(position.get("direction") or "").upper()
    if direction in {"BULLISH", "BUY CE", "CE"}:
        return "CE"
    if direction in {"BEARISH", "BUY PE", "PE"}:
        return "PE"
    return None


def _kv_table(pairs: Iterable[tuple[str, Text | str]], columns: int = 1) -> Table:
    """Borderless label/value grid with ``columns`` label+value pairs per row."""
    table = Table.grid(padding=(0, 1), expand=False)
    for _ in range(columns):
        table.add_column(style=DIM, no_wrap=True)
        table.add_column(overflow="fold")
        table.add_column(width=2)
    cells: list[Text] = []
    for label, value in pairs:
        cells.append(Text(f"{label}:", style=DIM))
        cells.append(value if isinstance(value, Text) else _t(value))
        cells.append(Text(""))
    per_row = columns * 3
    while len(cells) % per_row:
        cells.append(Text(""))
    for start in range(0, len(cells), per_row):
        table.add_row(*cells[start : start + per_row])
    return table


# --------------------------------------------------------------------------- dashboard parts


def _is_replay(snapshot: Mapping[str, Any]) -> bool:
    return str(_get(snapshot, "session", "mode", default="")).upper() == "REPLAY"


def _ref_now(snapshot: Mapping[str, Any], now: datetime) -> datetime:
    """Clock the ages are measured against.  A replay uses its own simulated time."""
    if _is_replay(snapshot):
        sim = parse_dt(snapshot.get("sim_time"))
        if sim is not None:
            return sim
    return now


def _data_age(snapshot: Mapping[str, Any], now: datetime) -> tuple[float | None, bool]:
    """(age seconds, is_stale).  Age is recomputed against ``now`` when a quote time exists."""
    last = parse_dt(_get(snapshot, "connection", "last_valid_quote_at"))
    age = max(0.0, (now - last).total_seconds()) if last is not None else None
    if age is None:
        age = _f(_get(snapshot, "connection", "data_age_seconds"))
    stale = bool(_get(snapshot, "connection", "stale", default=False))
    if age is not None and age > STALE_AGE_SECONDS:
        stale = True
    return age, stale


def _engine_state_text(snapshot: Mapping[str, Any]) -> Text:
    state = str(_get(snapshot, "session", "engine_state", default="UNKNOWN")).upper()
    reason = str(_get(snapshot, "session", "state_reason", default="") or "").strip()
    market = str(_get(snapshot, "session", "market_status", default="")).upper()
    if state == "RUNNING" and market == "CLOSED" and "market open" in reason.lower():
        reason = "market closed - no new entries"  # never show a stale 'market open' claim
    style = {
        "RUNNING": f"bold {GOOD}",
        "READY": "bold",
        "STARTING": "bold",
        "BLOCKED": "bold white on red",
        "HALTED_RISK": "bold white on red",
        "RECOVERY_REQUIRED": "bold black on yellow",
        "SHUTTING_DOWN": f"bold {WARN}",
        "STOPPED": DIM,
    }.get(state, "bold")
    text = Text()
    text.append(f" {state} " if " on " in style else state, style=style)
    if reason:
        reason_style = ""
        if state in {"BLOCKED", "HALTED_RISK"}:
            reason_style = BAD
        elif state == "RECOVERY_REQUIRED":
            reason_style = WARN
        text.append(" — ", style=DIM)
        text.append(_s(reason), style=reason_style)
    return text


def _market_text(snapshot: Mapping[str, Any]) -> Text:
    status = str(_get(snapshot, "session", "market_status", default="UNKNOWN")).upper()
    style = {"OPEN": GOOD, "PRE_OPEN": WARN, "CLOSED": DIM}.get(status, WARN)
    return Text(_s(status), style=style)


def _is_simulated_connection(snapshot: Mapping[str, Any]) -> bool:
    status = str(_get(snapshot, "connection", "status", default="")).upper()
    broker = str(_get(snapshot, "connection", "broker", default="")).upper()
    return _is_replay(snapshot) or status == "SIMULATED" or "SIMULATED" in broker


def _broker_text(snapshot: Mapping[str, Any]) -> str:
    if _is_simulated_connection(snapshot):
        return "none (SIMULATED)"
    return str(_get(snapshot, "connection", "broker", default="5paisa"))


def _connection_text(snapshot: Mapping[str, Any]) -> Text:
    if _is_simulated_connection(snapshot):
        return Text("SIMULATED (no broker)", style=f"bold {WARN}")
    status = str(_get(snapshot, "connection", "status", default="DISCONNECTED")).upper()
    style = {"CONNECTED": GOOD, "DEGRADED": WARN, "DISCONNECTED": BAD}.get(status, WARN)
    return Text(_s(status), style=f"bold {style}")


def _candle_trust_text(snapshot: Mapping[str, Any]) -> Text:
    source = str(_get(snapshot, "connection", "candle_source", default="UNKNOWN"))
    trusted = _get(snapshot, "connection", "candle_trusted", default=False) is True
    text = Text("Candles: ")
    if trusted:
        text.append(_s(source), style=GOOD)
        text.append(" ✓", style=GOOD)
    else:
        if source.upper() != "UNKNOWN":
            text.append(f"{_s(source)} ", style=WARN)
        text.append("NOT TRUSTED — strategy evaluation blocked", style=f"bold {BAD}")
    return text


def _levels_text(snapshot: Mapping[str, Any]) -> Text:
    """One-line reference levels row (PDH / PDL / PDC / EMA200 / IRH / IRL)."""
    levels = _get(snapshot, "levels", default={})
    text = Text("Levels: ", style=DIM)
    for index, key in enumerate(LEVEL_KEYS):
        if index:
            text.append("  |  ", style=DIM)
        text.append(f"{key} ", style=DIM)
        value = _f(levels.get(key)) if isinstance(levels, Mapping) else None
        text.append(NA if value is None else f"{value:,.2f}", style="" if value is not None else DIM)
    return text


def render_header(
    snapshot: Mapping[str, Any], now: datetime, *, compact: bool = False
) -> RenderableType:
    """Title panel: mode banner, connection, market, engine state, NIFTY and data freshness.

    ``compact`` returns three plain lines (no border) for very short terminals.
    """
    now = parse_dt(now) or datetime.now(IST)
    now = _ref_now(snapshot, now)
    replay = _is_replay(snapshot)
    label = str(_get(snapshot, "session", "label", default="") or "")
    parts: list[RenderableType] = []

    if replay:
        parts.append(
            Text(
                " REPLAY — SIMULATED DATA — NOT LIVE MARKET — NOT REAL TRADES ",
                style="bold black on yellow",
                justify="center",
            )
        )

    session_date = _get(snapshot, "session", "date")
    parsed_date = parse_dt(session_date)
    date_text = (
        parsed_date.strftime("%Y-%m-%d (%a)") if parsed_date is not None else _s(session_date or NA)
    )

    line1 = Text()
    line1.append("Date: ", style=DIM)
    line1.append(date_text)
    line1.append("   Sim time: " if replay else "   Time: ", style=DIM)
    line1.append(f"{now:%H:%M:%S} IST")
    line1.append("   Broker: ", style=DIM)
    line1.append(_s(_broker_text(snapshot)), style=WARN if replay else "")
    line1.append("   Connection: ", style=DIM)
    line1.append_text(_connection_text(snapshot))

    line2 = Text()
    line2.append("Mode: ", style=DIM)
    line2.append("PAPER ONLY", style="bold")
    if replay:
        line2.append(" (REPLAY — SIMULATED)", style=f"bold {WARN}")
    line2.append("   Market: ", style=DIM)
    line2.append_text(_market_text(snapshot))
    if label:
        line2.append("   Label: ", style=DIM)
        line2.append(_s(label), style=WARN if is_simulated_text(label) else "")

    line3 = Text()
    line3.append("Engine: ", style=DIM)
    line3.append_text(_engine_state_text(snapshot))

    age, stale = _data_age(snapshot, now)
    spot_price = _f(_get(snapshot, "spot", "price"))
    line4 = Text()
    line4.append("NIFTY: ", style=DIM)
    line4.append(fmt_num(spot_price), style="bold")
    line4.append("   Last valid quote: ", style=DIM)
    last_quote = _get(snapshot, "connection", "last_valid_quote_at")
    line4.append(f"{fmt_time(last_quote)} IST", style=WARN if stale else "")
    line4.append("   Data age: ", style=DIM)
    if age is None:
        line4.append(NA, style=WARN)
    else:
        line4.append(f"{age:.1f}s", style=WARN if stale else "")
    if stale:
        line4.append("  STALE", style=f"bold {WARN}")

    if compact:
        return Group(*parts, line1, line3, line4)

    parts.extend([line1, line2, line3, line4, _candle_trust_text(snapshot), _levels_text(snapshot)])

    notes = _get(snapshot, "connection", "notes", default=[])
    if isinstance(notes, list):
        for note in notes[-2:]:
            parts.append(Text(f"  note: {_s(note)}", style=WARN))

    written = parse_dt(_get(snapshot, "written_at"))
    if not replay and written is not None and (now - written).total_seconds() > STALE_SNAPSHOT_SECONDS:
        parts.append(
            Text(
                f"SNAPSHOT STALE: last written {int((now - written).total_seconds())}s ago "
                "— engine may have stopped",
                style=f"bold {WARN}",
            )
        )

    title = Text(TITLE_REPLAY if replay else TITLE_LIVE, style="bold")
    border = WARN if replay else "cyan"
    return Panel(Group(*parts), title=title, border_style=border, box=box.ROUNDED)


def _account_pairs(snapshot: Mapping[str, Any]) -> list[tuple[str, Text | str]]:
    account = _get(snapshot, "account", default={})
    equity = _f(_get(account, "equity"))
    open_equity = _f(_get(account, "session_open_equity"))
    realized = _f(_get(account, "session_realized_pnl"))
    unrealized = _f(_get(account, "unrealized_pnl"))
    if equity is not None and open_equity is not None:
        session_pnl: float | None = equity - open_equity
    elif realized is not None or unrealized is not None:
        session_pnl = (realized or 0.0) + (unrealized or 0.0)
    else:
        session_pnl = None
    limit = _f(_get(account, "daily_loss_limit"))
    if limit is None:
        limit = _f(_get(snapshot, "risk", "max_daily_loss_inr"))
    used = _f(_get(account, "daily_loss_used"))
    loss_style = ""
    if used is not None and limit and limit > 0:
        loss_style = BAD if used >= limit else WARN if used >= 0.75 * limit else ""
    loss_text = Text(
        f"{fmt_inr(used, decimals=0)} / {fmt_inr(limit, decimals=0)}", style=loss_style
    )
    wins, losses = _i(_get(account, "wins_today")), _i(_get(account, "losses_today"))
    trades = _i(_get(account, "trades_today"))
    pairs: list[tuple[str, Text | str]] = [
        ("Virtual capital", fmt_inr(_get(account, "starting_capital"), decimals=0)),
        ("Available cash", fmt_inr(_get(account, "cash"))),
        ("Realized today", Text(fmt_inr(realized, signed=True), style=pnl_style(realized))),
        (
            "Unrealized (gross)",
            Text(fmt_inr(unrealized, signed=True), style=pnl_style(unrealized)),
        ),
        ("Equity", Text(fmt_inr(equity), style="bold")),
        (
            "Session P&L",
            Text(fmt_inr(session_pnl, signed=True), style=f"bold {pnl_style(session_pnl)}".strip()),
        ),
        ("Daily loss used", loss_text),
        (
            "Trades today",
            f"{trades if trades is not None else NA} (W {wins if wins is not None else NA}"
            f" / L {losses if losses is not None else NA})",
        ),
    ]
    risk_state = str(_get(snapshot, "risk", "state", default="") or "").upper()
    if risk_state and risk_state != "OK":
        pairs.append(("Risk", Text(risk_state, style=f"bold {BAD}")))
    elif _get(snapshot, "risk", "cutoff_passed", default=False) is True:
        pairs.append(("Entries", Text("after cutoff — no new entries", style=WARN)))
    return pairs


def render_account(snapshot: Mapping[str, Any]) -> RenderableType:
    return Panel(
        _kv_table(_account_pairs(snapshot), columns=2),
        title=Text("Virtual Account"),
        border_style=DIM,
    )


def render_account_line(snapshot: Mapping[str, Any]) -> RenderableType:
    """One-line account summary used when the terminal is too short for the full panel."""
    account = _get(snapshot, "account", default={})
    realized = _f(_get(account, "session_realized_pnl"))
    unrealized = _f(_get(account, "unrealized_pnl"))
    text = Text("Account: ", style=DIM, no_wrap=True, overflow="ellipsis")
    text.append(f"cash {fmt_inr(_get(account, 'cash'))}  equity ")
    text.append(fmt_inr(_get(account, "equity")), style="bold")
    text.append("  realized ")
    text.append(fmt_inr(realized, signed=True), style=pnl_style(realized))
    text.append("  unrealized (gross) ")
    text.append(fmt_inr(unrealized, signed=True), style=pnl_style(unrealized))
    text.append(
        f"  daily loss used {fmt_inr(_get(account, 'daily_loss_used'))}"
        f" / {fmt_inr(_get(account, 'daily_loss_limit'))}"
    )
    return text


def render_quotes(snapshot: Mapping[str, Any], now: datetime) -> RenderableType:
    now = parse_dt(now) or datetime.now(IST)
    table = Table(box=None, expand=False, pad_edge=False, header_style=DIM)
    table.add_column("Side")
    table.add_column("Strike", justify="right")
    table.add_column("Bid", justify="right")
    table.add_column("Ask", justify="right")
    table.add_column("Sprd", justify="right")
    table.add_column("Age", justify="right")
    for side in ("CE", "PE"):
        quote = _get(snapshot, "quotes", side, default={})
        bid, ask = _f(_get(quote, "bid")), _f(_get(quote, "ask"))
        spread = ask - bid if bid is not None and ask is not None else None
        server_time = parse_dt(_get(quote, "server_time"))
        age = max(0.0, (now - server_time).total_seconds()) if server_time else None
        age_style = WARN if age is not None and age > STALE_AGE_SECONDS else ""
        table.add_row(
            Text(side, style="bold"),
            _t(fmt_strike(_get(quote, "strike"))),
            _t(fmt_num(bid)),
            _t(fmt_num(ask)),
            _t(fmt_num(spread)),
            Text(NA if age is None else f"{age:.1f}s", style=age_style),
        )
    return Panel(table, title=Text("Option Quotes"), border_style=DIM)


def _tier_display(tier: object, *, simulated: bool = False) -> tuple[str, str]:
    """(short tier text, style).  A replay is never described as an opt-in."""
    if simulated:
        return ("REPLAY-SIM", WARN)
    value = str(tier or "").upper()
    return {
        "SHADOW_ONLY": ("SHADOW", ""),
        "RESEARCH_PAPER": ("RESEARCH-PAPER", WARN),
        "AUTHOR_APPROVED": ("AUTHOR-APPROVED", GOOD),
        "DISABLED": ("DISABLED", DIM),
    }.get(value, (value or NA, WARN))


def _strategy_last_activity(row: Mapping[str, Any]) -> Text:
    signal = _get(row, "last_signal")
    rejection = _get(row, "last_rejection")
    text = Text()
    if isinstance(signal, Mapping):
        direction = labels.direction_label(str(signal.get("direction") or ""))
        when = fmt_time(signal.get("candle_open_time") or signal.get("decision_time"), seconds=False)
        text.append(f"SIGNAL {direction} @{when}".strip(), style="bold")
        sentence = reason_text(signal)
        if sentence != NA:
            text.append(f" {_s(sentence)}")
    if rejection:
        if text:
            text.append(" | ")
        text.append(f"rejected: {_s(rejection)}", style=WARN)
    if not text:
        failed = _get(row, "conditions_failed", default=[])
        if isinstance(failed, list) and failed:
            done = str(row.get("state") or "").upper() == "DONE_FOR_DAY"
            text.append(f"{'not met' if done else 'waiting'}: {_s(failed[0])}", style=DIM)
        else:
            text.append(DASH, style=DIM)
    return text


def _strategy_rows(snapshot: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    rows = _get(snapshot, "strategies", default=[])
    return [r for r in rows if isinstance(r, Mapping)] if isinstance(rows, list) else []


def render_strategies(snapshot: Mapping[str, Any]) -> RenderableType:
    table = Table(box=box.SIMPLE_HEAD, expand=True, pad_edge=False, show_edge=False)
    table.add_column("Strategy", no_wrap=True)
    table.add_column("Tier", no_wrap=True)
    table.add_column("State", no_wrap=True)
    table.add_column("Last eval", no_wrap=True)
    table.add_column("Last signal / rejection", overflow="fold", ratio=1)
    rows = _get(snapshot, "strategies", default=[])
    replay = _is_replay(snapshot)
    if not isinstance(rows, list) or not rows:
        table.add_row(Text(NA, style=DIM), "", "", "", "")
    else:
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            simulated = replay or str(row.get("auth_label") or "").upper().startswith("REPLAY")
            tier_text, tier_style = _tier_display(row.get("tier"), simulated=simulated)
            if str(row.get("tier") or "").upper() == "DISABLED":
                tier_text, tier_style = "DISABLED", DIM
            state = str(row.get("state") or NA)
            state_style = {
                "BLOCKED": BAD,
                "SIGNAL_SHADOW": WARN,
                "IN_TRADE": f"bold {GOOD}",
                "DISABLED": DIM,
                "WARMING_UP": WARN,
            }.get(state.upper(), "")
            name = strategy_display(
                row.get("name"), row.get("method"), given=row.get("label")
            ) if (row.get("name") or row.get("label")) else NA
            table.add_row(
                _t(name, "bold"),
                Text(tier_text, style=tier_style),
                Text(_s(state), style=state_style),
                _t(fmt_time(row.get("last_evaluated_candle"), seconds=False)),
                _strategy_last_activity(row),
            )
    return Panel(table, title=Text("Strategies"), border_style=DIM)


def render_strategies_line(snapshot: Mapping[str, Any]) -> RenderableType:
    """One-line strategy summary used when the terminal is too short for the table."""
    replay = _is_replay(snapshot)
    text = Text("Strategies: ", style=DIM, no_wrap=True, overflow="ellipsis")
    rows = _strategy_rows(snapshot)
    if not rows:
        text.append(NA, style=DIM)
    for index, row in enumerate(rows):
        if index:
            text.append("  |  ", style=DIM)
        name = strategy_display(row.get("name"), row.get("method"), given=row.get("label"))
        tier_text, _ = _tier_display(row.get("tier"), simulated=replay)
        text.append(_s(name), style="bold")
        text.append(f" {tier_text} {_s(row.get('state') or NA)}")
    return text


def _position_strategy_eval(snapshot: Mapping[str, Any], strategy: object) -> str:
    for row in _strategy_rows(snapshot):
        if row.get("name") == strategy:
            return (
                f"{row.get('state') or NA}, last candle "
                f"{fmt_time(row.get('last_evaluated_candle'), seconds=False)}"
            )
    return NA


def _bid_view(position: Mapping[str, Any]) -> tuple[Text, bool]:
    """(Option BID text, bid_is_stale).  Never presents an old bid as current."""
    bid = _f(position.get("current_bid"))
    if bid is None:
        return Text("n/a - no valid bid", style=f"bold {WARN}"), True
    age = _f(position.get("current_bid_age_seconds"))
    flag = position.get("bid_is_stale")
    stale = bool(flag) if isinstance(flag, bool) else (age is not None and age > STALE_AGE_SECONDS)
    text = Text(fmt_num(bid))
    if stale:
        age_part = "age n/a" if age is None else f"age {age:.0f}s"
        text.append(f" ({age_part} STALE)", style=f"bold {WARN}")
    return text, stale


def render_position(snapshot: Mapping[str, Any], now: datetime) -> RenderableType:
    """Active-position panel, or ``Active Position: NONE``."""
    now = parse_dt(now) or datetime.now(IST)
    now = _ref_now(snapshot, now)
    position = _get(snapshot, "position")
    if not isinstance(position, Mapping) or not position:
        body = Text("Active Position: NONE", style="bold")
        pending = _get(snapshot, "pending_entry")
        if isinstance(pending, Mapping) and pending:
            body.append(
                f"   (entry pending: {_s(pending.get('strategy') or NA)} "
                f"{_s(pending.get('symbol') or '')} - {_s(pending.get('detail') or 'waiting for a fill')})",
                style=WARN,
            )
        return Panel(body, title=Text("Position"), border_style=DIM)

    status = str(position.get("status") or "OPEN").upper()
    side = option_side(position)
    spot = _f(_get(snapshot, "spot", "price"))
    stop, target = _f(position.get("stop_spot")), _f(position.get("target_spot"))
    dist_stop = _f(position.get("dist_to_stop"))
    dist_target = _f(position.get("dist_to_target"))
    if dist_stop is None and spot is not None and stop is not None:
        dist_stop = abs(spot - stop)
    if dist_target is None and spot is not None and target is not None:
        dist_target = abs(target - spot)
    pnl = _f(position.get("net_pnl_estimate"))
    if pnl is None:
        pnl = _f(position.get("gross_pnl"))
    age, stale = _data_age(snapshot, now)
    bid_text, bid_stale = _bid_view(position)

    parts: list[RenderableType] = []
    if status == "EXIT_PENDING":
        reason = str(position.get("exit_pending_reason") or "").strip()
        banner = "EXIT PENDING — waiting for a valid bid"
        if reason:
            why = str(position.get("exit_pending_reason_text") or labels.exit_sentence(reason))
            banner += f" (trigger: {_s(reason)} - {_s(why)})"
        parts.append(Text(f" {banner} ", style="bold black on yellow"))

    contract = (
        f"NIFTY {fmt_expiry(position.get('expiry'))} {fmt_strike(position.get('strike'))} "
        f"{side or NA}"
    )
    pnl_text = Text(
        f"{fmt_inr(pnl, signed=True)} ({fmt_pct(position.get('pnl_pct'))})",
        style=f"bold {pnl_style(pnl)}".strip(),
    )
    if bid_stale and pnl is not None:
        pnl_text.append(" (stale bid)", style=f"bold {WARN}")
    direction = str(position.get("direction_label") or "").strip() or (
        f"BUY {side}" if side else NA
    )
    pairs: list[tuple[str, Text | str]] = [
        ("Strategy", record_strategy_label(position)),
        ("Status", Text(status, style=WARN if status == "EXIT_PENDING" else GOOD)),
        ("Contract", contract),
        ("Direction", direction),
        ("Qty", f"{fmt_num(position.get('lots'), 0)} lot(s) / {fmt_num(position.get('quantity'), 0)}"),
        ("Entry premium", fmt_num(position.get("entry_premium"))),
        ("Entry NIFTY", fmt_num(position.get("entry_spot"))),
        ("Current NIFTY", fmt_num(spot)),
        ("Option BID", bid_text),
        ("Stop NIFTY", Text(fmt_num(stop), style=BAD)),
        ("Target NIFTY", Text(fmt_num(target), style=GOOD)),
        ("To stop", f"{fmt_num(dist_stop)} pts"),
        ("To target", f"{fmt_num(dist_target)} pts"),
        ("P&L (net est.)", pnl_text),
        ("Holding", fmt_duration(position.get("holding_seconds"))),
        ("Strategy eval", _position_strategy_eval(snapshot, position.get("strategy"))),
        (
            "Data freshness",
            Text(
                NA if age is None else f"{age:.1f}s old" + (" STALE" if stale else ""),
                style=WARN if stale or age is None else "",
            ),
        ),
    ]
    label = str(position.get("label") or "")
    if label:
        pairs.append(("Label", Text(label, style=WARN)))
    parts.append(_kv_table(pairs, columns=2))
    why = Text("Why: ", style=DIM)
    why.append(_s(reason_text(position)))
    rid = rule_id_of(position)
    if rid:
        why.append(f"  (rule {_s(rid)})", style=DIM)
    parts.append(why)
    warnings = position.get("entry_warnings")
    if isinstance(warnings, list) and warnings:
        parts.append(Text("Entry warnings: " + "; ".join(_s(w) for w in warnings[:3]), style=WARN))
    return Panel(
        Group(*parts),
        title=Text("Active Position", style="bold"),
        border_style=WARN if status == "EXIT_PENDING" else "cyan",
    )


def _event_style(kind: str) -> str:
    upper = kind.upper()
    if any(word in upper for word in ("ERROR", "RISK", "BLOCK", "HALT", "FAIL")):
        return BAD
    if any(word in upper for word in ("WARN", "STALE", "REJECT", "RECOVER", "PENDING")):
        return WARN
    return ""


def event_body(kind: object, text: object) -> str:
    """Event text without a repeat of its own kind (``TRADE_OPENED`` + ``VIRTUAL TRADE OPENED ..``)."""
    body = str(text or "").strip()
    words = str(kind or "").strip().replace("_", " ").upper()
    if not words or not body:
        return body
    for prefix in (f"VIRTUAL {words}", words):
        if body.upper().startswith(prefix):
            rest = body[len(prefix) :].lstrip(" :-\u2014\u2013")
            return rest or body
    return body


def render_events(snapshot: Mapping[str, Any], limit: int = RECENT_EVENT_LINES) -> RenderableType:
    events = _get(snapshot, "recent_events", default=[])
    lines: list[Text] = []
    if isinstance(events, list) and limit > 0:
        for event in events[-limit:]:
            if not isinstance(event, Mapping):
                continue
            kind = str(event.get("kind") or "")
            line = Text(f"{fmt_time(event.get('t'))}  ", style=DIM, no_wrap=True, overflow="ellipsis")
            line.append(f"{_s(kind):<10} ", style=_event_style(kind) or "bold")
            line.append(_s(event_body(kind, event.get("text"))), style=_event_style(kind))
            line.truncate(300, overflow="ellipsis")
            lines.append(line)
    if not lines:
        lines.append(Text("no events yet", style=DIM))
    return Panel(Group(*lines), title=Text("Recent Events"), border_style=DIM)


def _rendered_height(renderable: RenderableType, width: int) -> int:
    probe = Console(width=width, file=io.StringIO(), force_terminal=False, color_system=None)
    return len(probe.render_lines(renderable, probe.options, pad=False))


def _top_row(snapshot: Mapping[str, Any], now: datetime) -> RenderableType:
    top = Table.grid(expand=True, padding=(0, 1))
    top.add_column(ratio=5)
    top.add_column(ratio=3)
    top.add_row(render_account(snapshot), render_quotes(snapshot, now))
    return top


def render_dashboard(
    snapshot: Mapping[str, Any] | None,
    now: datetime,
    *,
    height: int | None = None,
    width: int | None = None,
) -> RenderableType:
    """Full live dashboard from a runtime-snapshot dict.  Never raises on bad input.

    Order: header, ACTIVE POSITION, account + quotes, strategies, events.  When ``height`` is
    given the frame is guaranteed to put the header and the position first and then adds, in
    priority order, a strategy table, the account/quotes row and as many event lines as still
    fit (one-line summaries replace a panel that does not fit).  Without ``height`` everything
    is shown (8 event lines).
    """
    snap: Mapping[str, Any] = snapshot if isinstance(snapshot, Mapping) else {}
    now = parse_dt(now) or datetime.now(IST)
    now = _ref_now(snap, now)
    header = render_header(snap, now)
    position = render_position(snap, now)
    top = _top_row(snap, now)
    strategies = render_strategies(snap)
    if height is None:
        return Group(header, position, top, strategies, render_events(snap))

    cols = max(40, int(width or 120))
    budget = max(1, int(height) - 1)  # keep the last row free for the cursor
    pos_h = _rendered_height(position, cols)
    head_h = _rendered_height(header, cols)
    if head_h + pos_h > budget:
        header = render_header(snap, now, compact=True)
        head_h = _rendered_height(header, cols)
    remaining = budget - head_h - pos_h
    account: RenderableType | None = None
    strat: RenderableType | None = None
    events: RenderableType | None = None
    if remaining >= 2:
        account, strat = render_account_line(snap), render_strategies_line(snap)
        remaining -= 2
    elif remaining >= 1:
        strat = render_strategies_line(snap)
        remaining -= 1
    strat_extra = _rendered_height(strategies, cols) - 1
    if strat is not None and remaining >= strat_extra:
        strat, remaining = strategies, remaining - strat_extra
    top_extra = _rendered_height(top, cols) - 1
    if account is not None and remaining >= top_extra:
        account, remaining = top, remaining - top_extra
    if remaining >= 4:  # border (2) + at least two event lines
        events = render_events(snap, min(MAX_EVENT_LINES, remaining - 2))
    return Group(*(p for p in (header, position, account, strat, events) if p is not None))


# --------------------------------------------------------------------------- trade panels


def _quantity_text(record: Mapping[str, Any]) -> str:
    lots, quantity = _i(record.get("lots")), _i(record.get("quantity"))
    lot_size = _i(record.get("lot_size"))
    if lot_size is None and lots and quantity:
        lot_size = quantity // lots
    if lots is not None and lot_size is not None and quantity is not None:
        return f"{lots} lot(s) x {lot_size} = {quantity}"
    if quantity is not None:
        return f"{quantity}"
    return NA


def render_trade_opened(position: Mapping[str, Any]) -> RenderableType:
    """Prominent 'VIRTUAL TRADE OPENED' panel with the specified fields."""
    pos: Mapping[str, Any] = position if isinstance(position, Mapping) else {}
    side = option_side(pos)
    label = str(pos.get("label") or "")
    invested = _f(pos.get("premium_invested"))
    if invested is None:
        premium, qty = _f(pos.get("entry_premium")), _f(pos.get("quantity"))
        invested = premium * qty if premium is not None and qty is not None else None
    remaining = pos.get("remaining_cash", pos.get("cash_after", pos.get("cash")))
    direction = str(pos.get("direction_label") or "").strip() or (f"BUY {side}" if side else NA)
    pairs: list[tuple[str, Text | str]] = [
        ("Strategy", record_strategy_label(pos)),
        ("Reason", _t(reason_text(pos))),
        ("Direction", Text(direction, style="bold")),
        (
            "Contract",
            f"NIFTY {fmt_expiry(pos.get('expiry'))} {fmt_strike(pos.get('strike'))} {side or NA}",
        ),
        ("Entry NIFTY", fmt_num(pos.get("entry_spot"))),
        ("Entry Option ASK", fmt_num(pos.get("entry_ask"))),
        ("Virtual Entry Premium", Text(fmt_num(pos.get("entry_premium")), style="bold")),
        ("Quantity", _quantity_text(pos)),
        ("Target NIFTY", Text(fmt_num(pos.get("target_spot")), style=GOOD)),
        ("Stop NIFTY", Text(fmt_num(pos.get("stop_spot")), style=BAD)),
        ("Premium Invested", fmt_inr(invested)),
        ("Remaining Cash", fmt_inr(remaining)),
    ]
    rid = rule_id_of(pos)
    if rid:
        pairs.insert(2, ("Rule id", Text(_s(rid), style=DIM)))
    entry_time = pos.get("entry_time")
    if entry_time:
        pairs.insert(0, ("Time", f"{fmt_time(entry_time)} IST"))
    parts: list[RenderableType] = []
    simulated = is_simulated_text(label)
    mode_text = Text(f" MODE: {_s(label) if label else NA} ", style="bold black on yellow")
    if simulated:
        mode_text.append(" — SIMULATED, NOT LIVE ", style="bold black on yellow")
    parts.append(mode_text)
    parts.append(_kv_table(pairs))
    warnings = pos.get("entry_warnings")
    if isinstance(warnings, list) and warnings:
        parts.append(Text("Entry warnings: " + "; ".join(_s(w) for w in warnings[:3]), style=WARN))
    parts.append(Text("VIRTUAL fill only — no real order was placed.", style=DIM))
    return Panel(
        Group(*parts),
        title=Text("VIRTUAL TRADE OPENED", style="bold"),
        border_style="cyan",
        box=box.HEAVY,
    )


def render_trade_closed(trade: Mapping[str, Any]) -> RenderableType:
    """Prominent 'VIRTUAL TRADE CLOSED' panel with the specified fields."""
    rec: Mapping[str, Any] = trade if isinstance(trade, Mapping) else {}
    net = _f(rec.get("net_pnl"))
    gross = _f(rec.get("gross_pnl"))
    charges = _f(rec.get("total_charges"))
    if charges is None:
        entry_c, exit_c = _f(rec.get("entry_charges")), _f(rec.get("exit_charges"))
        charges = (entry_c or 0.0) + (exit_c or 0.0) if entry_c is not None or exit_c is not None else None
    reason = str(rec.get("exit_reason") or NA)
    reason_style = {"TARGET": GOOD, "STOP": BAD, "RISK": BAD, "EOD": WARN}.get(reason.upper(), "")
    reason_value = Text(_s(reason), style=f"bold {reason_style}".strip())
    if reason != NA:
        reason_value.append(f" — {_s(labels.exit_sentence(reason))}", style=DIM)
    label = str(rec.get("label") or "")
    side = option_side(rec)
    pairs: list[tuple[str, Text | str]] = [
        ("Strategy", record_strategy_label(rec)),
        ("Direction", labels.direction_label(rec.get("direction"), side) if (side or rec.get("direction")) else NA),
        ("Entry Time", f"{fmt_time(rec.get('entry_time'))} IST"),
        ("Exit Time", f"{fmt_time(rec.get('exit_time'))} IST"),
        ("Exit Reason", reason_value),
        ("Entry Premium", fmt_num(rec.get("entry_premium"))),
        ("Exit Premium", fmt_num(rec.get("exit_premium"))),
        ("Quantity", _quantity_text(rec)),
        ("Gross P&L", Text(fmt_inr(gross, signed=True), style=pnl_style(gross))),
        ("Charges", fmt_inr(charges)),
        (
            "NET P&L",
            Text(fmt_inr(net, signed=True), style=f"bold {pnl_style(net)}".strip()),
        ),
        ("Account Balance", Text(fmt_inr(rec.get("balance_after")), style="bold")),
    ]
    parts: list[RenderableType] = []
    simulated = is_simulated_text(label)
    if label:
        mode = Text(
            f" MODE: {_s(label)} ", style="bold black on yellow" if simulated else WARN
        )
        if simulated:
            mode.append(" — SIMULATED, NOT LIVE ", style="bold black on yellow")
        parts.append(mode)
    parts.append(_kv_table(pairs))
    if simulated:
        parts.append(Text("SIMULATED result - not a trading result.", style=DIM))
    border = GOOD if (net or 0) > 0 else BAD if (net or 0) < 0 else DIM
    return Panel(
        Group(*parts),
        title=Text("VIRTUAL TRADE CLOSED", style="bold"),
        border_style=border,
        box=box.HEAVY,
    )


# --------------------------------------------------------------------------- startup banner


def _check_fields(check: object) -> tuple[str, bool, str, bool]:
    if isinstance(check, Mapping):
        return (
            str(check.get("name", "")),
            bool(check.get("ok", False)),
            str(check.get("detail", "")),
            bool(check.get("blocking", True)),
        )
    return (
        str(getattr(check, "name", "")),
        bool(getattr(check, "ok", False)),
        str(getattr(check, "detail", "")),
        bool(getattr(check, "blocking", True)),
    )


def render_startup_checks(checks: Iterable[object]) -> RenderableType:
    """Ordered startup sequence with ✓/✗ marks (order preserved as given)."""
    table = Table.grid(padding=(0, 1))
    table.add_column(width=2)
    table.add_column(no_wrap=True)
    table.add_column(overflow="fold")
    blocked = False
    count = 0
    for index, check in enumerate(checks, start=1):
        name, ok, detail, blocking = _check_fields(check)
        count += 1
        if ok and detail.strip().upper().startswith("WARNING"):
            mark = Text("!", style=WARN)  # passed, but with a non-blocking warning
        elif ok:
            mark = Text("✓", style=GOOD)
        else:
            mark = Text("✗", style=BAD if blocking else WARN)
            blocked = blocked or blocking
        table.add_row(mark, Text(f"{index}. {_s(name)}", style="bold"), _t(detail, DIM))
    parts: list[RenderableType] = [table]
    if count == 0:
        parts = [Text("no startup checks reported", style=WARN)]
    elif blocked:
        parts.append(Text("STARTUP BLOCKED — fix the failed check(s) above", style=f"bold {BAD}"))
    else:
        parts.append(Text("Startup checks passed", style=GOOD))
    return Panel(
        Group(*parts),
        title=Text("Startup", style="bold"),
        border_style=BAD if blocked else GOOD if count else WARN,
    )


# --------------------------------------------------------------------------- plain status


def status_line(snapshot: Mapping[str, Any] | None, now: datetime) -> str:
    """One-line plain status for non-TTY output (redacted)."""
    snap: Mapping[str, Any] = snapshot if isinstance(snapshot, Mapping) else {}
    now = parse_dt(now) or datetime.now(IST)
    now = _ref_now(snap, now)
    age, stale = _data_age(snap, now)
    position = _get(snap, "position")
    if isinstance(position, Mapping) and position:
        _, bid_stale = _bid_view(position)
        pos_text = (
            f"{position.get('status', 'OPEN')} {record_strategy_label(position)} "
            f"{option_side(position) or NA} pnl(net est.)={fmt_inr(position.get('net_pnl_estimate'))}"
            f"{' (stale bid)' if bid_stale else ''}"
        )
    else:
        pos_text = "NONE"
    reason = _get(snap, "session", "state_reason", default="")
    line = (
        f"[{now:%H:%M:%S}] {_get(snap, 'session', 'mode', default='?')} "
        f"conn={'SIMULATED' if _is_simulated_connection(snap) else _get(snap, 'connection', 'status', default='DISCONNECTED')} "
        f"market={_get(snap, 'session', 'market_status', default='?')} "
        f"engine={_get(snap, 'session', 'engine_state', default='?')}"
        f"{' (' + str(reason) + ')' if reason else ''} "
        f"nifty={fmt_num(_get(snap, 'spot', 'price'))} "
        f"age={'n/a' if age is None else f'{age:.1f}s'}{' STALE' if stale else ''} "
        f"session_pnl={fmt_inr(_get(snap, 'account', 'session_realized_pnl'))} "
        f"position={pos_text}"
    )
    return _s(line)


def _status_key(snapshot: Mapping[str, Any]) -> tuple[Any, ...]:
    """State tuple whose change triggers a plain-mode print (excludes price/age/time)."""
    position = _get(snapshot, "position")
    pos_key = (
        (position.get("trade_id"), position.get("status"))
        if isinstance(position, Mapping) and position
        else None
    )
    return (
        _get(snapshot, "session", "mode"),
        _get(snapshot, "connection", "status"),
        _get(snapshot, "session", "market_status"),
        _get(snapshot, "session", "engine_state"),
        _get(snapshot, "session", "state_reason"),
        bool(_get(snapshot, "connection", "stale", default=False)),
        pos_key,
    )


# --------------------------------------------------------------------------- UI controller


class TerminalUI:
    """Context manager around ``rich.live.Live`` with a quiet non-TTY fallback.

    * TTY: a flicker-free live dashboard (no alternate screen), refresh capped at
      ``refresh_hz``.  Trade panels and messages are printed ABOVE the live area so they stay
      in scrollback.
    * Non-TTY (pipe / file / CI): no live rendering.  A one-line status is printed only when
      the key state changes (connection, engine state, position, ...) plus a slow heartbeat,
      so logs are never spammed.
    """

    def __init__(
        self,
        console: Console | None = None,
        refresh_hz: float = 2.0,
        *,
        plain_heartbeat_seconds: float = 60.0,
        now_fn: Callable[[], datetime] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.console = console if console is not None else Console()
        self.refresh_hz = max(0.1, float(refresh_hz))
        self.plain_heartbeat_seconds = max(1.0, float(plain_heartbeat_seconds))
        self._now_fn = now_fn or (lambda: datetime.now(IST))
        self._monotonic = monotonic
        self._live: Live | None = None
        self._snapshot: dict[str, Any] = {}
        self._last_refresh = float("-inf")
        self._last_plain_print = float("-inf")
        self._last_plain_key: tuple[Any, ...] | None = None
        self._seen: set[tuple[str, str, str]] = set()
        self._seen_order: deque[tuple[str, str, str]] = deque(maxlen=500)
        self._extra_events: deque[dict[str, Any]] = deque(maxlen=50)
        self._render_error_shown = False
        self._pending_panels: list[RenderableType] = []

    @property
    def interactive(self) -> bool:
        return bool(self.console.is_terminal) and not self.console.is_dumb_terminal

    # ---- context manager

    def __enter__(self) -> TerminalUI:
        if self.interactive and self._live is None:
            self._live = Live(
                Text(""),
                console=self.console,
                screen=False,
                auto_refresh=False,
                transient=False,
                redirect_stdout=False,
                redirect_stderr=False,
                vertical_overflow="crop",  # the frame is height-aware; never print '...'
            )
            self._live.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        self.flush()
        live, self._live = self._live, None
        if live is not None:
            try:
                live.update(self._build(), refresh=True)
            except Exception:
                pass
            live.stop()

    # ---- updates

    def _build(self) -> RenderableType:
        try:
            return render_dashboard(
                self._snapshot,
                self._now_fn(),
                height=self.console.height,
                width=self.console.width,
            )
        except Exception as exc:  # display must never take the engine down
            self._render_error_shown = True
            return Text(f"display error: {_s(type(exc).__name__)}", style=BAD)

    def _remember(self, events: Iterable[Mapping[str, Any]] | None) -> list[dict[str, Any]]:
        """Return events not seen before (bounded de-dupe)."""
        fresh: list[dict[str, Any]] = []
        for event in events or []:
            if not isinstance(event, Mapping):
                continue
            key = (str(event.get("t", "")), str(event.get("kind", "")), str(event.get("text", "")))
            if key in self._seen:
                continue
            if len(self._seen_order) == self._seen_order.maxlen:
                self._seen.discard(self._seen_order[0])
            self._seen_order.append(key)
            self._seen.add(key)
            fresh.append(dict(event))
        return fresh

    def update(
        self,
        snapshot: Mapping[str, Any] | None,
        events: Iterable[Mapping[str, Any]] | None = None,
    ) -> None:
        """Feed the latest snapshot (and any new events).  Cheap to call every poll."""
        snap: dict[str, Any] = dict(snapshot) if isinstance(snapshot, Mapping) else {}
        fresh = self._remember(events)
        self._extra_events.extend(fresh)
        base = snap.get("recent_events")
        base_list = [e for e in base if isinstance(e, Mapping)] if isinstance(base, list) else []
        known = {(str(e.get("t", "")), str(e.get("kind", "")), str(e.get("text", ""))) for e in base_list}
        extras = [
            e
            for e in self._extra_events
            if (str(e.get("t", "")), str(e.get("kind", "")), str(e.get("text", ""))) not in known
        ]
        snap["recent_events"] = (base_list + extras)[-50:]
        self._snapshot = snap
        now_m = self._monotonic()
        if self._live is not None:
            if now_m - self._last_refresh >= 1.0 / self.refresh_hz:
                self._last_refresh = now_m
                self._live.update(self._build(), refresh=True)
            self.flush()
            return
        self._plain_update(snap, fresh, now_m)
        self.flush()

    def _plain_update(
        self, snap: Mapping[str, Any], fresh: list[dict[str, Any]], now_m: float
    ) -> None:
        if self.interactive:  # entered without the context manager: do not print continuously
            return
        for event in fresh:
            kind = event.get("kind", "")
            self.console.print(
                Text(
                    f"{fmt_time(event.get('t'))} {_s(kind)} "
                    f"{_s(event_body(kind, event.get('text', '')))}"
                ),
                soft_wrap=True,
            )
        self.flush()  # OPENED / CLOSED panels follow the event lines that belong to them
        key = _status_key(snap)
        changed = key != self._last_plain_key
        heartbeat = now_m - self._last_plain_print >= self.plain_heartbeat_seconds
        if changed or heartbeat:
            self._last_plain_key = key
            self._last_plain_print = now_m
            self.console.print(Text(status_line(snap, self._now_fn())), soft_wrap=True)

    # ---- scrollback output

    def show_trade_opened(self, position: Mapping[str, Any]) -> None:
        """Queue the OPENED panel; it is printed after the related event lines (next update)."""
        self._pending_panels.append(render_trade_opened(position))

    def show_trade_closed(self, trade: Mapping[str, Any]) -> None:
        """Queue the CLOSED panel; it is printed after the related event lines (next update)."""
        self._pending_panels.append(render_trade_closed(trade))

    def flush(self) -> None:
        """Print queued trade panels (no-op when none are queued)."""
        pending, self._pending_panels = self._pending_panels, []
        for panel in pending:
            self.console.print(panel)

    def show_message(self, text: object, style: str = "") -> None:
        self.flush()
        self.console.print(Text(_s(text), style=style), soft_wrap=True)

    def startup_banner(self, checks: Iterable[object]) -> None:
        self.flush()
        self.console.print(render_startup_checks(list(checks)))


# --------------------------------------------------------------------------- demo (SYNTHETIC)

SYNTHETIC_LABEL = "ILLUSTRATIVE SYNTHETIC"
DEMO_KINDS = (
    "flat",
    "open_profit",
    "open_loss",
    "exit_pending",
    "blocked",
    "recovery",
    "stale",
    "stale_bid",
    "replay",
)


def illustrative_snapshot(kind: str, now: datetime) -> dict[str, Any]:
    """Obviously fake snapshot (labelled ILLUSTRATIVE SYNTHETIC) for demo and tests only."""
    now = parse_dt(now) or datetime.now(IST)
    iso = now.isoformat(timespec="seconds")
    quote_age = 0.8
    last_quote = (now.timestamp() - quote_age)
    last_quote_iso = datetime.fromtimestamp(last_quote, IST).isoformat(timespec="seconds")
    spot = 20000.0
    snap: dict[str, Any] = {
        "schema_version": 1,
        "written_at": iso,
        "session": {
            "session_id": "DEMO",
            "date": now.date().isoformat(),
            "mode": "LIVE",
            "label": SYNTHETIC_LABEL,
            "engine_state": "RUNNING",
            "state_reason": "",
            "market_status": "OPEN",
        },
        "connection": {
            "broker": "5paisa",
            "status": "CONNECTED",
            "last_valid_quote_at": last_quote_iso,
            "data_age_seconds": quote_age,
            "stale": False,
            "candle_source": "PROVIDER_COMPLETED",
            "candle_trusted": True,
            "notes": [],
        },
        "spot": {"price": spot},
        "levels": {
            "PDH": 20080.0, "PDL": 19920.0, "PDC": 19990.0, "EMA200": 19950.5,
            "IRH": 20012.0, "IRL": 19985.0,
        },
        "quotes": {
            "CE": {"strike": 20000, "bid": 100.0, "ask": 101.0, "server_time": last_quote_iso},
            "PE": {"strike": 20000, "bid": 98.0, "ask": 99.5, "server_time": last_quote_iso},
        },
        "account": {
            "starting_capital": 100000.0,
            "cash": 100000.0,
            "equity": 100000.0,
            "session_open_equity": 100000.0,
            "session_realized_pnl": 0.0,
            "unrealized_pnl": 0.0,
            "trades_today": 0,
            "wins_today": 0,
            "losses_today": 0,
            "daily_loss_limit": 10000.0,
            "daily_loss_used": 0.0,
        },
        "risk": {"state": "OK", "cutoff_passed": False},
        "strategies": [
            {
                "name": "PDS", "label": "PDS M1", "tier": "SHADOW_ONLY", "state": "MONITORING",
                "last_evaluated_candle": iso, "last_signal": None, "last_rejection": None,
            },
            {
                "name": "ORB", "label": "ORB M1", "tier": "RESEARCH_PAPER", "state": "MONITORING",
                "last_evaluated_candle": iso, "last_signal": None, "last_rejection": None,
            },
            {
                "name": "IRF", "label": "IRF M2", "tier": "SHADOW_ONLY", "state": "WARMING_UP",
                "last_evaluated_candle": None, "last_signal": None,
                "last_rejection": "FAKE: reference levels not ready",
            },
            {
                "name": "PDR", "label": "PDR Retest Bounce", "tier": "SHADOW_ONLY",
                "state": "MONITORING", "last_evaluated_candle": iso,
                "last_signal": {"direction": "BULLISH", "candle_open_time": iso,
                                "reason": "PDR_RETEST_BOUNCE"},
                "last_rejection": "SHADOW_ONLY",
            },
            {"name": "RF", "label": "RF", "tier": "DISABLED", "state": "DISABLED"},
        ],
        "position": None,
        "recent_events": [
            {"t": iso, "kind": "STATE", "text": f"{SYNTHETIC_LABEL}: engine RUNNING"},
            {"t": iso, "kind": "SIGNAL", "text": "PDR bullish signal (fake, shadow only)"},
        ],
    }

    def position(status: str, bid: float, pnl: float, pct: float) -> dict[str, Any]:
        return {
            "trade_id": "DEMO-1", "strategy": "ORB", "method": "ORB_METHOD_1_PDH_BREAKOUT",
            "direction": "BULLISH", "option_type": "CE", "symbol": "NIFTY FAKE",
            "strike": 20000, "expiry": now.date().isoformat(), "lots": 2, "quantity": 100,
            "entry_time": iso, "entry_spot": 19990.0, "entry_bid": 99.0, "entry_ask": 100.0,
            "entry_premium": 100.5, "current_bid": bid, "stop_spot": 19960.0,
            "target_spot": 20060.0, "gross_pnl": pnl, "net_pnl_estimate": pnl, "pnl_pct": pct,
            "holding_seconds": 754, "status": status, "label": "RESEARCH_PAPER_PROVISIONAL",
            "entry_reason": "ORB_METHOD_1_PDH_BREAKOUT",
            "current_bid_age_seconds": 0.8,
        }

    acct = snap["account"]
    if kind == "open_profit":
        snap["position"] = position("OPEN", 115.0, 1450.0, 14.43)
        acct.update(cash=89950.0, equity=101450.0, unrealized_pnl=1450.0)
        snap["spot"]["price"] = 20015.0
    elif kind == "open_loss":
        snap["position"] = position("OPEN", 90.0, -1050.0, -10.45)
        acct.update(cash=89950.0, equity=98950.0, unrealized_pnl=-1050.0, daily_loss_used=1050.0)
        snap["spot"]["price"] = 19972.0
    elif kind == "exit_pending":
        pos = position("EXIT_PENDING", 0.0, -2000.0, -19.9)
        pos["exit_pending_reason"] = "STOP"
        snap["position"] = pos
        acct.update(cash=89950.0, equity=98000.0, unrealized_pnl=-2000.0)
        snap["spot"]["price"] = 19955.0
        snap["quotes"]["CE"].update(bid=0.0)
    elif kind == "blocked":
        snap["session"].update(
            engine_state="BLOCKED", state_reason="no trustworthy completed candle from provider"
        )
        snap["connection"].update(candle_trusted=False, candle_source="SAMPLED_TICKS")
    elif kind == "recovery":
        snap["session"].update(
            engine_state="RECOVERY_REQUIRED", state_reason="open position found in checkpoint"
        )
    elif kind == "stale":
        stale_iso = datetime.fromtimestamp(now.timestamp() - 42, IST).isoformat(timespec="seconds")
        snap["connection"].update(
            status="DEGRADED", stale=True, last_valid_quote_at=stale_iso, data_age_seconds=42.0
        )
    elif kind == "stale_bid":
        pos = position("OPEN", 87.9, -1300.0, -12.9)
        pos["current_bid_age_seconds"] = 42.0
        snap["position"] = pos
        acct.update(cash=89950.0, equity=98700.0, unrealized_pnl=-1300.0)
    elif kind == "replay":
        snap["session"].update(mode="REPLAY", label="REPLAY_SIMULATED")
        snap["sim_time"] = iso
        snap["connection"].update(broker="SIMULATED", status="SIMULATED")
    return snap


def illustrative_trade(now: datetime) -> dict[str, Any]:
    now = parse_dt(now) or datetime.now(IST)
    iso = now.isoformat(timespec="seconds")
    return {
        "strategy": "ORB", "method": "ORB_METHOD_1_PDH_BREAKOUT", "option_type": "CE",
        "entry_time": iso, "exit_time": iso,
        "exit_reason": "TARGET", "entry_premium": 100.5, "exit_premium": 129.5, "lots": 2,
        "quantity": 100, "lot_size": 50, "gross_pnl": 2900.0, "total_charges": 85.25,
        "net_pnl": 2814.75, "balance_after": 102814.75, "label": "RESEARCH_PAPER_PROVISIONAL",
    }


def demo_main() -> None:
    """Render the illustrative states (fake numbers) to the terminal."""
    console = Console()
    now = datetime.now(IST)
    ui = TerminalUI(console=console)
    console.print(Text(f"{SYNTHETIC_LABEL} — every number below is fake", style="bold black on yellow"))
    ui.startup_banner(
        [
            {"name": "config", "ok": True, "detail": "demo"},
            {"name": "login", "ok": True, "detail": "demo (no network)"},
            {"name": "stream", "ok": False, "detail": "demo failure", "blocking": True},
        ]
    )
    for kind in DEMO_KINDS:
        console.print(Text(f"--- {SYNTHETIC_LABEL}: {kind} ---", style=WARN))
        console.print(render_dashboard(illustrative_snapshot(kind, now), now))
    opened = (illustrative_snapshot("open_profit", now)["position"] or {}) | {
        "remaining_cash": 89950.0, "premium_invested": 10050.0
    }
    console.print(Text(f"--- {SYNTHETIC_LABEL}: trade panels ---", style=WARN))
    ui.show_trade_opened(opened)
    ui.show_trade_closed(illustrative_trade(now))
    ui.flush()


if __name__ == "__main__":
    demo_main()
