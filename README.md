# 🛡️ Telegram Escrow Marketplace Bot

An escrow marketplace for buying and selling monetized social media accounts
(TikTok, YouTube, Facebook, …) — entirely inside Telegram.

**Core principle:** buyers and sellers NEVER transact directly. Money flows
through the bot's escrow wallet; all communication flows through the bot's
anonymous relay. The admin earns a flat commission on every completed deal —
$2 (USDT deals) or Rs 50 (local PKR deals). Listings go live automatically;
Binance/Bybit payments are auto-verified — no admin babysitting needed.

## How it works

1. **SELL** — seller lists an account (category → form → structured
   verification with proof uploads → credentials into the encrypted vault →
   payout details → **auto-approved → LIVE immediately**; flagged listings go
   live too but the admin gets a review alert).
2. **BUY** — buyer browses live listings (budget filter), taps **Buy Now**.
3. **Deal chat** — anonymous relay through the bot. Phone numbers,
   @usernames, t.me links, WhatsApp mentions and emails are masked
   automatically (emails + OTP codes allowed during the handover window).
4. **Pay inside the bot** — Binance/Bybit USDT is **auto-verified** (unique
   amount + deposit polling, see *Auto mode* below); Easypaisa/JazzCash/bank
   stay manual (screenshot → admin confirms).
5. **Delivery** — credentials auto-delivered from the encrypted vault.
6. **8h handover** — buyer/seller may exchange emails + OTPs; then the chat
   auto-locks.
7. **24h hold** — buyer verifies; **Confirm Receipt** any time, or funds
   auto-release to the seller (minus commission). Disputes pause the timer;
   the admin's decision is final.

## Auto mode

Three things run without admin involvement; everything else stays manual.

### 1. Auto-approve listings
`AUTO_APPROVE_LISTINGS` (default `true`): submitted listings go LIVE
immediately — no PENDING queue. The engagement heuristic (followers vs
average views) still runs: flagged listings go live anyway, but the admin
gets **"Listing #N auto-approved but flagged: <reason> — review/remove in
admin panel"** so he can pull them down. Set it to `false` for the old
manual-approval behavior.

### 2. Auto payment verification (Binance + Bybit)
When the buyer picks Binance or Bybit, the bot shows the escrow USDT
address, the network (default TRC20), and an **exact unique amount** —
the price plus random cents ($100 → e.g. **$100.37**). The random cents
prevent cross-deal confusion: every pending deal has a different expected
amount. An APScheduler job then polls deposit history every
`PAYMENT_POLL_INTERVAL_SEC` (default 60) and auto-confirms when it finds a
confirmed deposit whose amount is within `PAYMENT_AMOUNT_TOLERANCE`
(default 0.5 USDT) of the expected value and whose timestamp is after the
poll started. Auto-confirm triggers the exact same delivery flow as an
admin tap. If nothing matches within `PAYMENT_POLL_TIMEOUT_MIN` (default
30), the buyer is told "payment not detected", the admin is notified, and
the deal stays awaiting — the admin can still confirm manually.

**Read-only keys — setup (Binance):**
1. Binance → Profile → API Management → Create API.
2. Enable **"Enable Reading" only**. Do NOT enable Spot trading,
   withdrawals, or anything else — the bot only calls the deposit-history
   endpoint and will never move funds.
3. Optionally restrict the key to your server's IP.
4. Paste the key + secret into `.env` as `BINANCE_API_KEY` /
   `BINANCE_API_SECRET`, set `BINANCE_USDT_ADDRESS` (your deposit address)
   and `BINANCE_NETWORK` (default TRC20).

**Same steps for Bybit** (API Management → read-only key → `BYBIT_API_KEY` /
`BYBIT_API_SECRET` / `BYBIT_USDT_ADDRESS`).

⚠️ **Withdrawal-permission warning:** the bot NEVER needs withdrawal or
trading permission. If anyone (or any guide) tells you to enable it —
don't. Keys are masked as `****1234` in all logs.

If the keys are absent or wrong, Binance/Bybit **gracefully fall back** to
manual mode: the buyer sends their Pay ID and the admin confirms as before.
The bot never claims auto-verification is working when it isn't.

### 3. Differentiated commission
- **USDT deals** (Binance/Bybit): `COMMISSION_USDT_CENTS` (default 200 =
  $2), deducted from the seller payout in USDT.
- **Local deals** (Easypaisa/JazzCash/bank): `COMMISSION_LOCAL_PKR`
  (default 50), deducted in PKR. The buyer is shown **"Pay Rs X"** where
  `X = round(price_usd × USD_PKR_RATE)` (default rate 280, admin-tunable);
  the seller payout = Rs X − Rs 50, and the admin payout instruction shows
  PKR.
- If the price is ≤ the commission (either currency), the deal is held for
  **admin review** instead of auto-releasing.
- The old single `COMMISSION_CENTS` key was replaced cleanly by the two new
  keys — see `.env.example`.

### What stays manual
Easypaisa/JazzCash/bank payments (screenshot → admin confirm), seller
payouts (admin sends the money), disputes, and flagged-listing removal.

## Project layout

```
tg-escrow-bot/
├── bot/
│   ├── main.py            # entrypoint: webhook (prod) / polling (local)
│   ├── config.py          # env-based settings, no hard-coded secrets
│   ├── db/                # base.py (aiosqlite) + schema.sql + repo.py
│   ├── core/              # escrow.py (state machine), states.py (FSM),
│   │                      # dealops.py (business logic), timers.py (APScheduler)
│   ├── payments/          # base.py adapter interface, binance.py, bybit.py,
│   │                      # autoverify.py (deposit polling), manual.py
│   ├── handlers/          # start, sell, buy, wallet, deal, rating, admin
│   ├── middlewares/       # auth.py (ensure user, ban check)
│   └── utils/             # vault (Fernet), masking, money, keyboards,
│                          # texts, catalog (categories.yaml), notify
├── tests/                 # automated test suite (see TEST_MATRIX.md)
├── categories.yaml        # editable category tree — no code changes needed
├── requirements.txt
├── .env.example
├── render.yaml / Procfile # Render/Railway free-tier deploy
├── README.md / SETUP.md
├── TEST_MATRIX.md         # real test results
└── BUILD_REPORT.md        # REAL vs NOT-CONFIGURED/MANUAL + acceptance checklist
```

## Quick start (local)

```bash
cd tg-escrow-bot
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env
# 1) create a bot with @BotFather, paste the token into .env (BOT_TOKEN)
# 2) get your numeric Telegram ID from @userinfobot -> ADMIN_IDS
# 3) generate a vault key: .venv/bin/python -m bot.utils.vault genkey -> FERNET_KEY
# 4) set ESCROW_BINANCE_ADDRESS / ESCROW_MANUAL_NUMBER (where buyers pay)
.venv/bin/python -m bot.main   # polling mode (WEBHOOK_URL empty)
```

Full step-by-step: see **SETUP.md**.

## Safety notes (read before handling real money)

- Past violations/strikes are **declared by the seller** — no platform API
  exposes strike history, and this bot never claims otherwise.
- The credential vault is only as safe as your `FERNET_KEY` and server.
  Back up `data/escrow.db` regularly and never share `.env`.
- Binance **auto**-verify works with **read-only** API keys (deposit
  history only). NEVER enable withdrawal or trading permission on those
  keys. Without keys the bot falls back to manual Pay-ID confirmation.
- Operating an escrow service has legal/fraud implications in your
  jurisdiction — you own and are responsible for how you run it.
