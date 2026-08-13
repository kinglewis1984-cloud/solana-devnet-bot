"""
Devnet paper-trading bot. Reacts to REAL SOL/USD price data with a simple
dip-buy / take-profit-or-stop-loss strategy, and executes a REAL signed,
confirmed transaction on Solana devnet for every signal — a plain System
Program transfer between two devnet-only wallets, used as a stand-in for a
"buy"/"sell" since devnet has no meaningful DEX liquidity to swap against.

Zero real money at risk: both wallets hold devnet SOL only (worthless,
faucet-funded). "P&L" is virtual, computed from real price movement applied
notionally to the transferred amount — a paper-trading ledger backed by real
on-chain transactions, not simulated ones.

Run on a schedule by .github/workflows/devnet-bot.yml. State (price history,
position, trade log) persisted in state/portfolio_state.json, committed back
by the workflow.
"""
import json
import os
import time
from pathlib import Path

import requests
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.system_program import TransferParams, transfer
from solders.message import Message
from solders.transaction import Transaction
from solana.rpc.api import Client
from solana.rpc.types import TxOpts

DEVNET_RPC = os.environ.get("DEVNET_RPC_URL", "https://api.devnet.solana.com")
TRANSFER_AMOUNT_SOL = float(os.environ.get("TRANSFER_AMOUNT_SOL", "0.5"))
BUY_DIP_PCT = float(os.environ.get("BUY_DIP_PCT", "2.0"))
TAKE_PROFIT_PCT = float(os.environ.get("TAKE_PROFIT_PCT", "3.0"))
STOP_LOSS_PCT = float(os.environ.get("STOP_LOSS_PCT", "2.0"))
HISTORY_LEN = int(os.environ.get("HISTORY_LEN", "20"))
MIN_BALANCE_SOL = float(os.environ.get("MIN_BALANCE_SOL", "0.6"))  # alert if below this

STATE_FILE = Path(__file__).resolve().parent.parent / "state" / "portfolio_state.json"

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

CASH_SECRET = json.loads(os.environ["CASH_WALLET_SECRET"])
POSITION_SECRET = json.loads(os.environ["POSITION_WALLET_SECRET"])
cash_kp = Keypair.from_bytes(bytes(CASH_SECRET))
position_kp = Keypair.from_bytes(bytes(POSITION_SECRET))

client = Client(DEVNET_RPC)


def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"price_history": [], "position": "cash", "entry_price": None, "rolling_high": None, "trades": []}


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, indent=2))


def fetch_sol_price():
    resp = requests.get(
        "https://api.coingecko.com/api/v3/simple/price",
        params={"ids": "solana", "vs_currencies": "usd"},
        timeout=15,
    )
    resp.raise_for_status()
    return float(resp.json()["solana"]["usd"])


def send_telegram(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    resp = requests.post(
        url,
        json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown", "disable_web_page_preview": True},
        timeout=15,
    )
    resp.raise_for_status()


def sol_balance(pubkey: Pubkey) -> float:
    return client.get_balance(pubkey).value / 1e9


def execute_transfer(from_kp: Keypair, to_kp: Keypair, amount_sol: float) -> str:
    lamports = int(amount_sol * 1e9)
    ix = transfer(TransferParams(from_pubkey=from_kp.pubkey(), to_pubkey=to_kp.pubkey(), lamports=lamports))
    blockhash = client.get_latest_blockhash().value.blockhash
    msg = Message.new_with_blockhash([ix], from_kp.pubkey(), blockhash)
    tx = Transaction([from_kp], msg, blockhash)
    result = client.send_transaction(tx, opts=TxOpts(skip_preflight=False, preflight_commitment="confirmed"))
    sig = str(result.value)
    client.confirm_transaction(result.value, commitment="confirmed")
    return sig


def explorer_url(sig: str) -> str:
    return f"https://explorer.solana.com/tx/{sig}?cluster=devnet"


def main():
    state = load_state()
    price = fetch_sol_price()

    state["price_history"].append(price)
    state["price_history"] = state["price_history"][-HISTORY_LEN:]

    if state["position"] == "cash":
        if state["rolling_high"] is None or price > state["rolling_high"]:
            state["rolling_high"] = price

        drop_pct = (state["rolling_high"] - price) / state["rolling_high"] * 100
        if drop_pct >= BUY_DIP_PCT:
            balance = sol_balance(cash_kp.pubkey())
            if balance < MIN_BALANCE_SOL:
                send_telegram(
                    f"⚠️ *Devnet bot: low balance*\n"
                    f"Cash wallet has {balance:.3f} devnet SOL, need ≥{MIN_BALANCE_SOL}.\n"
                    f"Fund `{cash_kp.pubkey()}` at https://faucet.solana.com"
                )
            else:
                sig = execute_transfer(cash_kp, position_kp, TRANSFER_AMOUNT_SOL)
                state["position"] = "position"
                state["entry_price"] = price
                state["rolling_high"] = None
                state["trades"].append({"action": "BUY", "price": price, "sig": sig, "ts": time.time()})
                send_telegram(
                    f"🟢 *Devnet bot: BUY*\n"
                    f"SOL @ ${price:.2f} (−{drop_pct:.2f}% from recent high)\n"
                    f"Transferred {TRANSFER_AMOUNT_SOL} devnet SOL into position.\n"
                    f"[View tx]({explorer_url(sig)})"
                )

    elif state["position"] == "position":
        entry = state["entry_price"]
        change_pct = (price - entry) / entry * 100
        if change_pct >= TAKE_PROFIT_PCT or change_pct <= -STOP_LOSS_PCT:
            reason = "take profit" if change_pct >= TAKE_PROFIT_PCT else "stop loss"
            balance = sol_balance(position_kp.pubkey())
            if balance < MIN_BALANCE_SOL:
                send_telegram(
                    f"⚠️ *Devnet bot: low balance*\n"
                    f"Position wallet has {balance:.3f} devnet SOL, need ≥{MIN_BALANCE_SOL}.\n"
                    f"Fund `{position_kp.pubkey()}` at https://faucet.solana.com"
                )
            else:
                sig = execute_transfer(position_kp, cash_kp, TRANSFER_AMOUNT_SOL)
                state["position"] = "cash"
                state["entry_price"] = None
                state["rolling_high"] = price
                state["trades"].append(
                    {"action": "SELL", "price": price, "pnl_pct": change_pct, "sig": sig, "ts": time.time()}
                )
                emoji = "🟢" if change_pct >= 0 else "🔴"
                send_telegram(
                    f"{emoji} *Devnet bot: SELL ({reason})*\n"
                    f"SOL @ ${price:.2f} — entry was ${entry:.2f}\n"
                    f"Paper P&L: {change_pct:+.2f}%\n"
                    f"[View tx]({explorer_url(sig)})"
                )

    save_state(state)


if __name__ == "__main__":
    main()
