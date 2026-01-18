# app.py
# Dot Text API v2: rotate every 60s -> BTC -> ETH -> WEATHER -> ...
# Deploy on Koyeb (FastAPI + /health) and run a background loop.
#
# Files:
#   app.py
#   weather.json   (dict: icon_key -> base64 PNG string)
#
# ENV required:
#   DOT_API_KEY
#   DOT_DEVICE_ID
#
# ENV optional:
#   INTERVAL_SECS=60
#   WEATHER_CITY="Ho Chi Minh City"
#   WEATHER_LAT="10.8231"
#   WEATHER_LON="106.6297"
#   WEATHER_TZ="Asia/Ho_Chi_Minh"
#   WEATHER_DAY_ONLY="1"   # if set, always use day icons
#
# Run:
#   uvicorn app:app --host 0.0.0.0 --port 8000

import os
import json
import math
import asyncio
import logging
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Optional

import httpx
from fastapi import FastAPI
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger("dot-text-rotator")

# ===== APIs =====
BINANCE_24HR = "https://api.binance.com/api/v3/ticker/24hr"
OPEN_METEO = "https://api.open-meteo.com/v1/forecast"
DOT_TEXT_API_V2 = "https://dot.mindreset.tech/api/authV2/open/device/{device_id}/text"

# ===== Weather text VI (Open-Meteo weathercode) =====
WEATHER_TEXT_VI = {
    0: "Trời quang",
    1: "Gần như quang",
    2: "Ít mây",
    3: "Nhiều mây",
    45: "Sương mù",
    48: "Sương mù băng",
    51: "Mưa phùn nhẹ",
    53: "Mưa phùn",
    55: "Mưa phùn nặng",
    56: "Mưa phùn đóng băng nhẹ",
    57: "Mưa phùn đóng băng",
    61: "Mưa nhẹ",
    63: "Mưa",
    65: "Mưa to",
    66: "Mưa đóng băng nhẹ",
    67: "Mưa đóng băng",
    71: "Tuyết nhẹ",
    73: "Tuyết",
    75: "Tuyết dày",
    77: "Hạt tuyết",
    80: "Mưa rào nhẹ",
    81: "Mưa rào",
    82: "Mưa rào lớn",
    85: "Tuyết rào nhẹ",
    86: "Tuyết rào lớn",
    95: "Dông",
    96: "Dông kèm mưa đá",
    99: "Dông kèm mưa đá",
}

def weather_desc_vi(code: Optional[int]) -> str:
    if code is None:
        return "Thời tiết"
    return WEATHER_TEXT_VI.get(code, f"Mã {code}")

# ===== BTC/ETH icon base64 (from you) =====
BTC_ICON_B64 = "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"

ETH_ICON_B64 = "iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAYAAABzenr0AAAAAXNSR0IArs4c6QAAAIRlWElmTU0AKgAAAAgABQESAAMAAAABAAEAAAEaAAUAAAABAAAASgEbAAUAAAABAAAAUgEoAAMAAAABAAIAAIdpAAQAAAABAAAAWgAAAAAAAABIAAAAAQAAAEgAAAABAAOgAQADAAAAAQABAACgAgAEAAAAAQAAACCgAwAEAAAAAQAAACAAAAAAX7wP8AAAAAlwSFlzAAALEwAACxMBAJqcGAAAAVlpVFh0WE1MOmNvbS5hZG9iZS54bXAAAAAAADx4OnhtcG1ldGEgeG1sbnM6eD0iYWRvYmU6bnM6bWV0YS8iIHg6eG1wdGs9IlhNUCBDb3JlIDYuMC4wIj4KICAgPHJkZjpSREYgeG1sbnM6cmRmPSJodHRwOi8vd3d3LnczLm9yZy8xOTk5LzAyLzIyLXJkZi1zeW50YXgtbnMjIj4KICAgICAgPHJkZjpEZXNjcmlwdGlvbiByZGY6YWJvdXQ9IiIKICAgICAgICAgICAgeG1sbnM6dGlmZj0iaHR0cDovL25zLmFkb2JlLmNvbS90aWZmLzEuMC8iPgogICAgICAgICA8dGlmZjpPcmllbnRhdGlvbj4xPC90aWZmOk9yaWVudGF0aW9uPgogICAgICA8L3JkZjpEZXNjcmlwdGlvbj4KICAgPC9yZGY6UkRGPgo8L3g6eG1wbWV0YT4KGV7hBwAAArVJREFUWAm9ljusTVEQhrdnRIFCh9CIROi8EoVGJUF940aLKOkkNPTiEYlKaIlS4nYKJDpucxMNhZsoRbxf/3fW/DF2zuac/TDJv+axZs2/1tqz9zlV1V6WpKXZTuHhzaWiAK2l7eIVwXhe+kLYjrXezKQLl0XiJumvAWzEc8UbaDTJXdX/GbgXXJ4biLqqfM2Hg/ibNGAjRwTEOcXrcXSnr1TNlwKkeQPEmEOcW7yeRp/soupB/iV0ti8Fl3PD7a78bLer1HcB0h+hs02MHMRritdx9Ov6UHUgpPvRGb6ROcURryleh9HXeUw1msi9EW9sNvi8tjW9T7FGFd7EBmg8E9a15xaVwxrENYo35egTXNU6yHzNdeLsO+dacLnGlNRVtTxW7JGGgAbLjZdJs53z9kYN1wr33yq/x0+UDoGfbyZrsp37NFHlmik83vSOz2kako+hmwjHxb2GGohrFi/GpgbhGpHNRVWrpDmV4xEeq8ghlzXIltFYvh9hTqa8OXrgheBT0mSQ2Lcm5gYkNi/sF/4qJqknEafgUWG9sFM4IbwX3NW8chbbzH0QTgo7hI3CbgFp4iqztTE3DE34QOAZ8nm9IfjUXDWwf1M2m1grPBPuCMhU5GXJ7285NwABN3IqJrdKPxJM/Fj2tpg7G/HX4aNabYCF/nk9JNtk87J3MSmZFY6PrKraJ70gOI9HgIzt/jI12egCl5Xu4uhbgh/L7drcGfkIj6OzuB/QzwXIP4d+Kw2IfQo9J42Q77WjQJfBt8C1+v+AN5HJ32l+QxD1+n+Amr7O07IhpfvZDOA1JDYjIM4tXo+jT3VfNSHkw+OPD32A+LaK1/Po12md6i4KbAK8ElYLiHOKN8Do6z2o2t7AgeDx3AC0f5Y00XWFr/xvcvj8etET7gvHYj/Dq0yY7amYfwF5Gt6/sbuW8wAAAABJRU5ErkJggg=="

# ===== Weather icons loaded from weather.json (same folder as app.py) =====
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
WEATHER_JSON_PATH = os.path.join(BASE_DIR, "weather.json")

def load_weather_icons() -> dict:
    try:
        with open(WEATHER_JSON_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("weather.json must be an object/dict")
        logger.info("Loaded weather.json with %d icons", len(data))
        return data
    except Exception as e:
        logger.error("Cannot load weather.json at %s: %s", WEATHER_JSON_PATH, e)
        return {}

WEATHER_ICONS = load_weather_icons()


def is_night_vn() -> bool:
    vn = ZoneInfo("Asia/Ho_Chi_Minh")
    h = datetime.now(vn).hour
    return (h < 6) or (h >= 18)


def meteo_code_to_icon_key(code: Optional[int], day_only: bool = False) -> str:
    # Keys match your icon set naming style in weather.json, e.g. "day-sunny", "night-clear", ...
    if code is None:
        return "na"

    night = False if day_only else is_night_vn()

    if code == 0:
        return "night-clear" if night else "day-sunny"
    if code == 1:
        return "night-alt-partly-cloudy" if night else "day-sunny-overcast"
    if code == 2:
        return "night-alt-partly-cloudy" if night else "day-cloudy"
    if code == 3:
        return "night-alt-cloudy" if night else "cloudy"

    if code in (45, 48):
        return "night-fog" if night else "day-fog"

    if code in (51, 53, 55):
        return "night-alt-sprinkle" if night else "day-sprinkle"
    if code in (56, 57):
        return "night-alt-sleet" if night else "day-sleet"

    if code in (61, 63):
        return "night-alt-rain" if night else "day-rain"
    if code == 65:
        return "night-alt-rain-wind" if night else "day-rain-wind"

    if code in (66, 67):
        return "night-alt-sleet" if night else "day-sleet"

    if code in (71, 73, 75):
        return "night-alt-snow" if night else "day-snow"
    if code == 77:
        return "snow"

    if code in (80, 81, 82):
        return "night-alt-showers" if night else "day-showers"

    if code in (85, 86):
        return "night-alt-snow" if night else "day-snow"

    if code == 95:
        return "night-alt-thunderstorm" if night else "day-thunderstorm"
    if code in (96, 99):
        return "night-alt-hail" if night else "day-hail"

    return "na"


def get_weather_icon_b64(code: Optional[int]) -> Optional[str]:
    day_only = os.getenv("WEATHER_DAY_ONLY", "").strip() in ("1", "true", "TRUE", "yes", "YES")
    key = meteo_code_to_icon_key(code, day_only=day_only)
    # Try exact, then fallback
    return WEATHER_ICONS.get(key) or WEATHER_ICONS.get("na") or None


# ===== Helpers =====
def vn_timestamp_str() -> str:
    vn = ZoneInfo("Asia/Ho_Chi_Minh")
    return datetime.now(vn).strftime("%d/%m/%Y %H:%M")

def safe_float(v, default=None):
    try:
        return float(v)
    except Exception:
        return default

def fmt_price(p: float) -> str:
    # "99,824.33" style
    return f"{p:,.2f}"

def fmt_change(cp: Optional[float]) -> str:
    # "+1.2% ↑" or "-2.0% ↓"
    if cp is None:
        return ""
    if isinstance(cp, float) and math.isnan(cp):
        return ""
    arrow = "↑" if cp >= 0 else "↓"
    sign = "+" if cp >= 0 else ""
    return f"{sign}{cp:.1f}% {arrow}"


# ===== Fetchers =====
async def fetch_binance_symbol(client: httpx.AsyncClient, symbol: str) -> dict:
    r = await client.get(BINANCE_24HR, params={"symbol": symbol})
    if r.status_code // 100 != 2:
        raise RuntimeError(f"Binance error {r.status_code}: {r.text}")
    data = r.json()
    price = safe_float(data.get("lastPrice"), None)
    cp = safe_float(data.get("priceChangePercent"), None)
    return {"price": price, "change_percent": cp}

async def fetch_weather_today(client: httpx.AsyncClient, lat: float, lon: float, tz: str) -> dict:
    params = {
        "latitude": lat,
        "longitude": lon,
        "timezone": tz,
        "current_weather": "true",
        "daily": "temperature_2m_max,temperature_2m_min,weathercode",
    }
    r = await client.get(OPEN_METEO, params=params)
    if r.status_code // 100 != 2:
        raise RuntimeError(f"Open-Meteo error {r.status_code}: {r.text}")

    data = r.json()
    current = data.get("current_weather") or {}
    daily = data.get("daily") or {}

    temp_now = safe_float(current.get("temperature"), None)
    code_now = current.get("weathercode", None)

    tmax_list = daily.get("temperature_2m_max") or []
    tmin_list = daily.get("temperature_2m_min") or []
    code_day_list = daily.get("weathercode") or []

    tmax = safe_float(tmax_list[0], None) if len(tmax_list) > 0 else None
    tmin = safe_float(tmin_list[0], None) if len(tmin_list) > 0 else None
    code_day = code_day_list[0] if len(code_day_list) > 0 else None

    return {
        "temp_now": temp_now,
        "tmin": tmin,
        "tmax": tmax,
        "code_now": code_now,
        "code_day": code_day,
    }


# ===== Dot Text API Sender =====
async def send_to_dot_text_api(
    client: httpx.AsyncClient,
    api_key: str,
    device_id: str,
    title: str,
    message: str,
    signature: str,
    icon_b64: Optional[str] = None,
) -> None:
    url = DOT_TEXT_API_V2.format(device_id=device_id)
    payload = {
        "title": title,
        "message": message,
        "signature": signature,
        "icon": icon_b64,  # base64 PNG
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        # avoid weird decompression errors behind proxies
        "Accept-Encoding": "identity",
    }

    r = await client.post(url, json=payload, headers=headers)
    if r.status_code // 100 != 2:
        logger.error("Dot Text API error %s: %s", r.status_code, r.text)
        raise RuntimeError(f"Dot Text API status {r.status_code}")

    logger.info("Dot Text API ok %s (title=%s)", r.status_code, title)


# ===== Main loop: BTC -> ETH -> WEATHER -> ... =====
async def ticker_loop() -> None:
    api_key = os.environ["DOT_API_KEY"]
    device_id = os.environ["DOT_DEVICE_ID"]

    interval = max(int(os.getenv("INTERVAL_SECS", "60")), 10)

    city = os.getenv("WEATHER_CITY", "Di Linh")
    lat = float(os.getenv("WEATHER_LAT", "11.617917"))
    lon = float(os.getenv("WEATHER_LON", "108.058922"))
    tz = os.getenv("WEATHER_TZ", "Asia/Ho_Chi_Minh")

    logger.info("Start loop interval=%ss city=%s lat=%s lon=%s", interval, city, lat, lon)

    timeout = httpx.Timeout(30.0)
    default_headers = {
        "User-Agent": "dot-text-rotator/1.0",
        "Accept-Encoding": "identity",
    }

    sequence = ["BTC", "ETH", "WEATHER"]
    idx = 0

    async with httpx.AsyncClient(timeout=timeout, headers=default_headers, follow_redirects=True) as client:
        while True:
            kind = sequence[idx % len(sequence)]
            idx += 1

            try:
                sig = vn_timestamp_str()

                if kind == "BTC":
                    data = await fetch_binance_symbol(client, "BTCUSDT")
                    price = data["price"]
                    cp = data["change_percent"]
                    if price is None:
                        raise RuntimeError("BTC price missing")

                    title = "BTC"
                    message = f"Price: {fmt_price(price)} USD\nChange: {fmt_change(cp)}"
                    await send_to_dot_text_api(
                        client, api_key, device_id, title, message, sig, icon_b64=BTC_ICON_B64
                    )

                elif kind == "ETH":
                    data = await fetch_binance_symbol(client, "ETHUSDT")
                    price = data["price"]
                    cp = data["change_percent"]
                    if price is None:
                        raise RuntimeError("ETH price missing")

                    title = "ETH"
                    message = f"Price: {fmt_price(price)} USD\nChange: {fmt_change(cp)}"
                    await send_to_dot_text_api(
                        client, api_key, device_id, title, message, sig, icon_b64=ETH_ICON_B64
                    )

                else:  # WEATHER
                    w = await fetch_weather_today(client, lat, lon, tz)
                    temp_now = w.get("temp_now")
                    tmax = w.get("tmax")
                    tmin = w.get("tmin")

                    # title: show temperature (rounded) like "26℃"
                    if isinstance(temp_now, (int, float)):
                        title = f"{temp_now:.0f}℃"
                    else:
                        title = "--℃"

                    desc = weather_desc_vi(w.get("code_now") or w.get("code_day"))
                    hi = f"{tmax:.0f}℃" if isinstance(tmax, (int, float)) else "--℃"
                    lo = f"{tmin:.0f}℃" if isinstance(tmin, (int, float)) else "--℃"

                    message = f"{city}\n{desc}\nH:{hi}  L:{lo}"

                    icon_b64 = get_weather_icon_b64(w.get("code_now") or w.get("code_day"))
                    await send_to_dot_text_api(
                        client, api_key, device_id, title, message, sig, icon_b64=icon_b64
                    )

            except Exception as e:
                logger.exception("Loop error (kind=%s): %s", kind, e)

            await asyncio.sleep(interval)


# ===== FastAPI healthcheck for Koyeb =====
app = FastAPI()

@app.on_event("startup")
async def on_startup() -> None:
    # Start background loop
    asyncio.create_task(ticker_loop())

@app.get("/health")
async def health():
    return {"ok": True}
