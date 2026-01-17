import os
import io
import math
import base64
import asyncio
import logging
from datetime import datetime

import httpx
from fastapi import FastAPI
from dotenv import load_dotenv
from PIL import Image, ImageDraw, ImageFont

load_dotenv()

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger("dot-crypto-ticker-image")

# ===== APIs =====
BINANCE_24HR = "https://api.binance.com/api/v3/ticker/24hr"
DOT_IMAGE_API_V2 = "https://dot.mindreset.tech/api/authV2/open/device/{device_id}/image"

# Open-Meteo (no key)
OPEN_METEO = "https://api.open-meteo.com/v1/forecast"

SYMBOLS = ["BTCUSDT", "ETHUSDT"]
DISPLAY_MAP = {"BTCUSDT": "BTC", "ETHUSDT": "ETH"}

# ===== Rendering constants =====
W, H = 296, 152
BG = (0, 0, 0)
FG = (255, 255, 255)
MUTED = (180, 180, 180)

# ===== Weather code mapping (Open-Meteo) =====
WEATHER_TEXT = {
    0: "Clear",
    1: "Mainly clear",
    2: "Partly cloudy",
    3: "Overcast",
    45: "Fog",
    48: "Rime fog",
    51: "Light drizzle",
    53: "Drizzle",
    55: "Heavy drizzle",
    61: "Light rain",
    63: "Rain",
    65: "Heavy rain",
    71: "Light snow",
    73: "Snow",
    75: "Heavy snow",
    80: "Rain showers",
    81: "Rain showers",
    82: "Heavy showers",
    95: "Thunderstorm",
    96: "Thunderstorm+hail",
    99: "Thunderstorm+hail",
}

def weather_desc(code: int | None) -> str:
    if code is None:
        return "Weather"
    return WEATHER_TEXT.get(code, f"Code {code}")

# ===== Fonts =====
def load_font(size: int):
    font_path = os.path.join(os.path.dirname(__file__), "fonts", "DejaVuSans.ttf")
    try:
        return ImageFont.truetype(font_path, size=size)
    except Exception:
        return ImageFont.load_default()

FONT_TITLE = load_font(14)
FONT_BODY  = load_font(13)
FONT_SMALL = load_font(11)
FONT_BADGE = load_font(12)


# ===== Format helpers =====
def fmt_price_usd(p: float) -> str:
    # Compact: BTC might be huge; still fit.
    if p >= 1000:
        return f"${p:,.0f}"
    return f"${p:.2f}"

def fmt_change(cp: float | None) -> str:
    if cp is None or math.isnan(cp):
        return ""
    arrow = "↗" if cp >= 0 else "↘"
    sign = "+" if cp >= 0 else ""
    return f"{arrow}{sign}{cp:.1f}%"

def safe_float(v, default=None):
    try:
        return float(v)
    except Exception:
        return default


# ===== Data fetch =====
async def fetch_prices_binance(client: httpx.AsyncClient) -> list[dict]:
    out: list[dict] = []
    for sym in SYMBOLS:
        try:
            r = await client.get(BINANCE_24HR, params={"symbol": sym})
            if r.status_code // 100 != 2:
                logger.error("Binance error %s %s: %s", r.status_code, sym, r.text)
                continue
            data = r.json()
            price = safe_float(data.get("lastPrice"), 0.0) or 0.0
            cp = safe_float(data.get("priceChangePercent"), None)
            out.append({
                "symbol": DISPLAY_MAP.get(sym, sym),
                "price": price,
                "change_percent_24h": cp,
            })
        except Exception as e:
            logger.exception("Binance fetch failed for %s: %s", sym, e)
    # Ensure order BTC then ETH
    priority = {"BTC": 0, "ETH": 1}
    out.sort(key=lambda x: priority.get(x["symbol"], 9))
    return out

async def fetch_weather_today(client: httpx.AsyncClient, lat: float, lon: float, tz: str) -> dict:
    """
    Returns dict with:
      temp_now, tmin, tmax, code_now, code_day
    """
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


# ===== Image rendering =====
def draw_badge(draw: ImageDraw.ImageDraw, x: int, y: int, label: str):
    # Minimal “logo-like” badge: circle outline + label inside
    r = 10
    draw.ellipse((x, y, x + 2*r, y + 2*r), outline=FG, width=2)
    # center label
    w = draw.textlength(label, font=FONT_BADGE)
    draw.text((x + r - w/2, y + r - 6), label, font=FONT_BADGE, fill=FG)

def render_png(
    city: str,
    prices: list[dict],
    weather: dict,
) -> bytes:
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)

    # Header
    now = datetime.now()
    header_left = "Crypto + Weather"
    header_right = now.strftime("%d/%m %H:%M")
    d.text((10, 8), header_left, font=FONT_TITLE, fill=FG)
    w_right = d.textlength(header_right, font=FONT_TITLE)
    d.text((W - 10 - w_right, 8), header_right, font=FONT_TITLE, fill=MUTED)

    # Divider line
    d.line((10, 28, W - 10, 28), fill=(80, 80, 80), width=1)

    # Weather block (top-left)
    # Layout: City + desc on one line, temp now big-ish, min/max small
    city_line = city
    desc_line = weather_desc(weather.get("code_now") or weather.get("code_day"))

    d.text((10, 34), city_line, font=FONT_BODY, fill=FG)
    d.text((10, 50), desc_line, font=FONT_SMALL, fill=MUTED)

    temp_now = weather.get("temp_now")
    tmin = weather.get("tmin")
    tmax = weather.get("tmax")

    temp_str = f"{temp_now:.0f}°C" if isinstance(temp_now, (int, float)) else "--°C"
    d.text((10, 66), temp_str, font=load_font(26), fill=FG)

    mm = "Min/Max: "
    if isinstance(tmin, (int, float)) and isinstance(tmax, (int, float)):
        mm += f"{tmin:.0f}° / {tmax:.0f}°"
    else:
        mm += "-- / --"
    d.text((10, 96), mm, font=FONT_SMALL, fill=MUTED)

    # Prices block (right side)
    # Reserve right panel x from 155..286
    panel_x = 155
    d.text((panel_x, 34), "Prices (USDT)", font=FONT_BODY, fill=FG)
    d.line((panel_x, 54, W - 10, 54), fill=(80, 80, 80), width=1)

    y = 62
    for p in prices[:2]:
        sym = p["symbol"]
        price_str = fmt_price_usd(p["price"])
        chg_str = fmt_change(p.get("change_percent_24h"))

        # badge "B" for BTC, "E" for ETH
        badge_char = "B" if sym == "BTC" else ("E" if sym == "ETH" else sym[:1])
        draw_badge(d, panel_x, y, badge_char)

        # symbol + price
        d.text((panel_x + 26, y - 2), sym, font=FONT_BODY, fill=FG)
        price_w = d.textlength(price_str, font=FONT_BODY)
        d.text((W - 10 - price_w, y - 2), price_str, font=FONT_BODY, fill=FG)

        # change line
        if chg_str:
            d.text((panel_x + 26, y + 14), chg_str, font=FONT_SMALL, fill=MUTED)

        y += 40

    # Footer hint
    d.text((10, H - 16), "Updated automatically", font=FONT_SMALL, fill=(120, 120, 120))

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


# ===== Dot API call (Image) =====
async def send_to_dot_image_api(
    client: httpx.AsyncClient,
    api_key: str,
    device_id: str,
    png_bytes: bytes,
) -> None:
    url = DOT_IMAGE_API_V2.format(device_id=device_id)
    b64 = base64.b64encode(png_bytes).decode("ascii")
    payload = {"image": b64, "border": 0, "ditherType": "ORDERED"}

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept-Encoding": "identity",  # tránh lỗi decompress lạ
    }

    r = await client.post(url, json=payload, headers=headers)
    body = r.text
    if r.status_code // 100 != 2:
        logger.error("Dot Image API error %s: %s", r.status_code, body)
        raise RuntimeError(f"Dot Image API status {r.status_code}")

    logger.info("Dot Image API ok %s", r.status_code)


# ===== Main loop =====
async def ticker_loop() -> None:
    api_key = os.environ["DOT_API_KEY"]
    device_id = os.environ["DOT_DEVICE_ID"]
    interval_secs = max(int(os.getenv("INTERVAL_SECS", "600")), 2)

    # Default location: Ho Chi Minh City
    city = os.getenv("WEATHER_CITY", "Ho Chi Minh")
    lat = float(os.getenv("WEATHER_LAT", "10.8231"))
    lon = float(os.getenv("WEATHER_LON", "106.6297"))
    tz = os.getenv("WEATHER_TZ", "Asia/Ho_Chi_Minh")

    logger.info("Starting IMAGE ticker (interval=%ss, city=%s, lat=%s, lon=%s)", interval_secs, city, lat, lon)

    timeout = httpx.Timeout(30.0)
    default_headers = {
        "User-Agent": "dot-crypto-image/0.1",
        "Accept-Encoding": "identity",
    }

    async with httpx.AsyncClient(timeout=timeout, headers=default_headers, follow_redirects=True) as client:
        while True:
            try:
                prices_task = fetch_prices_binance(client)
                weather_task = fetch_weather_today(client, lat, lon, tz)
                prices, weather = await asyncio.gather(prices_task, weather_task)

                png = render_png(city=city, prices=prices, weather=weather)
                await send_to_dot_image_api(client, api_key, device_id, png)

                logger.info("Pushed image (BTC/ETH + weather)")
            except Exception as e:
                logger.exception("Loop error: %s", e)

            await asyncio.sleep(interval_secs)


# ===== FastAPI =====
app = FastAPI()

@app.on_event("startup")
async def on_startup() -> None:
    asyncio.create_task(ticker_loop())

@app.get("/health")
async def health():
    return {"ok": True}
