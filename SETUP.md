# SETUP — Telegram Escrow Marketplace Bot

## 1) Create the bot (2 minutes, on your phone)

1. Open Telegram, search **@BotFather**, tap START.
2. Send `/newbot`, choose a display name, then a username
   (must end in `bot`, e.g. `KhizarEscrowBot`).
3. BotFather replies with a **token** like `123456789:AAH...` — copy it.
4. (Optional) `/setdescription` and `/setabouttext` for a nicer profile.

## 2) Get your admin Telegram ID

1. Search **@userinfobot**, tap START — it replies with your numeric ID.
2. You'll paste it as `ADMIN_IDS` below.

## 3) Configure the server / your machine

```bash
cd tg-escrow-bot
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
```

Edit `.env`:

| Key | What to put |
|---|---|
| `BOT_TOKEN` | token from @BotFather |
| `ADMIN_IDS` | your numeric ID from @userinfobot (comma-separated if more) |
| `FERNET_KEY` | run `.venv/bin/python -m bot.utils.vault genkey`, paste output |
| `COMMISSION_CENTS` | `200` = $2 flat fee per completed deal |
| `ESCROW_BINANCE_ADDRESS` | your Binance USDT deposit address (buyers pay here) |
| `ESCROW_MANUAL_NUMBER` | your Easypaisa/JazzCash number or bank account |
| `ESCROW_MANUAL_TITLE` | account title shown to buyers |
| `WEBHOOK_URL` | leave empty for local testing (polling) |

Timers are locked by the spec: 24h hold / 8h handover / 12h delivery deadline.
`BINANCE_MODE` stays `manual` until your Binance merchant API is approved —
then set it to `auto` and add `BINANCE_API_KEY` / `BINANCE_API_SECRET`.

## 4) Run locally (polling)

```bash
.venv/bin/python -m bot.main
```

Open your bot in Telegram, send `/start` — you should see BUY / SELL.
Because your ID is in `ADMIN_IDS`, you also get 🛠️ **Admin Panel**.

## 5) Deploy to Render (free tier)

1. Push this folder to a GitHub repo.
2. Render → New → Web Service → connect the repo.
3. Render reads `render.yaml` automatically; set the **secret** env vars
   (`BOT_TOKEN`, `ADMIN_IDS`, `FERNET_KEY`, `ESCROW_*`, `WEBHOOK_URL`
   = `https://<your-service>.onrender.com`) in the dashboard.
4. Deploy. Set the webhook: open
   `https://api.telegram.org/bot<TOKEN>/setWebhook?url=<WEBHOOK_URL>/webhook`
   once in a browser (or leave `WEBHOOK_URL` empty and the bot polls —
   polling works on Render too, it just uses more uptime).

Railway: same idea — `Procfile` (`web: python -m bot.main`) is included.

> ⚠️ Free tiers sleep when idle. The 24h/8h/12h timers are re-registered
> from the database on every restart, so a sleeping dyno only *delays*
> timer jobs — it never loses them. For real money, a $5–7/month instance
> that never sleeps is strongly recommended.

## 6) Daily operation (admin)

- **Pending listings**: Admin Panel → Pending listings → watch the proof
  video/screenshots → Approve / Reject (with reason).
- **Payments**: deals in AWAITING_PAYMENT show the buyer's proof →
  Confirm (delivers credentials instantly) or Reject.
- **Disputes**: read the archived transcript → Refund Buyer / Release to
  Seller with a reason (logged).
- **Payouts**: after each release the bot shows you a payout instruction
  with the seller's saved details — send the money manually (or automate
  later via a payment adapter).
- **Ledger / CSV**: export the full transaction ledger any time.

## 7) Backup

Back up two things regularly: `data/escrow.db` and your `.env`
(especially `FERNET_KEY` — without it the credential vault is unreadable).
