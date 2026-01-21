# app.py
# Dot Text API v2: rotate every N seconds -> BTC -> ETH -> WEATHER -> ...
# Designed for Koyeb: FastAPI healthcheck + robust background task with lifespan.

import os
import json
import math
import asyncio
import logging
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Optional, Dict, Any

import httpx
from fastapi import FastAPI
from fastapi import Request
from contextlib import asynccontextmanager
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger("dot-text-rotator")

# ===== APIs =====
BINANCE_24HR = "https://api.binance.com/api/v3/ticker/24hr"
BINANCE_KLINES = "https://api.binance.com/api/v3/klines"
OPEN_METEO = "https://api.open-meteo.com/v1/forecast"
DOT_TEXT_API_V2 = "https://dot.mindreset.tech/api/authV2/open/device/{device_id}/text"
TET_COUNTDOWN_API = "https://open.oapi.vn/holiday/tet/countdown"
LUNAR_DATE_API = "https://open.oapi.vn/date/convert-to-lunar"

# ===== Weather text VI =====
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

ETH_ICON_B64 = "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"
# Tet countdown icon (base64)
TET_ICON_B64 = "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"

# ===== Paths =====
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
WEATHER_JSON_PATH = os.path.join(BASE_DIR, "weather.json")

def load_weather_icons() -> Dict[str, str]:
    try:
        with open(WEATHER_JSON_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("weather.json must be an object/dict")
        logger.info("Loaded weather.json (%d icons)", len(data))
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
    day_only = os.getenv("WEATHER_DAY_ONLY", "").strip().lower() in ("1", "true", "yes")
    key = meteo_code_to_icon_key(code, day_only=day_only)
    return WEATHER_ICONS.get(key) or WEATHER_ICONS.get("na") or None

def vn_timestamp_str() -> str:
    vn = ZoneInfo("Asia/Ho_Chi_Minh")
    return datetime.now(vn).strftime("%d/%m/%Y %H:%M")

def safe_float(v, default=None):
    try:
        return float(v)
    except Exception:
        return default

def fmt_price(p: float) -> str:
    return f"{p:,.2f}"

def fmt_change(cp: Optional[float]) -> str:
    if cp is None:
        return ""
    if isinstance(cp, float) and math.isnan(cp):
        return ""
    arrow = "↑" if cp >= 0 else "↓"
    sign = "+" if cp >= 0 else ""
    return f"{sign}{cp:.1f}% {arrow}"

def calculate_rsi(prices: list[float], period: int = 14) -> Optional[float]:
    """Calculate RSI from a list of closing prices"""
    if len(prices) < period + 1:
        return None
    
    # Calculate price changes
    deltas = []
    for i in range(1, len(prices)):
        deltas.append(prices[i] - prices[i - 1])
    
    # Separate gains and losses
    gains = [d if d > 0 else 0.0 for d in deltas]
    losses = [-d if d < 0 else 0.0 for d in deltas]
    
    # Calculate average gain and loss using Wilder's smoothing
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    
    # Apply Wilder's smoothing for remaining periods
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    
    # Calculate RSI
    if avg_loss == 0:
        return 100.0
    
    rs = avg_gain / avg_loss
    rsi = 100.0 - (100.0 / (1.0 + rs))
    return round(rsi, 2)

async def fetch_rsi(client: httpx.AsyncClient, symbol: str, interval: str) -> Optional[float]:
    """Fetch klines for a given interval and calculate RSI"""
    try:
        # Fetch last 100 candles - need at least 15 for RSI(14)
        params = {
            "symbol": symbol,
            "interval": interval,
            "limit": 100
        }
        r = await client.get(BINANCE_KLINES, params=params)
        if r.status_code // 100 != 2:
            logger.warning(f"Binance klines error {r.status_code} for {symbol} interval {interval}")
            return None
        
        klines = r.json()
        # Kline format: [timestamp, open, high, low, close, volume, ...]
        # Extract close prices (index 4)
        close_prices = [safe_float(k[4], 0.0) for k in klines if safe_float(k[4], None) is not None]
        
        if len(close_prices) < 15:
            logger.warning(f"Not enough data for RSI calculation: {len(close_prices)} candles")
            return None
        
        rsi = calculate_rsi(close_prices)
        return rsi
    except Exception as e:
        logger.warning(f"Error calculating RSI for {symbol} interval {interval}: {e}")
        return None

async def fetch_binance_symbol(client: httpx.AsyncClient, symbol: str) -> Dict[str, Any]:
    r = await client.get(BINANCE_24HR, params={"symbol": symbol})
    if r.status_code // 100 != 2:
        raise RuntimeError(f"Binance error {r.status_code}: {r.text}")
    data = r.json()
    
    # Fetch RSI 4h and 1d
    rsi_4h = await fetch_rsi(client, symbol, "4h")
    rsi_1d = await fetch_rsi(client, symbol, "1d")
    
    return {
        "price": safe_float(data.get("lastPrice"), None),
        "change_percent": safe_float(data.get("priceChangePercent"), None),
        "rsi_4h": rsi_4h,
        "rsi_1d": rsi_1d,
    }

async def fetch_weather_today(client: httpx.AsyncClient, lat: float, lon: float, tz: str) -> Dict[str, Any]:
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

async def fetch_tet_countdown(client: httpx.AsyncClient) -> Dict[str, Any]:
    """Fetch Tet countdown from open.oapi.vn"""
    r = await client.get(TET_COUNTDOWN_API)
    if r.status_code // 100 != 2:
        raise RuntimeError(f"Tet countdown API error {r.status_code}: {r.text}")
    data = r.json()
    if data.get("code") != "success":
        raise RuntimeError(f"Tet countdown API returned error: {data.get('message')}")
    return data.get("data", {})

async def fetch_lunar_date(client: httpx.AsyncClient, day: int, month: int, year: int) -> Dict[str, Any]:
    """Convert solar date to lunar date"""
    payload = {
        "day": day,
        "month": month,
        "year": year,
    }
    r = await client.post(LUNAR_DATE_API, json=payload, headers={"Content-Type": "application/json"})
    if r.status_code // 100 != 2:
        raise RuntimeError(f"Lunar date API error {r.status_code}: {r.text}")
    data = r.json()
    if data.get("code") != "success":
        raise RuntimeError(f"Lunar date API returned error: {data.get('message')}")
    return data.get("data", {})

async def send_to_dot_text_api(
    client: httpx.AsyncClient,
    api_key: str,
    device_id: str,
    title: str,
    message: str,
    signature: str,
    icon_b64: Optional[str],
) -> None:
    url = DOT_TEXT_API_V2.format(device_id=device_id)
    payload = {
        "title": title,
        "message": message,
        "signature": signature,
        "icon": icon_b64,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept-Encoding": "identity",
    }
    r = await client.post(url, json=payload, headers=headers)
    if r.status_code // 100 != 2:
        raise RuntimeError(f"Dot Text API {r.status_code}: {r.text}")

# ===== Background loop (robust) =====
async def ticker_loop() -> None:
    api_key = os.environ["DOT_API_KEY"]
    device_id = os.environ["DOT_DEVICE_ID"]

    interval = int(os.getenv("INTERVAL_SECS", "60"))
    if interval < 30:
        interval = 30  # safety: don't spam too hard; change if you really want 60 exact
    # If you want exact 60 always, set min=60 instead.

    city = os.getenv("WEATHER_CITY", "Di Linh")
    lat = float(os.getenv("WEATHER_LAT", "11.617917"))
    lon = float(os.getenv("WEATHER_LON", "108.058922"))
    tz = os.getenv("WEATHER_TZ", "Asia/Ho_Chi_Minh")

    logger.info("ticker_loop started: interval=%ss seq=BTC->ETH->WEATHER->DAY", interval)

    timeout = httpx.Timeout(30.0)
    default_headers = {"User-Agent": "dot-text-rotator/2.0", "Accept-Encoding": "identity"}

    seq = ["BTC", "ETH", "WEATHER", "DAY"]
    idx = 0

    async with httpx.AsyncClient(timeout=timeout, headers=default_headers, follow_redirects=True) as client:
        while True:
            kind = seq[idx % len(seq)]
            logger.info("Tick -> kind=%s idx=%d", kind, idx)

            try:
                sig = vn_timestamp_str()

                if kind == "BTC":
                    data = await fetch_binance_symbol(client, "BTCUSDT")
                    price = data["price"]
                    cp = data["change_percent"]
                    rsi_4h = data.get("rsi_4h")
                    rsi_1d = data.get("rsi_1d")
                    if price is None:
                        raise RuntimeError("BTC price missing")
                    title = "BTC"
                    rsi4_text = f"RSI 4h: {rsi_4h:.2f}" if rsi_4h is not None else "RSI 4h: N/A"
                    rsi1d_text = f"1d: {rsi_1d:.2f}" if rsi_1d is not None else "1d: N/A"
                    message = f"Price: {fmt_price(price)} USD\nChange: {fmt_change(cp)}\n{rsi4_text}   {rsi1d_text}"
                    await send_to_dot_text_api(client, api_key, device_id, title, message, sig, BTC_ICON_B64)

                elif kind == "ETH":
                    data = await fetch_binance_symbol(client, "ETHUSDT")
                    price = data["price"]
                    cp = data["change_percent"]
                    rsi_4h = data.get("rsi_4h")
                    rsi_1d = data.get("rsi_1d")

                    data_btc = await fetch_binance_symbol(client, "BTCUSDT")
                    price_btc = data_btc["price"]
                    cp_btc = data_btc["change_percent"]
                    rsi_4h_btc = data_btc.get("rsi_4h")
                    rsi_1d_btc = data_btc.get("rsi_1d")
                    
                    if price is None:
                        raise RuntimeError("ETH price missing")

                    # Determine title based on RSI thresholds
                    title = "ETH"
                    if (rsi_4h is not None and rsi_4h_btc is not None and
                        rsi_4h < 30 and rsi_4h_btc < 30):
                        title = "ETH (BUY ↑)"
                    elif (rsi_4h is not None and rsi_4h_btc is not None and
                          rsi_4h > 70 and rsi_4h_btc > 70):
                        title = "ETH (SELL ↓)"
                    else:
                        title = "ETH"

                    rsi4_text = f"RSI 4h: {rsi_4h:.2f}" if rsi_4h is not None else "RSI 4h: N/A"
                    rsi1d_text = f"1d: {rsi_1d:.2f}" if rsi_1d is not None else "1d: N/A"
                    message = f"Price: {fmt_price(price)} USD\nChange: {fmt_change(cp)}\n{rsi4_text}   {rsi1d_text}"
                    await send_to_dot_text_api(client, api_key, device_id, title, message, sig, ETH_ICON_B64)

                elif kind == "WEATHER":
                    w = await fetch_weather_today(client, lat, lon, tz)
                    temp_now = w.get("temp_now")
                    tmax = w.get("tmax")
                    tmin = w.get("tmin")
                    title = f"{temp_now:.0f}℃" if isinstance(temp_now, (int, float)) else "--℃"
                    desc = weather_desc_vi(w.get("code_now") or w.get("code_day"))
                    hi = f"{tmax:.0f}℃" if isinstance(tmax, (int, float)) else "--℃"
                    lo = f"{tmin:.0f}℃" if isinstance(tmin, (int, float)) else "--℃"
                    message = f"{city}\n{desc}\nH:{hi}  L:{lo}"
                    icon_b64 = get_weather_icon_b64(w.get("code_now") or w.get("code_day"))
                    await send_to_dot_text_api(client, api_key, device_id, title, message, sig, icon_b64)

                else:  # DAY - Tet countdown + Lunar date
                    # Get current date in VN timezone
                    vn = ZoneInfo("Asia/Ho_Chi_Minh")
                    now_vn = datetime.now(vn)
                    current_day = now_vn.day
                    current_month = now_vn.month
                    current_year = now_vn.year
                    
                    # Fetch Tet countdown
                    tet_data = await fetch_tet_countdown(client)
                    dayCount = tet_data.get("remainingDays", 0)
                    countdown_text = f"Còn {dayCount} ngày"
                    
                    
                    # Fetch lunar date
                    lunar_data = await fetch_lunar_date(client, current_day, current_month, current_year)
                    day_am = lunar_data.get("day", 0)
                    month_am = lunar_data.get("month", 0)
                    year_am = lunar_data.get("sexagenaryCycle", "N/A")
                    
                    # Format message
                    title = f"{countdown_text} dến TẾT"
                    message = f"Hôm nay: {current_day}/{current_month}/{current_year}\nÂm lịch: {day_am}/{month_am}  {year_am}"
                    signature = "++++++++++⁠"
                    
                    await send_to_dot_text_api(client, api_key, device_id, title, message, signature, TET_ICON_B64)

                # ✅ only advance when success
                idx += 1
                logger.info("Sent OK -> advance idx=%d", idx)

            except Exception as e:
                logger.exception("Tick failed (kind=%s). Will retry same kind next tick. Error=%s", kind, e)

            await asyncio.sleep(interval)

# ===== FastAPI lifespan to keep task alive =====
_task: Optional[asyncio.Task] = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _task
    logger.info("App lifespan startup")
    _task = asyncio.create_task(ticker_loop())
    try:
        yield
    finally:
        logger.info("App lifespan shutdown: cancelling task")
        if _task:
            _task.cancel()
            try:
                await _task
            except asyncio.CancelledError:
                pass

app = FastAPI(lifespan=lifespan)

@app.get("/health")
async def health():
    return {"ok": True}
