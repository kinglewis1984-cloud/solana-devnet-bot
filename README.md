# Solana Devnet Trading Bot

A real trading bot that only ever risks devnet SOL (worthless, faucet-funded) —
never mainnet funds. $0 to run: public devnet RPC, GitHub Actions cron (free —
this repo is public specifically to get unlimited free Actions minutes).

## How it works

Devnet has no meaningful DEX liquidity to swap against, so this bot uses a plain
System Program transfer between two devnet-only wallets ("cash" and "position")
as a stand-in for buy/sell — but it's a **real signed, submitted, confirmed
on-chain transaction** every time, verifiable on Solana Explorer, not a
simulation.

Strategy (`scripts/strategy_bot.py`, runs every 30 min via GitHub Actions):
- Watches **real** SOL/USD price (CoinGecko).
- **Buy**: while in cash, if price drops ≥2% from its recent rolling high →
  transfer 0.5 devnet SOL from cash wallet to position wallet.
- **Sell**: while in a position, if price is up ≥3% from entry (take profit) or
  down ≥2% from entry (stop loss) → transfer back.
- Paper P&L is computed from real price movement applied to the transferred
  amount — a real ledger, backed by real transactions, just with worthless
  devnet SOL instead of real money.
- Telegram alert on every trade, with a link to the transaction on devnet
  Explorer. Also alerts if a wallet's devnet SOL balance runs low.

State (price history, current position, trade log) persisted in
`state/portfolio_state.json`, committed back by the workflow — full trade
history is just the repo's commit history on that file.

## Setup

1. Fund the cash wallet with devnet SOL at [faucet.solana.com](https://faucet.solana.com)
   (paste its public key, shown below — takes under a minute, no signup).
2. Push to GitHub, then in Settings → Secrets and variables → Actions, add:
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`
   - `CASH_WALLET_SECRET` — JSON byte array, e.g. `[12,34,...]`
   - `POSITION_WALLET_SECRET` — same format

The workflow runs automatically every 30 minutes once those are set.

## Safety

Both wallets hold **devnet SOL only** — it has no monetary value and cannot be
sold, bridged, or redeemed for anything. This bot never touches mainnet, never
touches a wallet with real funds, and the keys involved are meaningless outside
devnet.
