"""When the morning refresh is due. Pure functions, so they are easy to test."""
from datetime import datetime, timedelta, timezone

RETRY_AFTER = timedelta(hours=1)  # a failed refresh is tried again after this long ...
MAX_ATTEMPTS = 3                  # ... up to this many times a day


def zone(name):
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except Exception:
        return timezone.utc  # no time zone data on this computer: fall back to UTC rather than not running


def parse_time(text):
    hour, minute = (int(part) for part in (text or "07:30").split(":"))
    return hour, minute


def local_now(tz_name, now=None):
    return (now or datetime.now(timezone.utc)).astimezone(zone(tz_name))


def due(schedule, state, now=None):
    """True when the refresh should start now. It runs once a day after the set time, catches up if the computer was off at that
    time, and retries a failed run a few times. `state` holds the date, the number of attempts and the last result."""
    if not schedule.get("enabled", True):
        return False
    local = local_now(schedule.get("tz", "Europe/London"), now)
    hour, minute = parse_time(schedule.get("time"))
    if (local.hour, local.minute) < (hour, minute):
        return False
    today = local.date().isoformat()
    if state.get("attempt_date") != today:
        return True
    if state.get("last_status") == "failed" and state.get("attempts", 0) < MAX_ATTEMPTS:
        try:
            last = datetime.fromisoformat(state["last_attempt_at"])
        except (KeyError, ValueError):
            return True
        return (now or datetime.now(timezone.utc)) - last >= RETRY_AFTER
    return False


def next_run(schedule, now=None):
    """The next time it will start by itself, as UTC (or None when the schedule is off)."""
    if not schedule.get("enabled", True):
        return None
    tz = zone(schedule.get("tz", "Europe/London"))
    local = local_now(schedule.get("tz", "Europe/London"), now)
    hour, minute = parse_time(schedule.get("time"))
    target = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= local:
        target = (local + timedelta(days=1)).replace(hour=hour, minute=minute, second=0, microsecond=0)
    return target.astimezone(timezone.utc)
