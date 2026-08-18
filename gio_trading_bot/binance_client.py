"""Binance client - signed REST + WebSocket.

Supports:
  - HMAC-SHA256 (api_key + secret)
  - RSA-2048 asymmetric (api_key + private key file) - 2024+ recommended
  - Testnet (api_testnet.binance.com) for safe local dev

Used by bot.py for /connect_binance + /trade if user attached keys.
"""
import os
import time
import json
import hmac
import hashlib
import base64
from urllib.parse import urlencode
from pathlib import Path
import httpx

BINANCE_KEYS_DIR = Path.home() / ".gio_binance_keys"
BINANCE_KEYS_DIR.mkdir(parents=True, exist_ok=True)


class BinanceClient:
    def __init__(self, api_key: str, secret_or_path: str, testnet: bool = True,
                 is_rsa: bool = False):
        self.api_key = api_key
        self.is_rsa = is_rsa
        self.testnet = testnet
        if testnet:
            self.base = "https://testnet.binance.vision"
        else:
            self.base = "https://api.binance.com"
        if is_rsa:
            # secret_or_path is path to PEM private key file
            self.private_key = self._load_pem(secret_or_path)
        else:
            self.secret = secret_or_path.encode() if isinstance(secret_or_path, str) else secret_or_path

    def _load_pem(self, path):
        from cryptography.hazmat.primitives import serialization
        with open(path, "rb") as f:
            return serialization.load_pem_private_key(f.read(), password=None)

    def _sign(self, query: str) -> str:
        if self.is_rsa:
            from cryptography.hazmat.primitives.asymmetric import padding
            sig = self.private_key.sign(
                query.encode(),
                padding.PKCS1v15(),
                hashlib.sha256(),
            )
            return _b64url(sig)
        else:
            return hmac.new(self.secret, query.encode(), hashlib.sha256).hexdigest()

    def _request(self, method: str, path: str, params: dict | None = None,
                 signed: bool = True) -> dict:
        params = dict(params or {})
        if signed:
            params["timestamp"] = int(time.time() * 1000)
            qs = urlencode(params)
            params["signature"] = self._sign(qs)
        headers = {"X-MBX-APIKEY": self.api_key}
        url = f"{self.base}{path}"
        try:
            with httpx.Client(timeout=15) as client:
                r = client.request(method, url, params=params, headers=headers)
                if r.status_code == 200:
                    return r.json()
                return {"error": f"HTTP {r.status_code}", "body": r.text[:300]}
        except Exception as e:
            return {"error": str(e)}

    # ---------- account / market ----------

    def ping(self) -> dict:
        return self._request("GET", "/api/v3/ping", signed=False)

    def server_time(self) -> dict:
        return self._request("GET", "/api/v3/time", signed=False)

    def account(self) -> dict:
        return self._request("GET", "/api/v3/account")

    def ticker(self, symbol: str) -> dict:
        return self._request("GET", "/api/v3/ticker/24hr",
                             {"symbol": symbol}, signed=False)

    def klines(self, symbol: str, interval: str = "1h", limit: int = 100) -> dict:
        return self._request("GET", "/api/v3/klines",
                             {"symbol": symbol, "interval": interval, "limit": limit},
                             signed=False)

    def price(self, symbol: str) -> dict:
        return self._request("GET", "/api/v3/ticker/price",
                             {"symbol": symbol}, signed=False)

    # ---------- spot trading ----------

    def order_test(self, symbol: str, side: str, qty: float, price: float = None,
                   type_: str = "MARKET") -> dict:
        """Validate order params without placing. Use first."""
        params = {"symbol": symbol, "side": side.upper(),
                  "type": type_.upper(), "quantity": qty}
        if type_.upper() == "LIMIT" and price:
            params["price"] = price
            params["timeInForce"] = "GTC"
        return self._request("POST", "/api/v3/order/test", params)

    def order(self, symbol: str, side: str, qty: float, price: float = None,
              type_: str = "MARKET") -> dict:
        params = {"symbol": symbol, "side": side.upper(),
                  "type": type_.upper(), "quantity": qty}
        if type_.upper() == "LIMIT" and price:
            params["price"] = price
            params["timeInForce"] = "GTC"
        return self._request("POST", "/api/v3/order", params)

    def open_orders(self, symbol: str = None) -> dict:
        params = {"symbol": symbol} if symbol else {}
        return self._request("GET", "/api/v3/openOrders", params)

    def cancel_order(self, symbol: str, order_id: int) -> dict:
        return self._request("DELETE", "/api/v3/order",
                             {"symbol": symbol, "orderId": order_id})


# ============ asymmetric key generator ============

def generate_rsa_keypair(user_id: int) -> tuple[str, str]:
    """Generate RSA-2048 keypair for user. Returns (private_pem_path, public_pem_str).

    User must upload public_pem to Binance via API key creation, then keep private_pem file.
    """
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives import serialization

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    pub_pem = key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )

    fname = f"binance_{user_id}_{int(time.time())}.pem"
    path = BINANCE_KEYS_DIR / fname
    path.write_bytes(priv_pem)
    os.chmod(path, 0o600)

    return str(path), pub_pem.decode()


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


# ============ safe-init from user_keys table ============

def make_client_from_db(user_id: int, prefer: str = "rsa") -> BinanceClient | None:
    """Build BinanceClient from db.user_keys. Returns None if not configured."""
    from . import db
    api_key = db.get_api_key(user_id, "binance_key")
    if not api_key:
        return None
    if prefer == "rsa":
        pem_path = db.get_api_key(user_id, "binance_pem_path")
        if pem_path and Path(pem_path).exists():
            return BinanceClient(api_key, pem_path, testnet=True, is_rsa=True)
    secret_hash = db.get_api_key(user_id, "binance_secret_hash")
    if secret_hash:
        # we stored sha256(secret) only - we need actual secret to sign
        # so HMAC not available here; user must keep secret separately
        return None
    return None