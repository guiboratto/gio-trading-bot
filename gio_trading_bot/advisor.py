"""LLM advisor via OpenRouter."""
import os
import httpx

SYSTEM = (
    "You are GIO, a concise crypto trading analyst. "
    "Reply structured: state, recommendation (BUY/SELL/HOLD), "
    "entry, stop-loss, take-profit, R/R ratio, key risks. "
    "No filler. No disclaimers. No 'consult an advisor'. "
    "Match user language."
)


class Advisor:
    def __init__(self):
        self.api_key = os.environ.get("OPENROUTER_API_KEY", "")
        self.base = os.environ.get("OPENROUTER_BASE_URL",
                                   "https://openrouter.ai/api/v1")
        self.model = os.environ.get("OPENROUTER_MODEL",
                                    "meta-llama/llama-3.3-70b-instruct:free")

    async def simple(self, user_prompt: str) -> str:
        if not self.api_key:
            return ("[OpenRouter key not set - canned response]\n\n"
                    "Without live AI, here's a basic framework:\n"
                    "1. Wait for clear setup (breakout, retest of support)\n"
                    "2. Risk 1-2% of account per trade\n"
                    "3. Stop below recent swing low\n"
                    "4. Take-profit 2-3x the stop distance\n\n"
                    "Add OPENROUTER_API_KEY to .env for AI analysis.")
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(
                f"{self.base}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": SYSTEM},
                        {"role": "user", "content": user_prompt},
                    ],
                    "temperature": 0.3,
                },
            )
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"]

    async def analyze(self, ctx: dict) -> str:
        return await self.simple(str(ctx))