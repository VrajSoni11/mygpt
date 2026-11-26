"""Online tools for LOKI: live weather, web search, news search.

These are the ONLY parts of LOKI that use the internet, and only when a tool is called.
Turn them all off with  ONLINE_TOOLS = False  in main.py.
"""
import httpx

# ---------- Settings ----------
DEFAULT_LOCATION = "Junagadh"   # used when you ask "what's the weather?" without a place
SEARCH_REGION = "in-en"         # "wt-wt" = worldwide, "us-en" = USA, "in-en" = India
MAX_RESULTS = 5
MAX_CHARS = 1800                # keeps results short so the small model stays fast

GEO_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

WMO = {
    0: "clear sky", 1: "mainly clear", 2: "partly cloudy", 3: "overcast",
    45: "fog", 48: "freezing fog",
    51: "light drizzle", 53: "drizzle", 55: "heavy drizzle",
    56: "freezing drizzle", 57: "heavy freezing drizzle",
    61: "light rain", 63: "rain", 65: "heavy rain",
    66: "freezing rain", 67: "heavy freezing rain",
    71: "light snow", 73: "snow", 75: "heavy snow", 77: "snow grains",
    80: "light showers", 81: "showers", 82: "violent showers",
    85: "snow showers", 86: "heavy snow showers",
    95: "thunderstorm", 96: "thunderstorm with hail", 99: "thunderstorm with heavy hail",
}


def _get_json(url: str, params: dict) -> dict:
    r = httpx.get(url, params=params, timeout=15)
    r.raise_for_status()
    return r.json()


def _desc(code) -> str:
    return WMO.get(code, "unknown conditions")


def _n(value, unit: str = "") -> str:
    return "n/a" if value is None else f"{value}{unit}"


# ---------- Weather ----------
def get_weather(location: str = None, days: int = 2) -> str:
    place = (location or DEFAULT_LOCATION).strip()
    try:
        days = max(1, min(int(days), 5))
    except (TypeError, ValueError):
        days = 2

    try:
        geo = _get_json(GEO_URL, {"name": place, "count": 1})
        results = geo.get("results") or []
        if not results:
            return f"Couldn't find a place called '{place}'."
        g = results[0]
        label = ", ".join(x for x in (g.get("name"), g.get("admin1"), g.get("country")) if x)

        data = _get_json(
            FORECAST_URL,
            {
                "latitude": g["latitude"],
                "longitude": g["longitude"],
                "current": "temperature_2m,apparent_temperature,relative_humidity_2m,weather_code,wind_speed_10m",
                "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
                "timezone": "auto",
                "forecast_days": days,
            },
        )
    except httpx.HTTPError as e:
        return f"Couldn't reach the weather service ({e}). Are you online?"

    cur, daily = data["current"], data["daily"]
    lines = [
        f"Weather for {label} (live, Open-Meteo):",
        f"Now: {_n(cur.get('temperature_2m'), 'C')} (feels like {_n(cur.get('apparent_temperature'), 'C')}), "
        f"{_desc(cur.get('weather_code'))}, humidity {_n(cur.get('relative_humidity_2m'), '%')}, "
        f"wind {_n(cur.get('wind_speed_10m'), ' km/h')}.",
    ]
    names = ["Today", "Tomorrow"]
    for i, day in enumerate(daily["time"]):
        name = names[i] if i < len(names) else day
        lines.append(
            f"{name} ({day}): {_n(daily['temperature_2m_min'][i])} to {_n(daily['temperature_2m_max'][i], 'C')}, "
            f"{_desc(daily['weather_code'][i])}, rain chance {_n(daily['precipitation_probability_max'][i], '%')}."
        )
    return "\n".join(lines)


# ---------- Web and news search ----------
def _ddgs():
    try:
        from ddgs import DDGS
    except ImportError:
        try:
            from duckduckgo_search import DDGS  # older package name
        except ImportError:
            return None
    return DDGS


def _clip(text: str, n: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[: n - 1] + "..."


def web_search(query: str) -> str:
    DDGS = _ddgs()
    if DDGS is None:
        return "Web search isn't installed. Run: pip install ddgs"
    try:
        results = list(DDGS(timeout=10).text(query, region=SEARCH_REGION, max_results=MAX_RESULTS))
    except Exception as e:
        return f"Web search failed ({e}). Are you online?"
    if not results:
        return "No results found."
    lines = [
        f"{i}. {_clip(r.get('title'), 90)}\n   {_clip(r.get('body'), 240)}\n   {r.get('href', '')}"
        for i, r in enumerate(results, 1)
    ]
    return _clip_block("\n".join(lines))


def search_news(query: str) -> str:
    DDGS = _ddgs()
    if DDGS is None:
        return "News search isn't installed. Run: pip install ddgs"
    try:
        results = list(DDGS(timeout=10).news(query, region=SEARCH_REGION, max_results=MAX_RESULTS))
    except Exception as e:
        return f"News search failed ({e}). Are you online?"
    if not results:
        return "No news found."
    lines = []
    for i, r in enumerate(results, 1):
        when = (r.get("date") or "")[:10]
        lines.append(
            f"{i}. {_clip(r.get('title'), 100)} ({r.get('source', 'unknown')}, {when})\n   {_clip(r.get('body'), 200)}"
        )
    return _clip_block("\n".join(lines))


def _clip_block(text: str) -> str:
    return text if len(text) <= MAX_CHARS else text[: MAX_CHARS - 3] + "..."


# ---------- Tool definitions the model sees ----------
def _schema(name: str, desc: str, props: dict, required: list = None) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": desc,
            "parameters": {"type": "object", "properties": props, "required": required or []},
        },
    }


SCHEMAS = [
    _schema(
        "get_weather",
        "Get live weather and forecast. Omit location for the owner's home city.",
        {
            "location": {"type": "string", "description": "City name (optional)"},
            "days": {"type": "integer", "description": "Forecast days 1-5, default 2"},
        },
    ),
    _schema(
        "web_search",
        "Search the web for current or factual information.",
        {"query": {"type": "string", "description": "Short search query"}},
        ["query"],
    ),
    _schema(
        "search_news",
        "Search recent news about a topic.",
        {"query": {"type": "string", "description": "Topic, e.g. 'India cricket'"}},
        ["query"],
    ),
]