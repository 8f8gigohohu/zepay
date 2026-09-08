"""Solana / Phantom wallet integration (§9, §52).

SECURITY MODEL — non-negotiable:
  * ZEPAY NEVER requests, receives, derives or stores a seed phrase, private
    key or recovery phrase. There is no code path here that could accept one.
  * The backend only ever sees the wallet's PUBLIC address.
  * Connection = the user's Phantom extension signs a challenge message; the
    backend verifies the ed25519 signature against the public address.
  * Swaps/transactions: the backend asks Jupiter for a REAL quote and an
    UNSIGNED serialized transaction; Phantom signs it client-side; only the
    SIGNED transaction is broadcast (relayed by the backend via sendTransaction).

Market data here is real: Solana JSON-RPC (balances, blockhash, health) plus
Jupiter price/quote APIs. When unreachable → UNAVAILABLE, never estimated.
"""

from __future__ import annotations

import base64
import json
import logging
import secrets as pysecrets
import urllib.request
from typing import Any

from zepay.core.errors import ExchangeError
from zepay.core.util import safe_float, utcnow_iso
from zepay.exchanges.base import Capabilities, ExchangeAdapter
from zepay.exchanges.http import http_error_str

log = logging.getLogger("zepay.wallets.solana")

SOLANA_RPC = "https://api.mainnet-beta.solana.com"
JUPITER_PRICE = "https://lite-api.jup.ag/price/v3"
JUPITER_QUOTE = "https://lite-api.jup.ag/swap/v1/quote"
JUPITER_SWAP = "https://lite-api.jup.ag/swap/v1/swap"

SOL_MINT = "So11111111111111111111111111111111111111112"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
USDT_MINT = "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"

_B58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def b58decode(s: str) -> bytes:
    n = 0
    for ch in s:
        idx = _B58_ALPHABET.find(ch)
        if idx < 0:
            raise ValueError(f"invalid base58 character: {ch!r}")
        n = n * 58 + idx
    full = n.to_bytes((n.bit_length() + 7) // 8, "big") if n else b""
    pad = len(s) - len(s.lstrip("1"))
    return b"\x00" * pad + full


def is_valid_pubkey(addr: str) -> bool:
    try:
        raw = b58decode(addr)
        return len(raw) == 32
    except Exception:
        return False


class SolanaWalletAdapter(ExchangeAdapter):
    """Read-only chain data + Phantom-delegated signing. Not a trading venue
    in the order-book sense: capabilities declare what it really does."""

    id = "solana"
    name = "Solana (Phantom wallet + Jupiter)"

    def __init__(self, secrets, config, audit_fn, rpc_url: str = SOLANA_RPC):
        super().__init__()
        self.secrets = secrets
        self.config = config
        self.audit = audit_fn
        self.rpc_url = rpc_url
        self._connected_address: str | None = None
        self._challenge: dict = {}

    def capabilities(self) -> Capabilities:
        return Capabilities(
            market_data=True,
            websocket=False,
            derivatives_context=False,
            signed_account=True,  # read balances of the connected public address
            trading=True,  # via Phantom-signed Jupiter swap transactions
            order_types=["DEX_SWAP"],
            time_in_force=["IOC"],
            withdrawal_supported=False,
        )

    # ---- JSON-RPC plumbing ----
    def _rpc(self, method: str, params: list, timeout: float = 10):
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
        req = urllib.request.Request(
            self.rpc_url,
            data=body,
            method="POST",
            headers={"Content-Type": "application/json", "User-Agent": "ZEPAY/3"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                out = json.loads(r.read().decode("utf-8", "replace") or "{}")
            if "error" in out:
                raise ExchangeError(f"solana rpc error: {out['error']}", self.id, status="DEGRADED")
            self._ok()
            return out.get("result")
        except ExchangeError:
            raise
        except Exception as e:
            self._fail(http_error_str(e), "UNREACHABLE")
            raise ExchangeError(
                f"solana rpc failed: {http_error_str(e)}",
                self.id,
                status="UNREACHABLE",
                retryable=True,
            ) from e

    def _jup_get(self, url: str, timeout: float = 10):
        req = urllib.request.Request(url, headers={"User-Agent": "ZEPAY/3"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                out = json.loads(r.read().decode("utf-8", "replace") or "{}")
            self._ok()
            return out
        except Exception as e:
            self._fail(f"jupiter: {http_error_str(e)}", "UNREACHABLE")
            raise ExchangeError(
                f"jupiter request failed: {http_error_str(e)}",
                self.id,
                status="UNREACHABLE",
                retryable=True,
            ) from e

    def _jup_post(self, url: str, payload: dict, timeout: float = 15):
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode(),
            method="POST",
            headers={"Content-Type": "application/json", "User-Agent": "ZEPAY/3"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                out = json.loads(r.read().decode("utf-8", "replace") or "{}")
            self._ok()
            return out
        except Exception as e:
            self._fail(f"jupiter: {http_error_str(e)}", "UNREACHABLE")
            raise ExchangeError(
                f"jupiter request failed: {http_error_str(e)}",
                self.id,
                status="UNREACHABLE",
                retryable=True,
            ) from e

    # ---- health / metadata ----
    def ping(self):
        return self._rpc("getHealth", [])

    def exchange_info(self, symbols=None):
        """DEX 'instruments' are token mints priced via Jupiter."""
        return {"venue": self.id, "kind": "DEX", "mints": [SOL_MINT, USDC_MINT, USDT_MINT]}

    def ticker_24h(self, symbol: str | None = None):
        raise ExchangeError(
            "Solana DEX tickers come from Jupiter prices; use token_prices()",
            self.id,
            status="NOT_CONFIGURED",
        )

    def klines(self, symbol: str, interval: str = "1h", limit: int = 200):
        raise ExchangeError(
            "candle history for DEX mints is NOT_CONFIGURED in this build "
            "(no bundled OHLCV source). Shows UNAVAILABLE — never faked.",
            self.id,
            status="NOT_CONFIGURED",
        )

    def depth(self, symbol: str, limit: int = 20):
        raise ExchangeError(
            "DEX depth is NOT_CONFIGURED (Jupiter quote priceImpact is "
            "exposed instead). Shows UNAVAILABLE — never faked.",
            self.id,
            status="NOT_CONFIGURED",
        )

    def token_prices(self, mints: list[str]) -> dict:
        """Real Jupiter USD prices for token mints. {mint: {usdPrice, ...}}."""
        ids = ",".join(mints)
        data = self._jup_get(f"{JUPITER_PRICE}?ids={ids}")
        return data or {}

    # ---- wallet connection (§52: public address only) ----
    def connected_address(self) -> str | None:
        return self._connected_address

    def start_connection(self, address: str) -> dict:
        """Frontend calls this after Phantom `connect()` returns the public
        key. We store ONLY the public address and issue a sign-in challenge."""
        if not is_valid_pubkey(address):
            raise ValueError("invalid Solana public address (must be 32-byte base58)")
        challenge = pysecrets.token_hex(16)
        self._challenge = {
            "address": address,
            "challenge": challenge,
            "issued_at": utcnow_iso(),
            "verified": False,
        }
        self.audit(
            "wallet",
            "connection_challenge_issued",
            {"address_tail": address[-6:], "challenge_len": len(challenge)},
        )
        return {
            "address": address,
            "challenge": challenge,
            "note": "Sign this message with Phantom. ZEPAY never asks for a "
            "private key or seed phrase.",
        }

    def verify_signature(self, address: str, signature_b64: str) -> dict:
        """Verify Phantom's ed25519 signature over the challenge. Uses the
        cryptography lib if present; otherwise the challenge stays pending and
        the UI shows NOT_VERIFIED (never silently trusted)."""
        ch = self._challenge
        if not ch or ch.get("address") != address:
            return {"verified": False, "error": "no pending challenge for this address"}
        try:
            from cryptography.exceptions import InvalidSignature
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

            pub = Ed25519PublicKey.from_public_bytes(b58decode(address))
            pub.verify(base64.b64decode(signature_b64), ch["challenge"].encode())
        except ImportError:
            return {
                "verified": False,
                "error": "signature verification library unavailable — connection "
                "stays NOT_VERIFIED (honest)",
            }
        except (InvalidSignature, Exception) as e:
            return {"verified": False, "error": f"signature invalid: {e}"}
        ch["verified"] = True
        self._connected_address = address
        self.audit("wallet", "connection_verified", {"address_tail": address[-6:]})
        return {"verified": True, "address": address}

    def disconnect(self) -> None:
        self._connected_address = None
        self._challenge = {}
        self.audit("wallet", "disconnected", {})

    # ---- balances (read-only, real chain state) ----
    def balances(self) -> dict:
        addr = self._connected_address
        if not addr:
            raise ExchangeError(
                "wallet not connected — connect Phantom first", self.id, status="NOT_CONFIGURED"
            )
        lamports = self._rpc("getBalance", [addr])
        sol = safe_float(lamports) / 1e9
        tokens = []
        try:
            resp = self._rpc(
                "getTokenAccountsByOwner",
                [
                    addr,
                    {"programId": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"},
                    {"encoding": "jsonParsed"},
                ],
            )
            for acct in resp or []:
                info = acct.get("account", {}).get("data", {}).get("parsed", {}).get("info", {})
                amt = safe_float(info.get("tokenAmount", {}).get("uiAmountString"))
                if amt > 0:
                    tokens.append(
                        {
                            "mint": info.get("mint"),
                            "amount": amt,
                            "decimals": info.get("tokenAmount", {}).get("decimals"),
                        }
                    )
        except Exception as e:
            log.warning("token accounts fetch failed: %s", e)
        out: dict[str, Any] = {"address": addr, "sol": sol, "tokens": tokens, "ts": utcnow_iso()}
        # real USD values via Jupiter
        try:
            mints = [SOL_MINT] + [t["mint"] for t in tokens if t.get("mint")]
            prices = self.token_prices(mints)
            out["prices_usd"] = {
                m: safe_float((prices.get(m) or {}).get("usdPrice")) for m in mints
            }
            out["sol_usd"] = out["prices_usd"].get(SOL_MINT, 0)
            out["total_usd"] = sol * out.get("sol_usd", 0) + sum(
                t["amount"] * out["prices_usd"].get(t.get("mint"), 0) for t in tokens
            )
        except Exception as e:
            out["prices_error"] = http_error_str(e)
        return out

    # ---- swap flow (Phantom signs; we never hold keys) ----
    def quote_swap(
        self, input_mint: str, output_mint: str, amount: int, slippage_bps: int = 50
    ) -> dict:
        """amount = smallest unit (lamports / token base units)."""
        return self._jup_get(
            f"{JUPITER_QUOTE}?inputMint={input_mint}&outputMint={output_mint}"
            f"&amount={amount}&slippageBps={slippage_bps}"
        )

    def prepare_swap_transaction(self, quote: dict, user_address: str) -> dict:
        """Returns the UNSIGNED serialized transaction (base64) for Phantom to
        sign. Jupiter builds it; ZEPAY relays it; the key never touches us."""
        if user_address != self._connected_address:
            raise ExchangeError(
                "wallet not connected for this address", self.id, status="NOT_CONFIGURED"
            )
        out = self._jup_post(JUPITER_SWAP, {"quoteResponse": quote, "userPublicKey": user_address})
        tx_b64 = out.get("swapTransaction")
        if not tx_b64:
            raise ExchangeError(
                f"jupiter returned no transaction: {out}", self.id, status="DEGRADED"
            )
        self.audit(
            "wallet",
            "swap_tx_prepared",
            {
                "in": quote.get("inputMint", "")[:6],
                "out": quote.get("outputMint", "")[:6],
                "amount": quote.get("inAmount"),
            },
        )
        return {
            "unsigned_tx_b64": tx_b64,
            "note": "Sign with Phantom. ZEPAY cannot sign — it never has your key.",
        }

    def broadcast_signed_transaction(self, signed_tx_b64: str) -> dict:
        """Relay the Phantom-SIGNED transaction to the chain."""
        sig = self._rpc(
            "sendTransaction",
            [signed_tx_b64, {"encoding": "base64", "skipPreflight": False}],
            timeout=20,
        )
        self.audit("wallet", "swap_tx_broadcast", {"signature_tail": str(sig)[-8:]})
        return {"signature": sig}

    # ---- unused venue surface (honest refusals) ----
    def account(self, timeout: float = 12):
        return self.balances()

    def test_connection(self) -> dict:
        out: dict[str, Any] = {
            "connected": bool(self._connected_address),
            "venue": self.id,
            "checks": {},
        }
        try:
            health = self.ping()
            out["checks"]["rpc_health"] = health
            out["status"] = "OK" if health == "ok" else "DEGRADED"
        except Exception as e:
            out["checks"]["rpc_health"] = False
            out["status"] = "UNREACHABLE"
            out["error"] = http_error_str(e)
            return out
        try:
            prices = self.token_prices([SOL_MINT])
            out["checks"]["jupiter_prices"] = bool(prices.get(SOL_MINT))
            out["sol_usd"] = safe_float((prices.get(SOL_MINT) or {}).get("usdPrice"))
        except Exception as e:
            out["checks"]["jupiter_prices"] = False
            out["jupiter_error"] = http_error_str(e)
        if not self._connected_address:
            out["wallet"] = "NOT_CONNECTED"
            out["note"] = "Connect Phantom in the UI to enable balances/swaps."
        else:
            out["wallet"] = "CONNECTED"
            out["address_tail"] = self._connected_address[-6:]
        return out
