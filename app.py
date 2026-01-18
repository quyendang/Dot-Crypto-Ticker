# app.py
# Pixel-style Dot E-Ink dashboard: alternates CRYPTO <-> WEATHER every 60s
# Output: 296x152 PNG (Base64) -> Dot Image API v2
#
# Folder layout:
#   .
#   ├── app.py
#   ├── requirements.txt
#   ├── fonts/
#   │   └── pixel.ttf                (REQUIRED for best pixel look)
#   └── assets/                      (OPTIONAL icons)
#       ├── btc.png
#       ├── eth.png
#       ├── w_sun.png
#       ├── w_cloud.png
#       ├── w_rain.png
#       └── w_storm.png

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
logger = logging.getLogger("dot-pixel-dashboard")

# ===== APIs =====
BINANCE_24HR = "https://api.binance.com/api/v3/ticker/24hr"
OPEN_METEO = "https://api.open-meteo.com/v1/forecast"
DOT_IMAGE_API_V2 = "https://dot.mindreset.tech/api/authV2/open/device/{device_id}/image"

# ===== Coins =====
SYMBOLS = ["BTCUSDT", "ETHUSDT"]
DISPLAY_MAP = {"BTCUSDT": "BTC", "ETHUSDT": "ETH"}

# ===== Canvas =====
W, H = 296, 152
SCALE = 2  # render at 2x then downscale NEAREST for crisp pixel look

# ===== Weather text VI (you can shorten if you prefer) =====
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


# ===== Paths =====
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FONT_DIR = os.path.join(BASE_DIR, "fonts")
ASSET_DIR = os.path.join(BASE_DIR, "assets")


# ===== Font (pixel) =====
def load_pixel_font(size: int) -> ImageFont.ImageFont:
    """
    Load pixel font from ./fonts/pixel.ttf
    If missing, falls back to default (will look less pixel-perfect).
    """
    font_path = os.path.join(FONT_DIR, "pixel.ttf")
    try:
        return ImageFont.truetype(font_path, size=size)
    except Exception as e:
        logger.warning("Cannot load pixel.ttf at %s (%s). Using default font.", font_path, e)
        return ImageFont.load_default()

# ===== Font (default with size) =====
def load_default_font(size: int) -> ImageFont.ImageFont:
    """
    Load font with specified size from FONT_DIR.
    Tries common monospace font names, falls back to default if not found.
    """
    # Try common monospace font names in FONT_DIR
    font_names = [
        "DejaVuSansMono.ttf",
        "LiberationMono-Regular.ttf",
        "NotoMono-Regular.ttf",
        "Courier-New.ttf",
        "monospace.ttf",
    ]
    
    for font_name in font_names:
        font_path = os.path.join(FONT_DIR, font_name)
        try:
            if os.path.exists(font_path):
                return ImageFont.truetype(font_path, size=size)
        except Exception:
            continue
    
    # Fallback: use default font (fixed size, but we'll scale text manually)
    return ImageFont.load_default()


# ===== Pixel draw helpers =====
def px_text(d: ImageDraw.ImageDraw, x: int, y: int, text: str, font, fill=0):
    d.text((x, y), text, font=font, fill=fill)


def px_rect(d: ImageDraw.ImageDraw, x0: int, y0: int, x1: int, y1: int, w: int = 2, fill=0):
    for i in range(w):
        d.rectangle((x0 + i, y0 + i, x1 - i, y1 - i), outline=fill)


def px_hline(d: ImageDraw.ImageDraw, x0: int, x1: int, y: int, w: int = 1, fill=0):
    for i in range(w):
        d.line((x0, y + i, x1, y + i), fill=fill)


def px_dotted_hline(d: ImageDraw.ImageDraw, x0: int, x1: int, y: int, step: int = 4, fill=0):
    x = x0
    while x <= x1:
        d.point((x, y), fill=fill)
        x += step


def make_canvas_2x() -> Image.Image:
    return Image.new("1", (W * SCALE, H * SCALE), 255)  # Background trắng (255)


def load_icon(name: str) -> Image.Image | None:
    path = os.path.join(ASSET_DIR, name)
    if not os.path.exists(path):
        logger.warning("Icon not found: %s", path)
        return None
    
    try:
        im = Image.open(path)
        
        # Convert to RGBA to handle transparency
        if im.mode != "RGBA":
            im = im.convert("RGBA")
        
        # Create a temporary image to extract the icon shape
        # Convert to grayscale for thresholding
        gray = im.convert("L")
        
        # Create mask: anything that's not fully transparent becomes part of the icon
        # For RGBA, alpha channel < 128 means transparent
        alpha = im.split()[3] if im.mode == "RGBA" else None
        
        # Convert to black and white: icon parts become black (0), background becomes white (255)
        # Strategy: if pixel is not transparent and has color, make it black
        if alpha:
            # Create mask from alpha channel
            mask = alpha.point(lambda x: 0 if x < 128 else 255, mode="1")
            # Create black icon: where mask is True, set to black (0)
            icon_bw = Image.new("1", im.size, 255)  # Start with white
            # Where mask is True (icon area), set to black
            icon_bw.paste(0, mask=mask)
        else:
            # No alpha channel, use grayscale threshold
            # Assume darker areas are the icon
            threshold = 128
            icon_bw = gray.point(lambda x: 0 if x < threshold else 255, mode="1")
            # Invert so icon is black
            icon_bw = Image.eval(icon_bw, lambda x: 255 - x)
        
        # Resize with nearest neighbor to keep pixelated look
        icon_bw = icon_bw.resize((icon_bw.size[0] * SCALE, icon_bw.size[1] * SCALE), Image.NEAREST)
        
        logger.info("Loaded icon %s: size=%s, mode=%s", name, icon_bw.size, icon_bw.mode)
        return icon_bw
        
    except Exception as e:
        logger.error("Error loading icon %s: %s", path, e)
        return None


def is_night_vn() -> bool:
    # Nếu muốn chọn icon day/night theo giờ VN
    vn_tz = ZoneInfo("Asia/Ho_Chi_Minh")
    h = datetime.now(vn_tz).hour
    return (h < 6) or (h >= 18)


def weather_icon_name(code: int | None) -> str:
    """
    Open-Meteo weathercode -> weather-icons-master png_48 icon filename.
    Ref codes (Open-Meteo):
      0 Clear sky
      1,2,3 Mainly clear/Partly cloudy/Overcast
      45,48 Fog
      51,53,55 Drizzle
      56,57 Freezing drizzle
      61,63,65 Rain
      66,67 Freezing rain
      71,73,75 Snow fall
      77 Snow grains
      80,81,82 Rain showers
      85,86 Snow showers
      95 Thunderstorm
      96,99 Thunderstorm with hail
    """

    # Nếu không có code, fallback
    if code is None:
        return "w_na.png"

    night = is_night_vn()

    # ===== Clear / Cloud =====
    if code == 0:
        return "w_night-clear.png" if night else "w_day-sunny.png"

    if code == 1:
        # Mainly clear
        return "w_night-alt-partly-cloudy.png" if night else "w_day-sunny-overcast.png"

    if code == 2:
        # Partly cloudy
        return "w_night-alt-partly-cloudy.png" if night else "w_day-cloudy.png"

    if code == 3:
        # Overcast
        return "w_night-alt-cloudy.png" if night else "w_cloudy.png"

    # ===== Fog =====
    if code in (45, 48):
        return "w_night-fog.png" if night else "w_day-fog.png"

    # ===== Drizzle =====
    if code in (51, 53, 55):
        # Drizzle: use sprinkle
        return "w_night-alt-sprinkle.png" if night else "w_day-sprinkle.png"

    # ===== Freezing drizzle =====
    if code in (56, 57):
        return "w_night-alt-sleet.png" if night else "w_day-sleet.png"

    # ===== Rain =====
    if code == 61:
        return "w_night-alt-rain.png" if night else "w_day-rain.png"
    if code == 63:
        return "w_night-alt-rain.png" if night else "w_rain.png"
    if code == 65:
        return "w_night-alt-rain.png" if night else "w_rain.png"

    # ===== Freezing rain =====
    if code in (66, 67):
        return "w_night-alt-sleet.png" if night else "w_day-sleet.png"

    # ===== Snow =====
    if code in (71, 73, 75):
        return "w_night-alt-snow.png" if night else "w_day-snow.png"
    if code == 77:
        return "w_snow.png"

    # ===== Rain showers =====
    if code in (80, 81, 82):
        # showers vary
        return "w_night-alt-showers.png" if night else "w_day-showers.png"

    # ===== Snow showers =====
    if code in (85, 86):
        return "w_night-alt-snow.png" if night else "w_day-snow.png"

    # ===== Thunderstorm =====
    if code == 95:
        return "w_night-alt-thunderstorm.png" if night else "w_day-thunderstorm.png"

    # ===== Thunderstorm with hail =====
    if code in (96, 99):
        return "w_night-alt-hail.png" if night else "w_day-hail.png"

    return "w_na.png"


# ===== Format helpers =====
def safe_float(v, default=None):
    try:
        return float(v)
    except Exception:
        return default


def fmt_price_usd(p: float) -> str:
    # Pixel screen: keep short, no decimals for big numbers
    if p >= 1000:
        return f"${p:,.0f}"
    return f"${p:.2f}"


def fmt_change(cp: float | None) -> str:
    if cp is None:
        return ""
    if isinstance(cp, float) and math.isnan(cp):
        return ""
    arrow = "↑" if cp >= 0 else "↓"   # use ASCII-ish arrows to avoid glyph issues
    sign = "+" if cp >= 0 else ""
    return f"{arrow}{sign}{cp:.1f}%"


# ===== Fetch data =====
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
            high_24h = safe_float(data.get("highPrice"), None)
            low_24h = safe_float(data.get("lowPrice"), None)
            out.append(
                {
                    "symbol": DISPLAY_MAP.get(sym, sym),
                    "price": price,
                    "change_percent_24h": cp,
                    "high_24h": high_24h,
                    "low_24h": low_24h,
                }
            )
        except Exception as e:
            logger.exception("Binance fetch failed for %s: %s", sym, e)

    priority = {"BTC": 0, "ETH": 1}
    out.sort(key=lambda x: priority.get(x["symbol"], 9))
    return out


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


# ===== Render: WEATHER (pixel card like your sample) =====
def render_weather_pixel(city: str, weather: dict) -> bytes:
    img2 = make_canvas_2x()
    d = ImageDraw.Draw(img2)

    f_title = load_pixel_font(14 * SCALE)
    f_city = load_pixel_font(22 * SCALE)
    f_temp = load_pixel_font(40 * SCALE)
    f_small = load_pixel_font(14 * SCALE)

    pad = 6 * SCALE
    px_rect(d, pad, pad, img2.size[0] - pad, img2.size[1] - pad, w=3, fill=0)

    # Title: short uppercase (pixel style)
    desc = weather_desc_vi(weather.get("code_now") or weather.get("code_day")).upper()
    # shorten common Vietnamese for compact look
    # ex: "NHIỀU MÂY" -> "NHIEU MAY" (avoid diacritics if font lacks glyphs)
    desc = desc.replace("TRỜI", "TROI").replace("MÂY", "MAY").replace("SƯƠNG", "SUONG")
    if len(desc) > 12:
        desc = desc[:12]

    title_y = pad + 6 * SCALE
    tw = d.textlength(desc, font=f_title)
    px_text(d, (img2.size[0] - tw) // 2, title_y, desc, f_title)

    px_hline(d, pad + 6 * SCALE, img2.size[0] - pad - 6 * SCALE, pad + 24 * SCALE, w=2, fill=0)

    # Icon - larger and more prominent
    icon = load_icon(weather_icon_name(weather.get("code_day")))
    if icon:
        # Center icon horizontally, position it prominently
        ix = (img2.size[0] - icon.size[0]) // 2
        iy = pad + 32 * SCALE  # Position after header line
        # Paste icon directly (icon is already 1-bit with black icon on white)
        # Use the icon itself as mask for proper pasting
        img2.paste(icon, (ix, iy), icon)

    # City
    city_u = city.upper()
    if len(city_u) > 12:
        city_u = city_u[:12]
    cy = pad + 74 * SCALE
    cw = d.textlength(city_u, font=f_city)
    px_text(d, (img2.size[0] - cw) // 2, cy, city_u, f_city)

    # underline
    px_hline(d, pad + 50 * SCALE, img2.size[0] - pad - 50 * SCALE, cy + 28 * SCALE, w=2, fill=0)

    # Temp
    temp_now = weather.get("temp_now")
    temp_str = f"{temp_now:.0f}°C" if isinstance(temp_now, (int, float)) else "--°C"
    ty = cy + 34 * SCALE
    tw2 = d.textlength(temp_str, font=f_temp)
    px_text(d, (img2.size[0] - tw2) // 2, ty, temp_str, f_temp)

    # dotted separator + Low/High
    sep_y = img2.size[1] - pad - 26 * SCALE
    px_dotted_hline(d, pad + 10 * SCALE, img2.size[0] - pad - 10 * SCALE, sep_y, step=4, fill=0)

    tmin = weather.get("tmin")
    tmax = weather.get("tmax")
    low = f"L: {tmin:.0f}°C" if isinstance(tmin, (int, float)) else "L: --°C"
    high = f"H: {tmax:.0f}°C" if isinstance(tmax, (int, float)) else "H: --°C"

    px_text(d, pad + 20 * SCALE, sep_y + 6 * SCALE, low, f_small)
    hw = d.textlength(high, font=f_small)
    px_text(d, img2.size[0] - pad - 20 * SCALE - hw, sep_y + 6 * SCALE, high, f_small)

    # Add small VN time top-right (optional, subtle)
    vn_tz = ZoneInfo("Asia/Ho_Chi_Minh")
    now_vn = datetime.now(vn_tz).strftime("%d/%m %H:%M")
    wt = d.textlength(now_vn, font=f_small)
    px_text(d, img2.size[0] - pad - wt, pad + 6 * SCALE, now_vn, f_small)

    out = img2.resize((W, H), Image.NEAREST)
    buf = io.BytesIO()
    out.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


# ===== Render: CRYPTO (SwiftUI-style layout) =====
def render_crypto_pixel(prices: list[dict]) -> bytes:
    img2 = make_canvas_2x()
    d = ImageDraw.Draw(img2)

    # Use default font with different sizes (matching SwiftUI)
    # Font sizes match SwiftUI: symbol=50, price=30, currency=15, price_label=11, change=13
    f_symbol = load_default_font(50 * SCALE)
    f_price = load_default_font(22 * SCALE)
    f_currency = load_default_font(15 * SCALE)
    f_price_label = load_default_font(11 * SCALE)
    f_change = load_default_font(13 * SCALE)

    pad = 10 * SCALE  # Padding 10px like SwiftUI
    midx = img2.size[0] // 2
    
    # Divider line in the middle (opacity 0.35 = lighter black, use dotted pattern)
    divider_y_start = pad
    divider_y_end = img2.size[1] - pad
    # Draw divider with lighter color (use dotted pattern for opacity effect)
    for y in range(divider_y_start, divider_y_end, 3):
        d.point((midx, y), fill=0)

    def coin(sym: str):
        return next((x for x in prices if x["symbol"] == sym), None)

    def draw_coin(block_x: int, sym: str):
        p = coin(sym)
        if not p:
            # Draw symbol
            d.text((block_x, pad), sym, font=f_symbol, fill=0)
            # Draw PRICE label
            d.text((block_x, pad + 60 * SCALE), "PRICE:", font=f_price_label, fill=0)
            # Draw placeholder
            d.text((block_x, pad + 80 * SCALE), "--", font=f_price, fill=0)
            return

        price_val = p["price"]
        # Format price: remove commas, keep 2 decimals
        price_str = f"{price_val:.2f}"
        chg = p.get("change_percent_24h", 0.0)
        
        # Calculate positions (matching SwiftUI spacing: 6px between elements)
        y_symbol = pad
        y_price_label = pad + 60 * SCALE  # Spacing 60px from top
        y_price = pad + 80 * SCALE  # Spacing 80px from top
        
        # Calculate column width
        column_width = (midx - pad) if sym == "BTC" else (img2.size[0] - midx - pad)
        
        # 1. Draw SYMBOL (large, heavy, centered in column)
        symbol_text = sym
        symbol_width = d.textlength(symbol_text, font=f_symbol)
        symbol_x = block_x + (column_width - symbol_width) // 2
        d.text((symbol_x, y_symbol), symbol_text, font=f_symbol, fill=0)
        
        # 2. Draw PRICE: label + ChangeBadge on same line
        price_label_text = "PRICE:"
        price_label_width = d.textlength(price_label_text, font=f_price_label)
        d.text((block_x, y_price_label), price_label_text, font=f_price_label, fill=0)
        
        # ChangeBadge: percent + arrow (right-aligned in column)
        change_text = f"{chg:+.1f}%"
        arrow = "↑" if chg >= 0 else "↓"
        change_badge_text = f"{change_text} {arrow}"
        change_width = d.textlength(change_badge_text, font=f_change)
        change_x = block_x + column_width - change_width
        d.text((change_x, y_price_label), change_badge_text, font=f_change, fill=0)
        
        # 3. Draw big price + currency (on same line, aligned to baseline)
        price_width = d.textlength(price_str, font=f_price)
        currency_text = "$"
        currency_width = d.textlength(currency_text, font=f_currency)
        
        # Price and currency on same line
        d.text((block_x, y_price), price_str, font=f_price, fill=0)
        # Currency slightly smaller and positioned next to price
        d.text((block_x + price_width + 6 * SCALE, y_price + 5 * SCALE), currency_text, font=f_currency, fill=0)

    # Draw left column (BTC)
    left_x = pad
    draw_coin(left_x, "BTC")
    
    # Draw right column (ETH)
    right_x = midx + pad
    draw_coin(right_x, "ETH")

    out = img2.resize((W, H), Image.NEAREST)
    buf = io.BytesIO()
    out.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


# ===== Dot API call (Image) =====
async def send_to_dot_image_api(client: httpx.AsyncClient, api_key: str, device_id: str, png_bytes: bytes) -> None:
    url = DOT_IMAGE_API_V2.format(device_id=device_id)
    b64 = base64.b64encode(png_bytes).decode("ascii")

    # Pixel style: NO dither => crisp
    payload = {"image": b64, "border": 0, "ditherType": "NONE"}
    logger.info(b64)
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept-Encoding": "identity",
    }

    r = await client.post(url, json=payload, headers=headers)
    if r.status_code // 100 != 2:
        logger.error("Dot Image API error %s: %s", r.status_code, r.text)
        raise RuntimeError(f"Dot Image API status {r.status_code}")

    logger.info("Dot Image API ok %s", r.status_code)


# ===== Main loop: alternate every minute =====
async def ticker_loop() -> None:
    api_key = os.environ["DOT_API_KEY"]
    device_id = os.environ["DOT_DEVICE_ID"]

    # Weather location
    city = os.getenv("WEATHER_CITY", "Ha Noi")
    lat = float(os.getenv("WEATHER_LAT", "21.0285"))
    lon = float(os.getenv("WEATHER_LON", "105.8542"))
    tz = os.getenv("WEATHER_TZ", "Asia/Ho_Chi_Minh")

    logger.info("Starting pixel dashboard (alternate each 60s). City=%s lat=%s lon=%s", city, lat, lon)

    timeout = httpx.Timeout(30.0)
    default_headers = {"User-Agent": "dot-pixel-dashboard/0.1", "Accept-Encoding": "identity"}

    show_crypto = True  # start with crypto

    async with httpx.AsyncClient(timeout=timeout, headers=default_headers, follow_redirects=True) as client:
        while True:
            try:
                prices = await fetch_prices_binance(client)
                png = render_crypto_pixel(prices)
                logger.info("Rendered: CRYPTO")
                # if show_crypto:
                #     prices = await fetch_prices_binance(client)
                #     png = render_crypto_pixel(prices)
                #     logger.info("Rendered: CRYPTO")
                # else:
                #     weather = await fetch_weather_today(client, lat, lon, tz)
                #     png = render_weather_pixel(city, weather)
                #     logger.info("Rendered: WEATHER")

                await send_to_dot_image_api(client, api_key, device_id, png)
                # show_crypto = not show_crypto

            except Exception as e:
                logger.exception("Loop error: %s", e)

            await asyncio.sleep(120)


# ===== FastAPI (healthcheck for Koyeb) =====
app = FastAPI()

@app.on_event("startup")
async def on_startup() -> None:
    # quick sanity log
    logger.info("BASE_DIR=%s", BASE_DIR)
    logger.info("FONT_DIR=%s exists=%s", FONT_DIR, os.path.exists(FONT_DIR))
    logger.info("ASSET_DIR=%s exists=%s", ASSET_DIR, os.path.exists(ASSET_DIR))
    asyncio.create_task(ticker_loop())

@app.get("/health")
async def health():
    return {"ok": True}
