import asyncio
from gio_trading_bot.binance_ws import BinanceWS


async def cb(payload):
    sym = payload.get("s")
    price = float(payload.get("p", 0))
    qty = float(payload.get("q", 0))
    print(f"TRADE: {sym} {price} x {qty} = ${price*qty:,.0f} m={payload.get('m')}")


async def main():
    ws = BinanceWS(mode="testnet")
    ws.on("btcusdt@aggTrade", cb)
    await ws.connect()
    await ws.subscribe(["btcusdt@aggTrade"])
    print("connected, waiting 8s for trades...")
    try:
        await asyncio.wait_for(ws.listen(), timeout=8)
    except asyncio.TimeoutError:
        pass
    await ws.close()
    print("done")


asyncio.run(main())