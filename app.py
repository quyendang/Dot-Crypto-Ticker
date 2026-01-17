import os
import io
import math
import base64
import asyncio
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

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
    61: "Mưa nhẹ",
    63: "Mưa",
    65: "Mưa to",
    71: "Tuyết nhẹ",
    73: "Tuyết",
    75: "Tuyết dày",
    80: "Mưa rào",
    81: "Mưa rào",
    82: "Mưa rào lớn",
    95: "Dông",
    96: "Dông kèm mưa đá",
    99: "Dông kèm mưa đá",
}

def weather_desc_vi(code: int | None) -> str:
    if code is None:
        return "Thời tiết"
    return WEATHER_TEXT_VI.get(code, f"Mã {code}")

# ===== Fonts =====
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FONT_DIR = os.path.join(BASE_DIR, "fonts")

logger.info("BASE_DIR=%s", BASE_DIR)
logger.info("FONT_DIR=%s", FONT_DIR)
logger.info("Font files=%s", os.listdir(FONT_DIR))

def load_font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    if bold:
        font_path = os.path.join(FONT_DIR, "DejaVuSans-Bold.ttf")
    else:
        font_path = os.path.join(FONT_DIR, "DejaVuSans.ttf")

    try:
        return ImageFont.truetype(font_path, size=size)
    except Exception as e:
        print(f"[WARN] Cannot load font {font_path}: {e}")
        return ImageFont.load_default()

FONT_TITLE = load_font(16, bold=True)
FONT_BODY  = load_font(16, bold=True)
FONT_SMALL = load_font(12, bold=True)
FONT_TEMP  = load_font(30, bold=True)
FONT_BADGE = load_font(14, bold=True)


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


def draw_text_bold(d: ImageDraw.ImageDraw, xy, text: str, font, fill=255, stroke=1):
    # stroke giúp nét dày hơn, e-ink dễ đọc
    d.text(
        xy,
        text,
        font=font,
        fill=fill,
        stroke_width=stroke,
        stroke_fill=fill,
    )

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

def draw_badge_1bit(d: ImageDraw.ImageDraw, x: int, y: int, label: str):
    r = 12
    d.ellipse((x, y, x + 2*r, y + 2*r), outline=255, width=3)
    w = d.textlength(label, font=FONT_BADGE)
    draw_text_bold(d, (x + r - w/2, y + r - 8), label, FONT_BADGE, fill=255, stroke=1)

def render_png_crypto(prices: list[dict]) -> bytes:
    img = Image.new("1", (W, H), 0)
    d = ImageDraw.Draw(img)

    vn_tz = ZoneInfo("Asia/Ho_Chi_Minh")
    now_vn = datetime.now(vn_tz)
    header = now_vn.strftime("%d/%m %H:%M")

    # Title + time
    draw_text_bold(d, (8, 6), "CRYPTO", FONT_TITLE, fill=255, stroke=2)
    w_right = d.textlength(header, font=FONT_TITLE)
    draw_text_bold(d, (W - 8 - w_right, 6), header, FONT_TITLE, fill=255, stroke=1)

    d.line((8, 30, W - 8, 30), fill=255, width=1)

    # Big rows (2 coins)
    y = 44
    for p in prices[:2]:
        sym = p["symbol"]
        price = fmt_price_usd(p["price"])
        chg = fmt_change(p.get("change_percent_24h"))

        # Badge bigger
        badge_char = "B" if sym == "BTC" else ("E" if sym == "ETH" else sym[:1])
        draw_badge_1bit(d, 10, y - 10, badge_char)

        # Symbol
        draw_text_bold(d, (44, y - 10), sym, load_font(22, bold=True), fill=255, stroke=2)

        # Price right aligned
        f_price = load_font(22, bold=True)
        pw = d.textlength(price, font=f_price)
        draw_text_bold(d, (W - 8 - pw, y - 10), price, f_price, fill=255, stroke=2)

        # Change line (smaller)
        if chg:
            draw_text_bold(d, (44, y + 18), chg, load_font(14, bold=True), fill=255, stroke=1)

        y += 54

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def render_png_weather(city: str, weather: dict) -> bytes:
    img = Image.new("1", (W, H), 0)
    d = ImageDraw.Draw(img)

    vn_tz = ZoneInfo("Asia/Ho_Chi_Minh")
    now_vn = datetime.now(vn_tz)
    header = now_vn.strftime("%d/%m %H:%M")

    # Title + time
    draw_text_bold(d, (8, 6), "THOI TIET", FONT_TITLE, fill=255, stroke=2)
    w_right = d.textlength(header, font=FONT_TITLE)
    draw_text_bold(d, (W - 8 - w_right, 6), header, FONT_TITLE, fill=255, stroke=1)

    d.line((8, 30, W - 8, 30), fill=255, width=1)

    # City (big)
    city_line = city
    draw_text_bold(d, (8, 40), city_line, load_font(20, bold=True), fill=255, stroke=2)

    # Description (short)
    desc = weather_desc_vi(weather.get("code_now") or weather.get("code_day"))
    if len(desc) > 20:
        desc = desc[:20] + "…"
    draw_text_bold(d, (8, 66), desc, load_font(14, bold=True), fill=255, stroke=1)

    # Temp big
    temp_now = weather.get("temp_now")
    tmin = weather.get("tmin")
    tmax = weather.get("tmax")

    temp_str = f"{temp_now:.0f}°C" if isinstance(temp_now, (int, float)) else "--°C"
    draw_text_bold(d, (8, 86), temp_str, load_font(44, bold=True), fill=255, stroke=2)

    # Min/Max
    mm = "Min/Max "
    if isinstance(tmin, (int, float)) and isinstance(tmax, (int, float)):
        mm += f"{tmin:.0f}/{tmax:.0f}°"
    else:
        mm += "--/--"
    draw_text_bold(d, (170, 110), mm, load_font(14, bold=True), fill=255, stroke=1)

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
    payload = {"image": b64, "border": 0, "ditherType": "NONE"}

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
    city = os.getenv("WEATHER_CITY", "Di Linh")
    lat = float(os.getenv("WEATHER_LAT", "11.617810"))
    lon = float(os.getenv("WEATHER_LON", "108.059262"))
    tz = os.getenv("WEATHER_TZ", "Asia/Ho_Chi_Minh")

    logger.info("Starting IMAGE ticker (interval=%ss, city=%s, lat=%s, lon=%s)", interval_secs, city, lat, lon)

    timeout = httpx.Timeout(30.0)
    default_headers = {
        "User-Agent": "dot-crypto-image/0.1",
        "Accept-Encoding": "identity",
    }

    async with httpx.AsyncClient(timeout=timeout, headers=default_headers, follow_redirects=True) as client:
        show_crypto = True  # bắt đầu bằng crypto
        while True:
            try:
                if show_crypto:
                    prices = await fetch_prices_binance(client)
                    png = render_png_crypto(prices)
                    logger.info("Render: CRYPTO")
                else:
                    weather = await fetch_weather_today(client, lat, lon, tz)
                    png = render_png_weather(city, weather)
                    logger.info("Render: WEATHER")

                await send_to_dot_image_api(client, api_key, device_id, png)
                show_crypto = not show_crypto  # đảo mode cho lần sau

            except Exception as e:
                logger.exception("Loop error: %s", e)

            await asyncio.sleep(60)  # mỗi 1 phút đổi màn hình



# ===== FastAPI =====
app = FastAPI()

@app.on_event("startup")
async def on_startup() -> None:
    asyncio.create_task(ticker_loop())

@app.get("/health")
async def health():
    return {"ok": True}
