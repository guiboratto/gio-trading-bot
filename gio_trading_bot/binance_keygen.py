"""Generate RSA-2048 keypair for Binance API.

Usage:
  python -m gio_trading_bot.binance_keygen <user_id>

Outputs:
  ~/.gio_binance_keys/binance_<user_id>_<ts>.pem  - keep secret, chmod 600
  ~/.gio_binance_keys/binance_<user_id>_<ts>.pub.pem - upload to Binance API key
"""
import sys
import os
from pathlib import Path


def main():
    if len(sys.argv) < 2:
        print("Usage: python -m gio_trading_bot.binance_keygen <user_id>")
        sys.exit(1)
    user_id = sys.argv[1]
    from . import binance_client
    priv_path, pub_pem = binance_client.generate_rsa_keypair(user_id)
    pub_path = priv_path.replace(".pem", ".pub.pem")
    Path(pub_path).write_text(pub_pem)
    print(f"Private key (keep secret): {priv_path}")
    print(f"Public key (upload to Binance): {pub_path}")
    print()
    print("Next steps:")
    print("1. Upload the public key to Binance API key creation page.")
    print("2. Send /settings -> Connect Binance -> paste API key.")
    print("3. Bot uses the matching private key for signed requests.")


if __name__ == "__main__":
    main()