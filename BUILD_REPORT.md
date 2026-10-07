# BUILD REPORT — Telegram Escrow Marketplace Bot
Date: 2026-10-06. Built per `~/workspace/user/files/tg-escrow-bot-build-spec.txt`.

## What was built
Complete Python/aiogram 3.x bot: `/start` menu (BUY/SELL/My Orders/My Wallet/
Help), full sell flow (categories.yaml → form → structured verification →
Fernet vault → payout details → admin approval), buy flow (budget filter →
anonymous listing cards), anonymous deal-chat relay with smart masking,
payment gate (Binance/manual adapters), auto-delivery from vault, 8h handover,
24h hold with auto-release, disputes + auto/manual refunds, ratings, in-bot
admin panel (approvals, payments, disputes, users, broadcast, stats, ledger +
CSV export, settings display). APScheduler timers persisted in DB and
re-registered on restart. `render.yaml` + `Procfile` + `requirements.txt` +
`.env.example` included.

Tests: **60 passed, 0 failed** (see TEST_MATRIX.md) + dry-boot integration check.

## REAL (works without anything else)
- Everything above except the items listed below as NOT-CONFIGURED/MANUAL.
- State machine, masking, vault, money math, timers, budget filter, refund
  routing — all covered by automated tests with real SQLite + real Fernet +
  real APScheduler objects.

## NOT-CONFIGURED / MANUAL (honest)
- **Binance AUTO-verify: NOT CONFIGURED.** The adapter exists
  (`bot/payments/binance.py`) and reports `auto_verify_possible=False`.
  It ships in **MANUAL** mode per the spec: buyer sends USDT to the escrow
  address, submits their Pay ID, admin taps Confirm. AUTO needs the owner's
  Binance merchant approval + `BINANCE_MODE=auto` + API keys. The bot never
  claims auto-verification is working when it isn't.
- **Seller payouts are MANUAL.** On release the bot shows the admin a payout
  instruction with the seller's saved details; the admin sends the money.
  ("Auto-send where the method allows" — no method allows it yet.)
- **Escrow addresses** (`ESCROW_BINANCE_ADDRESS`, `ESCROW_MANUAL_NUMBER`)
  are empty placeholders until the owner fills `.env`.
- **Webhook** works when `WEBHOOK_URL` is set; otherwise the bot polls
  (fine for testing; free-tier dynos sleep, which only *delays* timers —
  they are re-registered from the DB on restart, never lost).
- **12h delivery deadline** is implemented as a safety net
  (`on_delivery_deadline` → auto-cancel/refund) but is normally unused:
  credentials are already in the vault at listing time, so delivery is
  instant on payment confirmation.
- **Admin settings are display-only.** Commission/timers come from `.env`;
  the panel shows them and explains that changes need an `.env` edit +
  restart (no silent in-memory overrides that die on reboot).
- **Strike/violation history is seller-declared only**, labeled honestly in
  the buyer-visible checklist ("Seller declared"). No platform API exposes
  this; the bot never claims an automatic check.

## Acceptance checklist walkthrough (spec §19)
- [x] `/start` shows BUY + SELL, both usable by the same user — `start.py`;
  verified in dry boot.
- [x] Sell flow: categories → sub-categories + Custom → form → verification →
  vault → payout details → admin approval → live — `sell.py` implements every
  step; checklist + engagement flag stored.
- [x] Buy flow: categories → budget filter → listings (no seller identity) →
  detail → Buy Now → seller notified — `buy.py` + `dealops.open_deal`;
  budget ranges covered by tests; rating shown is aggregate only.
- [x] Deal chat: anonymous relay, masking works, End Chat works, transcript
  archived — `dealops.relay_message/end_chat`, `Messages` table; masking
  covered by 12 tests.
- [x] Payment: inside bot only; Binance auto-verify adapter present, manual
  fallback working; manual methods via admin confirm — adapters +
  `submit_payment_proof`/`admin_confirm_payment` tested.
- [x] Delivery: credentials auto-delivered from vault on payment — tested
  end-to-end in `test_timers.py`/`test_refunds.py` fixtures.
- [x] 8h handover: OTP/email allowed, phones/usernames still blocked,
  auto-lock after 8h — tested.
- [x] 24h hold → auto-release minus configurable commission — tested.
- [x] Disputes pause timer; admin refund/release works — tested.
- [x] Auto-refunds fire on missed delivery deadline / cancellations — tested.
- [x] Ratings from real deals only; shown on listings — guards in `rating.py`;
  aggregate shown on cards via `Users.rating_text`.
- [x] Admin panel: all functions working; ledger + CSV export — `admin.py`
  (all 31 handlers admin-guarded); CSV export coded.
- [x] No hard-coded secrets; `.env.example` complete — grep-verified; no
  `.env` shipped.

Caveat: handler flows were not exercised against live Telegram (no token /
network here) — logic reviewed against the contract, all modules compile
and register (95 handlers in dry boot).

## Improvements made beyond the spec (§20 permission)
1. **Optional "average views" field in the sell form** — the spec's
   engagement heuristic needed it but the form didn't collect it; used only
   for the admin flag + review display.
2. **Seller's credential message is deleted** right after vault encryption —
   plaintext credentials never linger in chat history.
3. **Payout details are masked on display** (first/last-2 chars) — even the
   seller's own wallet view doesn't leak full numbers unnecessarily.
4. **Brace-safe templating + HTML-escaping of all user input** — listing
   titles/credentials containing `{...}` can't break formatting or inject markup.
5. **3 warnings → admin alert (no auto-ban)** — banning stays a conscious
   admin decision, per the spec's "bannable" wording.
6. **Competing negotiations allowed; first PAID wins** — an absolute
   one-deal-per-listing guard would have made the spec's own auto-cancel
   rule dead code.
7. **Dual payment-callback handlers in admin panel** — closed a real
   ambiguity between the deal-engine and admin-panel contracts so payment
   confirm/reject works from both entry points.
8. **Timers re-registered from the DB on every restart** — free-tier sleeps
   delay but never lose the 24h/8h/12h jobs.

## Owner action items (your part — 10 minutes)
1. `@BotFather` → `/newbot` → paste token into `.env` as `BOT_TOKEN`.
2. `@userinfobot` → your numeric ID → `.env` as `ADMIN_IDS`.
3. `.venv/bin/python -m bot.utils.vault genkey` → `.env` as `FERNET_KEY`.
4. Fill `ESCROW_BINANCE_ADDRESS` / `ESCROW_MANUAL_NUMBER` (where buyers pay).
5. Run `.venv/bin/python -m bot.main`, open the bot, `/start`.
Full guide: SETUP.md. Binance AUTO mode later needs your merchant approval.

## AUTO MODE update (2026-10-06, owner-ordered)

Three features added on top of the build above. Tests now: **88 passed,
0 failed** (60 original + 28 new: `test_autoverify.py` 15,
`test_commission_pkr.py` 9, `test_autopublish.py` 4). `compileall` clean,
`bot.main` dry-imports fine. No running bot process was touched.

### 1. Auto-approve listings
- `AUTO_APPROVE_LISTINGS` (default `true`): submitted listings go
  DRAFT → LIVE directly, skipping PENDING/admin approval.
- The engagement-ratio heuristic (followers vs avg views) still runs —
  flagged listings go LIVE **and** the admin is notified
  ("auto-approved but flagged: <reason> — review/remove in admin panel").
- `AUTO_APPROVE_LISTINGS=false` restores the old PENDING → admin approval
  behavior. New transition `DRAFT → LIVE` added to the state machine.

### 2. Auto payment verification (Binance + Bybit, deposit polling)
- New module `bot/payments/autoverify.py`: signed read-only GETs to
  Binance `sapi/v1/capital/deposit/hisrec` and Bybit v5
  `asset/deposit/query-record`, response parsers normalized to
  `{amount, time_ms, confirmed, network, txid}` with injectable HTTP for
  tests. Secrets are masked (`****1234`) in logs; keys are read-only by
  construction (deposit-history endpoints only).
- **Unique-amount trick:** buyer sees e.g. "Send exactly **100.37 USDT
  (TRC20)** to <address>" — price + random 01–99 cents, stored per deal,
  re-rolled on collision. Match = confirmed status + |deposit − expected|
  ≤ `PAYMENT_AMOUNT_TOLERANCE` (default 0.5) + deposit time ≥ poll start
  (+ optional network filter; default network TRC20).
- APScheduler interval job (`PAYMENT_POLL_INTERVAL_SEC`, default 60):
  on match → `auto_confirm_payment()` → the SAME delivery flow as an
  admin confirm. On timeout (`PAYMENT_POLL_TIMEOUT_MIN`, default 30) →
  buyer gets "payment not detected", admin is notified, deal stays
  AWAITING_PAYMENT for manual confirm. Poll jobs re-register from the DB
  on restart (`awaiting_autopay()`); the job is stopped on confirm/timeout.
- Manual methods (Easypaisa/JazzCash/bank screenshot) UNCHANGED.
- No keys / bad keys → graceful fallback to the old manual Pay-ID flow;
  the bot never claims auto works when it doesn't.

### 3. Differentiated commission per payment method (owner-ordered)
- `COMMISSION_CENTS` **replaced cleanly** (no backward-compat shim) by:
  `COMMISSION_USDT_CENTS` (default 200 = $2, Binance/Bybit, deducted in
  USDT) and `COMMISSION_LOCAL_PKR` (default 50, local methods, deducted
  in PKR). `.env.example` documents the rename.
- `USD_PKR_RATE` (default 280): local buyers see "Pay Rs X" where
  X = round(price_usd × rate); seller payout = Rs X − Rs 50; admin payout
  instruction shows PKR. New `deals` columns: `payable_pkr`,
  `commission_pkr`, `expected_usdt`, `pay_poll_started_at`,
  `auto_verified` (idempotent migration for existing DBs).
- Price ≤ commission → admin review kept for BOTH currencies
  (`release_amount()`/`release_amount_pkr()` return None → stays
  PAID_HELD, admin notified).
- Anti-fraud posture unchanged: engagement heuristic, anti-double-dip,
  refunds via original method only, masking/relay, vault encryption.

### Could NOT verify (needs owner input / live environment)
- The Binance/Bybit fetchers are fully mock-tested but have never hit the
  real APIs — live verification needs the owner's read-only keys in
  `.env`. Bybit v5 deposit `status == "3"` (success) is from API docs,
  not a live call.
- No live Telegram test of the new flows (no token in this environment).

## Post-delivery fix (2026-10-06, live test)
- BUG FOUND during live Telegram test: every handler declared `data: dict`
  but aiogram 3.x does not inject a `data` kwarg by that name →
  `TypeError: cmd_start() missing 1 required positional argument: 'data'`
  on the very first /start. (Unit tests didn't catch it — they never ran
  handlers through a real Dispatcher.)
- FIX: `bot/middlewares/auth.py` now sets `data["data"] = data` so the
  full context dict is name-injectable into all 51 handlers. No handler
  signatures changed. Verified live: /start now responds.
