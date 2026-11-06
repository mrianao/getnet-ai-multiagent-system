"""General-purpose web tools for the Knowledge Agent (all free, official, keyless APIs).

- web_search:        Tavily (if TAVILY_API_KEY) -> DuckDuckGo Instant Answer API -> Wikipedia search
- get_weather:       Open-Meteo geocoding + daily forecast
- get_exchange_rate: Frankfurter (European Central Bank reference rates)
"""
from __future__ import annotations

import logging
import re
from datetime import date, timedelta

import httpx

logger = logging.getLogger(__name__)


class ToolError(RuntimeError):
    pass


# Wikimedia/Open-Meteo etiquette: identify the client with a contact URL.
USER_AGENT = "GetnetAssessmentBot/1.0 (+https://github.com/mrianao/getnet-ai-multiagent-system)"


WMO = {0: "clear sky", 1: "mainly clear", 2: "partly cloudy", 3: "overcast", 45: "fog", 48: "rime fog",
       51: "light drizzle", 53: "drizzle", 55: "dense drizzle", 61: "light rain", 63: "rain", 65: "heavy rain",
       71: "light snow", 73: "snow", 75: "heavy snow", 80: "rain showers", 81: "heavy rain showers",
       82: "violent rain showers", 95: "thunderstorm", 96: "thunderstorm with hail", 99: "severe thunderstorm"}

CURRENCIES = {"euro": "EUR", "eur": "EUR", "dollar": "USD", "dólar": "USD", "usd": "USD", "pound": "GBP",
              "libra": "GBP", "gbp": "GBP", "peso": "ARS", "yen": "JPY", "iene": "JPY", "real": "BRL", "brl": "BRL"}


class _RetryingClient:
    """Thin wrapper: one retry with backoff on 429/5xx (free APIs rate-limit shared IPs)."""

    def __init__(self, client: httpx.AsyncClient, retries: int = 1, backoff_s: float = 1.5):
        self._c, self._retries, self._backoff = client, retries, backoff_s

    async def _send(self, method: str, url: str, **kw) -> httpx.Response:
        import asyncio

        for attempt in range(self._retries + 1):
            r = await self._c.request(method, url, **kw)
            if r.status_code not in (429, 502, 503, 504) or attempt == self._retries:
                return r
            await asyncio.sleep(self._backoff * (attempt + 1))
        return r

    async def get(self, url: str, **kw) -> httpx.Response:
        return await self._send("GET", url, **kw)

    async def post(self, url: str, **kw) -> httpx.Response:
        return await self._send("POST", url, **kw)


class WebTools:
    def __init__(self, client: httpx.AsyncClient, tavily_api_key: str = "", retry_backoff_s: float = 1.5):
        self.http = _RetryingClient(client, backoff_s=retry_backoff_s)
        self.tavily_key = tavily_api_key

    # ---------------- web search ----------------
    async def web_search(self, query: str, max_results: int = 4) -> list[dict]:
        """Returns [{title, url, snippet}]. Tries providers in order; never invents results."""
        errors = []
        providers = ([self._tavily] if self.tavily_key else []) + [self._ddg_instant, self._wikipedia]
        for provider in providers:
            try:
                res = await provider(query, max_results)
                if res:
                    return res
            except (httpx.HTTPError, ValueError, KeyError) as e:
                errors.append(f"{provider.__name__}: {e}")
        if errors:
            logger.warning("web_search providers failed: %s", errors)
        return []

    async def _tavily(self, query: str, n: int) -> list[dict]:
        r = await self.http.post("https://api.tavily.com/search",
                                 json={"api_key": self.tavily_key, "query": query, "max_results": n})
        r.raise_for_status()
        return [{"title": x["title"], "url": x["url"], "snippet": x.get("content", "")[:500]} for x in r.json()["results"]]

    async def _ddg_instant(self, query: str, n: int) -> list[dict]:
        r = await self.http.get("https://api.duckduckgo.com/",
                                params={"q": query, "format": "json", "no_html": 1, "skip_disambig": 1})
        r.raise_for_status()
        d = r.json()
        out = []
        if d.get("AbstractText"):
            out.append({"title": d.get("Heading") or query, "url": d.get("AbstractURL", ""), "snippet": d["AbstractText"]})
        for t in d.get("RelatedTopics", []):
            if "Text" in t and t.get("FirstURL"):
                out.append({"title": t["Text"][:80], "url": t["FirstURL"], "snippet": t["Text"]})
        return out[:n]

    async def _wikipedia(self, query: str, n: int) -> list[dict]:
        r = await self.http.get("https://en.wikipedia.org/w/api.php",
                                params={"action": "query", "list": "search", "srsearch": query, "format": "json", "srlimit": n},
                                headers={"User-Agent": USER_AGENT})
        r.raise_for_status()
        return [{"title": x["title"], "url": f"https://en.wikipedia.org/wiki/{x['title'].replace(' ', '_')}",
                 "snippet": re.sub(r"<[^>]+>", "", x["snippet"])} for x in r.json()["query"]["search"]]

    # ---------------- weather ----------------
    async def get_weather(self, city: str, day_offset: int = 1) -> dict:
        """Open-Meteo first; wttr.in as fallback provider (both free, keyless)."""
        try:
            return await self._open_meteo(city, day_offset)
        except ToolError:
            raise
        except (httpx.HTTPError, KeyError, ValueError) as e:
            logger.warning("open-meteo failed (%s), trying wttr.in", e)
            return await self._wttr(city, day_offset)

    async def _wttr(self, city: str, day_offset: int) -> dict:
        r = await self.http.get(f"https://wttr.in/{city}", params={"format": "j1"})
        r.raise_for_status()
        d = r.json()
        day = d["weather"][min(day_offset, len(d["weather"]) - 1)]
        area = d["nearest_area"][0]
        hourly = day["hourly"]
        return {
            "city": city, "station_area": area["areaName"][0]["value"], "region": area["region"][0]["value"], "country": area["country"][0]["value"],
            "date": day["date"], "temp_max_c": float(day["maxtempC"]), "temp_min_c": float(day["mintempC"]),
            "precip_probability_pct": max(int(h.get("chanceofrain", 0)) for h in hourly),
            "condition": hourly[len(hourly) // 2]["weatherDesc"][0]["value"],
            "provider": "wttr.in", "source_url": f"https://wttr.in/{city.replace(' ', '%20')}",
        }

    async def _open_meteo(self, city: str, day_offset: int) -> dict:
        g = await self.http.get("https://geocoding-api.open-meteo.com/v1/search", params={"name": city, "count": 1})
        g.raise_for_status()
        results = g.json().get("results") or []
        if not results:
            raise ToolError(f"city not found: {city}")
        loc = results[0]
        f = await self.http.get("https://api.open-meteo.com/v1/forecast", params={
            "latitude": loc["latitude"], "longitude": loc["longitude"], "timezone": "auto", "forecast_days": 3,
            "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max,weather_code"})
        f.raise_for_status()
        d = f.json()["daily"]
        i = min(day_offset, len(d["time"]) - 1)
        return {
            "city": loc["name"], "region": loc.get("admin1"), "country": loc.get("country"), "date": d["time"][i],
            "temp_max_c": d["temperature_2m_max"][i], "temp_min_c": d["temperature_2m_min"][i],
            "precip_probability_pct": d["precipitation_probability_max"][i],
            "condition": WMO.get(d["weather_code"][i], f"code {d['weather_code'][i]}"),
            "provider": "Open-Meteo",
            "source_url": f"https://open-meteo.com/en/docs?latitude={loc['latitude']}&longitude={loc['longitude']}",
        }

    # ---------------- exchange rate ----------------
    async def get_exchange_rate(self, base: str = "EUR", quote: str = "BRL") -> dict:
        r = await self.http.get("https://api.frankfurter.app/latest", params={"from": base, "to": quote})
        r.raise_for_status()
        d = r.json()
        return {"base": base, "quote": quote, "rate": d["rates"][quote], "date": d["date"],
                "provider": "Frankfurter (European Central Bank reference rate)",
                "source_url": f"https://api.frankfurter.app/latest?from={base}&to={quote}"}


def detect_currency_pair(message: str) -> tuple[str, str]:
    low = message.lower()
    found = [code for word, code in CURRENCIES.items() if re.search(rf"\b{re.escape(word)}s?\b", low)]
    base = next((c for c in found if c != "BRL"), "USD")
    return base, "BRL"


def detect_city(message: str) -> str | None:
    m = re.search(r"\b(?:in|em|para|for)\s+([A-ZÀ-Ú][\wÀ-ú]+(?:\s+[A-ZÀ-Ú][\wÀ-ú]+)*)", message)
    return m.group(1) if m else None


def detect_day_offset(message: str) -> int:
    low = message.lower()
    if "tomorrow" in low or "amanhã" in low or "amanha" in low:
        return 1
    return 0


def today() -> date:  # indirection for tests
    return date.today()


def yesterday() -> date:
    return today() - timedelta(days=1)
