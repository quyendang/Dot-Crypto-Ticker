import os
import asyncio
import logging
from datetime import datetime

import httpx
from fastapi import FastAPI
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger("dot-crypto-ticker")

BINANCE_24HR = "https://api.binance.com/api/v3/ticker/24hr"

# NEW Dot API (v2) - deviceId nằm trên path
DOT_TEXT_API_V2 = "https://dot.mindreset.tech/api/authV2/open/device/{device_id}/text"  # :contentReference[oaicite:1]{index=1}

SYMBOLS = ["BTCUSDT", "ETHUSDT", "USDCUSDT"]
DISPLAY_MAP = {"BTCUSDT": "BTC", "ETHUSDT": "ETH", "USDCUSDT": "USDT"}  # giữ đúng như Rust của bạn


def format_price(price: float) -> str:
    return f"${price:,.2f}" if price >= 1000.0 else f"${price:.2f}"


def format_change(change_percent: float) -> str:
    arrow = "↗" if change_percent >= 0 else "↘"
    sign = "+" if change_percent >= 0 else ""
    return f"{arrow}{sign}{change_percent:.1f}%"


def create_display_message(prices: list[dict]) -> str:
    priority = {"BTC": 0, "ETH": 1, "USDT": 2}
    prices_sorted = sorted(prices, key=lambda p: priority.get(p["symbol"], 3))[:3]

    lines = []
    for p in prices_sorted:
        price_str = format_price(p["price"])
        cp = p.get("change_percent_24h")
        if cp is None:
            lines.append(f'{p["symbol"]} {price_str}')
        else:
            lines.append(f'{p["symbol"]} {price_str} {format_change(cp)}')
    return "\n".join(lines)


async def fetch_crypto_prices_binance(client: httpx.AsyncClient) -> list[dict]:
    results = []
    for sym in SYMBOLS:
        try:
            r = await client.get(BINANCE_24HR, params={"symbol": sym})
            if r.status_code // 100 != 2:
                logger.error("Binance API error %s for %s: %s", r.status_code, sym, r.text)
                continue

            data = r.json()
            price = float(data.get("lastPrice", "0") or "0")
            change_percent = data.get("priceChangePercent")
            change_percent_f = float(change_percent) if change_percent is not None else None

            display = DISPLAY_MAP.get(sym, sym)
            logger.info("Fetched %s: %s", display, price)

            results.append(
                {"symbol": display, "price": price, "change_percent_24h": change_percent_f}
            )
        except Exception as e:
            logger.exception("Failed Binance fetch/parse for %s: %s", sym, e)

    return results


async def send_to_dot_text_api_v2(
    client: httpx.AsyncClient,
    api_key: str,
    device_id: str,
    title: str,
    message: str,
    signature: str,
) -> None:
    url = DOT_TEXT_API_V2.format(device_id=device_id)

    # v2: deviceId nằm trên URL; body chỉ cần refreshNow/title/message/signature...
    payload = {
        "refreshNow": True,
        "title": title,
        "message": message,
        "signature": signature,
        "icon": None,
        "link": None,
    }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        # Tắt nén để tránh lỗi zlib/br/gzip mismatch
        "Accept-Encoding": "identity",
    }

    r = await client.post(url, json=payload, headers=headers)
    # đọc text an toàn (không đụng decompress lạ vì đã identity)
    body = r.text

    if r.status_code // 100 != 2:
        logger.error("Dot API error %s: %s", r.status_code, body)
        raise RuntimeError(f"Dot API status {r.status_code}")

    logger.info("Dot API ok %s: %s", r.status_code, body)


async def ticker_loop() -> None:
    api_key = os.environ["DOT_API_KEY"]
    device_id = os.environ["DOT_DEVICE_ID"]
    title = os.getenv("DOT_TITLE", "Crypto Prices")
    interval_secs = max(int(os.getenv("INTERVAL_SECS", "600")), 2)

    logger.info("Starting crypto price ticker (interval=%ss)", interval_secs)

    timeout = httpx.Timeout(30.0)
    default_headers = {
        "User-Agent": "dot-crypto-ticker/0.2-python",
        # cũng tắt nén ở mức client cho chắc
        "Accept-Encoding": "identity",
    }

    async with httpx.AsyncClient(
        timeout=timeout,
        headers=default_headers,
        follow_redirects=True,
    ) as client:
        while True:
            try:
                data = await fetch_crypto_prices_binance(client)
                if data:
                    message = create_display_message(data)
                    signature = f"Updated at {datetime.now().strftime('%H:%M')}"
                    await send_to_dot_text_api_v2(client, api_key, device_id, title, message, signature)
                    logger.info("Prices updated: %s", [d["symbol"] for d in data])
                else:
                    logger.error("No price data received")
            except Exception as e:
                logger.exception("Loop error: %s", e)

            await asyncio.sleep(interval_secs)


app = FastAPI()


@app.on_event("startup")
async def on_startup() -> None:
    asyncio.create_task(ticker_loop())


@app.get("/health")
async def health():
    return {"ok": True}
