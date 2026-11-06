"""Customer Support tools (simulated data) + web tools (mocked HTTP)."""
from datetime import date

import httpx
import pytest

from app.tools import customer as ct
from app.tools.web import WebTools, detect_city, detect_currency_pair, detect_day_offset
from tests.conftest import mock_handler

TODAY = date(2026, 11, 6)


def test_settlement_schedule_for_yesterday():
    r = ct.get_settlement_schedule("cliente1988", today=TODAY)
    assert r["found"] and r["simulated"] is True
    assert r["sales_date"] == "2026-11-05"
    assert r["schedule"] and r["schedule"][0]["deposit_date"] >= "2026-11-05"
    assert r["gross_amount_brl"] == pytest.approx(sum(i["amount_brl"] for i in r["schedule"]), abs=0.05)


def test_transaction_status_declined_filter_and_reason():
    r = ct.get_transaction_status("cliente1988", today=TODAY, status="declined")
    assert r["transactions"] and all(t["status"] == "declined" for t in r["transactions"])
    assert all(t["decline_reason"] for t in r["transactions"])


def test_terminal_status():
    r = ct.get_terminal_status("cliente1988")
    assert {t["status"] for t in r["terminals"]} == {"online", "offline"}


def test_data_is_deterministic():
    assert ct.get_transaction_status("cliente1988", today=TODAY) == ct.get_transaction_status("cliente1988", today=TODAY)


@pytest.mark.parametrize("tool", list(ct.TOOLS))
def test_unknown_user(tool):
    assert ct.TOOLS[tool]("ghost", today=TODAY)["found"] is False


@pytest.fixture
def web():
    return WebTools(httpx.AsyncClient(transport=httpx.MockTransport(mock_handler)))


async def test_weather_tool(web):
    w = await web.get_weather("Porto Alegre", 1)
    assert w["date"] == "2026-11-07" and w["condition"] == "rain" and w["precip_probability_pct"] == 80


async def test_fx_tool(web):
    fx = await web.get_exchange_rate("EUR", "BRL")
    assert fx["rate"] == 6.1234 and "Central Bank" in fx["provider"]


async def test_web_search_falls_back_to_wikipedia(web):
    res = await web.web_search("capital of Brazil")
    assert res[0]["url"] == "https://en.wikipedia.org/wiki/Brazil"
    assert "<b>" not in res[0]["snippet"]


async def test_web_search_returns_empty_on_failures():
    w = WebTools(httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(500))))
    assert await w.web_search("anything") == []


def test_parsers():
    assert detect_city("What's the weather forecast in Porto Alegre tomorrow?") == "Porto Alegre"
    assert detect_day_offset("tomorrow") == 1 and detect_day_offset("today") == 0
    assert detect_currency_pair("What's the euro exchange rate today?") == ("EUR", "BRL")


async def test_weather_falls_back_to_second_provider_on_429():
    def handler(request):
        if request.url.host == "api.open-meteo.com":
            return httpx.Response(429)
        return mock_handler(request)
    w = WebTools(httpx.AsyncClient(transport=httpx.MockTransport(handler)), retry_backoff_s=0)
    res = await w.get_weather("Porto Alegre", 1)
    assert res["provider"] == "wttr.in" and res["date"] == "2026-11-07"
