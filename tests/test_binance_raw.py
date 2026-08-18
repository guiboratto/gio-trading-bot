import asyncio
from gio_trading_bot.binance_ws import BinanceWS

async def main():
    ws = BinanceWS(mode="testnet")
    received = []

    async def cb(payload):
        received.append(payload)
        print(f"RAW: {payload}")
        if len(received) >= 3:
            ws.running = False

    # raw aggTrade - subscribe to single symbol
    await ws.connect()
    await ws.subscribe(["btcusdt@aggTrade"])
    # patch dispatcher to print raw
    ws2 = ws

    async def listen_print():
        while ws.running:
            try:
                msg = await ws.ws.recv()
                print(f"GOT: {msg[:300]}")
            except Exception as e:
                print(f"err: {e}")
                break

    try:
        await asyncio.wait_for(listen_print(), timeout=6)
    except asyncio.TimeoutError:
        pass
    await ws.close()
    print(f"received {len(received)} parsed")

asyncio.run(main())