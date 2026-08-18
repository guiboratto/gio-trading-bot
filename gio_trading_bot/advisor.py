"""LLM-driven analysis via OpenRouter (free)."""
import os
import json
import httpx

SYSTEM = (
    "You are GIO Trading Advisor. Professional trading assistant.\n"
    "Respond ONLY structured:\n\n"
    "ANALYSIS: <asset>\n\n"
    "Current state:\n<2-3 sentences with numbers>\n\n"
    "Recommendation: <BUY / SELL / HOLD>\n"
    "Entry: <price or range>\n"
    "Stop-loss: <price or %>\n"
    "Take-profit: <price or %>\n"
    "R/R: <ratio>\n\n"
    "Risks:\n<bullet list 2-3 items>\n\n"
    "Confirmation:\n<what must happen for the recommendation to hold>\n\n"
    "Concise. No filler. No disclaimers.\n"
    "Reply in user language (UA/RU/EN)."
)


class Advisor:
    def __init__(self):
        self.api_key = os.environ["OPENROUTER_API_KEY"]
        self.base = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
        self.model = os.environ.get(
            "OPENROUTER_MODEL", "meta-llama/llama-3.3-70b-instruct:free"
        )

    async def analyze(self, ctx: dict) -> str:
        prompt = (
            f"Request:\n"
            f"Strategy: {ctx.get('strategy')}\n"
            f"Asset: {ctx.get('asset')}\n"
            f"Horizon: {ctx.get('horizon')}\n"
            f"Entry: {ctx.get('entry')}\n"
            f"Risk: {ctx.get('risk')}\n\n"
            f"CoinGecko: {json.dumps(ctx.get('coingecko'), ensure_ascii=False)[:2000]}\n"
            f"DeBank: {json.dumps(ctx.get('debank'), ensure_ascii=False)[:2000]}\n"
            f"Archam: {json.dumps(ctx.get('archam'), ensure_ascii=False)[:2000]}\n\n"
            f"{'Clarifications: ' + json.dumps(ctx.get('followups'), ensure_ascii=False) if ctx.get('is_followup') else ''}\n\n"
            f"Make the analysis."
        )
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(
                f"{self.base}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": SYSTEM},
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0.4,
                },
            )
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"]