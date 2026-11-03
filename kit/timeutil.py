from __future__ import annotations

from datetime import datetime, timezone


def parse_timestamp(value) -> float:
    """Accept the timestamp formats screenpipe writes and return epoch seconds (UTC)."""
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if text.endswith(" UTC"):
        text = text[:-4]
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    # Python 3.9's fromisoformat accepts "YYYY-MM-DD HH:MM:SS[.ffffff][+HH:MM]" and "T" separators.
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        # Trim nanosecond precision to microseconds if present.
        head, _, tail = text.partition(".")
        frac = "".join(ch for ch in tail if ch.isdigit())[:6]
        rest = tail[len("".join(ch for ch in tail if ch.isdigit())):]
        dt = datetime.fromisoformat(f"{head}.{frac}{rest}" if frac else head + rest)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def iso_utc(ts: float | None = None) -> str:
    dt = datetime.fromtimestamp(ts, tz=timezone.utc) if ts is not None else datetime.now(tz=timezone.utc)
    return dt.isoformat(timespec="seconds")


def local_clock(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%H:%M:%S")
