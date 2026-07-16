"""weather-kit — the Weather agent's OWN tools (agent-private plugin).

Ships INSIDE agents/weather/plugins/ and travels with the agent in its .agentpkg:
`get_weather` reads Open-Meteo's free forecast API (no key, no signup) for Tokyo;
`send_email` delivers the morning briefing over SMTP. Credentials come from the
environment, never from code — nothing here is specific to any machine.
"""

from __future__ import annotations

import asyncio
import json
import os
import smtplib
import urllib.parse
import urllib.request
from email.message import EmailMessage

from agentd.application.interfaces.tool import Tool, ToolResult

# Tokyo — this agent's whole world (see IDENTITY.md).
_LAT, _LON, _TZ = 35.6762, 139.6503, "Asia/Tokyo"

# WMO weather codes -> plain words (Open-Meteo returns the code only)
_WMO = {
    0: "clear sky", 1: "mostly clear", 2: "partly cloudy", 3: "overcast",
    45: "fog", 48: "rime fog",
    51: "light drizzle", 53: "drizzle", 55: "heavy drizzle",
    61: "light rain", 63: "rain", 65: "heavy rain",
    66: "freezing rain", 67: "heavy freezing rain",
    71: "light snow", 73: "snow", 75: "heavy snow", 77: "snow grains",
    80: "light showers", 81: "showers", 82: "violent showers",
    85: "snow showers", 86: "heavy snow showers",
    95: "thunderstorm", 96: "thunderstorm with hail", 99: "severe thunderstorm with hail",
}


def _sky(code) -> str:
    try:
        return _WMO.get(int(code), f"weather code {code}")
    except (TypeError, ValueError):
        return "unknown"


def _fetch_forecast() -> dict:
    query = urllib.parse.urlencode(
        {
            "latitude": _LAT,
            "longitude": _LON,
            "timezone": _TZ,
            "current": "temperature_2m,relative_humidity_2m,apparent_temperature,"
            "precipitation,weather_code,wind_speed_10m",
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,"
            "precipitation_probability_max,precipitation_sum,wind_speed_10m_max,sunrise,sunset",
            "forecast_days": 2,
        }
    )
    req = urllib.request.Request(
        f"https://api.open-meteo.com/v1/forecast?{query}",
        headers={"User-Agent": "agentd-weather/0.1"},
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))


def _day(daily: dict, key: str, i: int):
    values = daily.get(key) or []
    return values[i] if len(values) > i else None


def _shape(raw: dict) -> dict:
    """Open-Meteo's response -> the small stable shape this agent speaks everywhere
    (the model's summary, the emailed briefing, and the dashboard all read THIS)."""
    cur = raw.get("current") or {}
    daily = raw.get("daily") or {}

    def day(i: int) -> dict:
        return {
            "date": _day(daily, "time", i),
            "sky": _sky(_day(daily, "weather_code", i)),
            "high_c": _day(daily, "temperature_2m_max", i),
            "low_c": _day(daily, "temperature_2m_min", i),
            "rain_probability_pct": _day(daily, "precipitation_probability_max", i),
            "rain_total_mm": _day(daily, "precipitation_sum", i),
            "wind_max_ms": _day(daily, "wind_speed_10m_max", i),
            "sunrise": _day(daily, "sunrise", i),
            "sunset": _day(daily, "sunset", i),
        }

    return {
        "city": "Tokyo",
        "timezone": raw.get("timezone") or _TZ,
        "observed_at": cur.get("time"),
        "current": {
            "temperature_c": cur.get("temperature_2m"),
            "feels_like_c": cur.get("apparent_temperature"),
            "humidity_pct": cur.get("relative_humidity_2m"),
            "precipitation_mm": cur.get("precipitation"),
            "wind_ms": cur.get("wind_speed_10m"),
            "sky": _sky(cur.get("weather_code")),
        },
        "today": day(0),
        "tomorrow": day(1),
    }


def _summary(w: dict) -> str:
    cur, today = w["current"], w["today"]
    lines = [
        f"Tokyo weather (observed {w.get('observed_at') or 'now'}):",
        f"- now: {cur['temperature_c']}°C ({cur['sky']}), feels like {cur['feels_like_c']}°C, "
        f"humidity {cur['humidity_pct']}%, wind {cur['wind_ms']} m/s",
        f"- today: {today['sky']}, high {today['high_c']}°C / low {today['low_c']}°C, "
        f"rain chance {today['rain_probability_pct']}% ({today['rain_total_mm']} mm), "
        f"wind up to {today['wind_max_ms']} m/s",
        f"- sun: rise {str(today['sunrise'] or '')[-5:]}, set {str(today['sunset'] or '')[-5:]}",
    ]
    return "\n".join(lines)


class GetWeatherTool(Tool):
    name = "get_weather"
    plugin = "weather-kit"
    label = "Get weather"
    concurrency = "parallel"
    description = (
        "Current conditions + today's and tomorrow's forecast for Tokyo (Open-Meteo, no key). "
        "format='summary' (default) returns a readable digest; format='json' returns the raw "
        "structured shape (used by the dashboard UI)."
    )
    parameters = {
        "type": "object",
        "properties": {
            "format": {
                "type": "string",
                "enum": ["summary", "json"],
                "description": "summary (readable, default) | json (structured)",
            }
        },
    }

    def __init__(self, config):
        self.config = config

    async def execute(self, tool_call_id, params, abort, on_update=None) -> ToolResult:
        try:
            raw = await asyncio.to_thread(_fetch_forecast)
        except Exception as e:  # noqa: BLE001 — network tool: report, don't crash the turn
            return ToolResult.text(f"weather fetch failed: {e}", is_error=True)
        shaped = _shape(raw)
        if (params.get("format") or "summary") == "json":
            return ToolResult.text(json.dumps(shaped, ensure_ascii=False))
        return ToolResult.text(_summary(shaped), details=shaped)


_SMTP_REQUIRED = ("SMTP_HOST", "SMTP_USER", "SMTP_PASS")


class SendEmailTool(Tool):
    name = "send_email"
    plugin = "weather-kit"
    label = "Send email"
    concurrency = "sequential"
    description = (
        "Send a plain-text email over SMTP. Server credentials come from the environment "
        "(SMTP_HOST, SMTP_USER, SMTP_PASS; optional SMTP_PORT=587, SMTP_FROM=SMTP_USER) — "
        "set them in the daemon's .env. Use for the morning weather briefing."
    )
    parameters = {
        "type": "object",
        "required": ["to", "subject", "body"],
        "properties": {
            "to": {"type": "string", "description": "recipient email address"},
            "subject": {"type": "string", "description": "subject line"},
            "body": {"type": "string", "description": "plain-text body"},
        },
    }

    def __init__(self, config):
        self.config = config

    async def execute(self, tool_call_id, params, abort, on_update=None) -> ToolResult:
        missing = [v for v in _SMTP_REQUIRED if not os.environ.get(v)]
        if missing:
            return ToolResult.text(
                "email is not configured: set "
                + ", ".join(missing)
                + " (and optionally SMTP_PORT, SMTP_FROM) in the daemon's .env, then restart it.",
                is_error=True,
            )

        def _send() -> None:
            host = os.environ["SMTP_HOST"]
            port = int(os.environ.get("SMTP_PORT", "587"))
            user = os.environ["SMTP_USER"]
            password = os.environ["SMTP_PASS"]
            msg = EmailMessage()
            msg["From"] = os.environ.get("SMTP_FROM") or user
            msg["To"] = params["to"]
            msg["Subject"] = params["subject"]
            msg.set_content(params["body"])
            if port == 465:  # implicit-TLS servers; everything else does STARTTLS
                with smtplib.SMTP_SSL(host, port, timeout=30) as s:
                    s.login(user, password)
                    s.send_message(msg)
            else:
                with smtplib.SMTP(host, port, timeout=30) as s:
                    s.starttls()
                    s.login(user, password)
                    s.send_message(msg)

        try:
            await asyncio.to_thread(_send)
        except Exception as e:  # noqa: BLE001 — report the SMTP failure to the model/user
            return ToolResult.text(f"email send failed: {e}", is_error=True)
        return ToolResult.text(f"email sent to {params['to']}: {params['subject']}")


def register(api, ctx):
    api.register_tool(GetWeatherTool(ctx.config))
    api.register_tool(SendEmailTool(ctx.config))
