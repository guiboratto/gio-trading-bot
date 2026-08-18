import asyncio
import websockets


async def try_url(url):
    try:
        async with websockets.connect(url, ping_interval=30) as ws:
            await ws.send('{"method":"SUBSCRIBE","params":["btcusdt@aggTrade"],"id":1}')
            print(f"{url}: connected")
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=5)
                print(f"  first msg: {msg[:200]}")
            except asyncio.TimeoutError:
                print(f"  connected but no msg in 5s")
    except Exception as e:
        print(f"{url}: FAIL {type(e).__name__}: {e}")


async def main():
    urls = [
        "wss://stream.testnet.binance.vision/stream",
        "wss://stream.binance.com:9443/stream",
        "wss://stream.binance.com:443/stream",
        "wss://demo-stream.binance.com:9443/stream",
    ]
    for u in urls:
        await try_url(u)


asyncio.run(main())