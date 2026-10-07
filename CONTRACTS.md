# CONTRACTS — read this FIRST, then read the foundation files.

You are implementing ONE module of the Telegram Escrow Marketplace Bot.
The full spec is at `~/workspace/user/files/tg-escrow-bot-build-spec.txt`
(sections referenced as §N). Build EXACTLY per spec; do not weaken anything.

## Project root
`~/workspace/tg-escrow-bot/` — work ONLY inside it. Run from that dir
(`categories.yaml` is loaded via relative path "categories.yaml").

## What already exists (READ the files before writing code)
- `bot/config.py` — `settings` singleton: bot_token, admin_ids,
  fernet_key, commission_cents (default 200), hold_hours=24,
  handover_hours=8, delivery_deadline_hours=12, binance_mode,
  escrow_binance_address, escrow_manual_number/title, database_path,
  webhook_url, port, rating_window_days. `settings.is_admin(tg_id)`.
- `bot/db/base.py` — `Database(path)`; `await db.init(schema_path)`.
- `bot/db/schema.sql` — tables: users, listings, credentials, payouts,
  deals, messages, ledger, ratings, audit, jobs. Money = integer cents.
- `bot/db/repo.py` — `Users, Listings, Credentials, Payouts, Deals,
  Messages, Ledger, Ratings, Audit, Jobs` classes, all async, all take
  `db: Database`. Use these — NEVER write raw SQL in handlers.
- `bot/utils/vault.py` — `Vault(key).encrypt/.decrypt` (Fernet).
- `bot/utils/masking.py` — `mask_text(text, handover=False) ->
  (masked_text, offenses)`. Offenses: phone|username|link|email|whatsapp.
- `bot/utils/money.py` — `fmt_cents`, `parse_usd`, `commission_for`,
  `release_amount(price, commission)` → None when price<=commission
  (means ADMIN REVIEW, never auto-release $0/negative).
- `bot/core/escrow.py` — `DealStatus` (NEGOTIATING, AWAITING_PAYMENT,
  PAID_HELD, DISPUTED, RELEASED, REFUNDED, CANCELLED), `TRANSITIONS`,
  `can_transition`, `transition(db, deal_id, to, actor, note)` (raises
  IllegalTransition; audit-logs). `ListingStatus` + `transition_listing`.
- `bot/core/states.py` — FSM groups: SellFlow, BuyFlow, PayoutSetup,
  RateFlow, DisputeFlow, BroadcastFlow, SettingsFlow, WarnFlow.
- `bot/core/timers.py` — `make_scheduler()`, `schedule_job(db, scheduler,
  bot, deal_id, kind)`, `cancel_job`, `cancel_all_jobs`. Kinds:
  'delivery_deadline' (12h), 'handover_lock' (8h), 'hold_release' (24h).
  NOTE: timers.py imports `bot.core.dealops` lazily — dealops must exist.
- `bot/utils/texts.py` — all UI strings.
- `bot/utils/keyboards.py` — builders; callback prefixes
  `start:|sell:|buy:|deal:|pay:|wallet:|rate:|admin:`. `BUDGET_RANGES`
  maps budget key → (min_cents, max_cents).
- `bot/utils/catalog.py` — `category_names()`, `sub_names(category)`.
- `bot/utils/notify.py` — `notify_admins(bot, text)`.
- `bot/middlewares/auth.py` — `UserMiddleware(db)`: ensures user row,
  blocks banned users, puts `db_user` dict into handler data.
- `bot/payments/base.py` — `PaymentAdapter` ABC (`code`, `label`,
  `instructions(deal, cfg)`, `needs_admin_confirm`, `auto_verify_possible`).
- `bot/payments/binance.py` — `BinanceAdapter(cfg)` (MANUAL default).
- `bot/payments/manual.py` — `ManualAdapter()`.

## Hard rules for YOUR code
1. Every handler module exposes exactly: `router = Router()`.
   Register middlewares/routers ONLY in main.py (not your job).
2. Parse_mode is HTML everywhere. Escape user input with
   `aiogram.utils.markdown.hcode` / `html.quote` where interpolated.
3. Access shared objects ONLY via the `data` dict aiogram gives you:
   `data["db"]`, `data["bot"]`, `data["scheduler"]`, `data["vault"]`,
   `data["settings"]`, `data["db_user"]`. NEVER create your own
   Database/Bot instances. NEVER read env directly — use settings.
4. Money math only via `bot.utils.money`. Amounts in cents.
5. Deal status changes ONLY via `escrow.transition(...)` with actor
   strings: `'user:<tg_id>'`, `'admin:<tg_id>'`, `'bot'`.
6. Listing status changes ONLY via `escrow.transition_listing(...)`.
7. No secrets in code, logs, or messages. No seller identity is ever
   shown to buyers (no tg_id, username, name — only aggregate rating).
8. FSM: always `await state.clear()` when a flow ends or is cancelled.
   Add a "❌ Cancel" inline button on multi-step flows where sensible.
9. Handlers must be defensive: re-check deal/listing status from DB
   inside every callback (no trusting callback data alone); verify the
   caller is the buyer/seller/admin the action requires.

## Module assignments

### WORKER A — user flows: `bot/handlers/start.py`, `sell.py`, `buy.py`, `wallet.py`
- `start.py`: `/start` → texts.MAIN_MENU + keyboards.main_menu(is_admin).
  Callbacks: `start:home` (re-show menu), `start:buy` → delegate to buy
  flow entry, `start:sell` → delegate to sell flow entry,
  `start:orders` → list user's deals (Deals.for_user) with status +
  buttons `deal:open:<id>`, `start:wallet` → delegate to wallet,
  `start:help` → texts.HELP_RULES.
  (To delegate: import the entry functions from sell/buy/wallet and call them.)
- `sell.py` — full SellFlow FSM per spec §5:
  category (keyboards.categories) → subcategory (+Custom free text) →
  title → price (parse_usd; must be > commission, else warn) →
  followers (int) → avg_views OPTIONAL (improvement: ask "average views
  per post (optional, send 0 to skip)") → country → account_age →
  description → ver_email_access (yes/no) → ver_violations (yes/no) →
  if yes: details → ver_monetized (yes/no) → ver_proof_video (must be a
  video; store file_id) → ver_proof_shots (photos; "Done" button ends;
  at least 1 required) → credentials (text; encrypt via vault, save via
  Credentials.save) → payout method (binance/easypaisa/jazzcash/bank
  buttons) → payout details (text; Payouts.save) → review screen showing
  everything + engagement heuristic flag →
  [✅ Submit] → status PENDING, notify admins, texts.LISTING_SUBMITTED.
  Engagement heuristic: if followers>1000 and avg_views>0 and
  avg_views < followers*0.001 → engagement_flag=1 + show
  texts.ENGAGEMENT_FLAG_NOTE. Checklist JSON stored:
  {"age":"Seller declared","email_access":"Seller declared",
   "violations":"Seller declared","monetization":"Seller declared",
   "proof_video":"Proof attached","proof_shots":"Proof attached"}.
  NEVER claim automatic strike/violation verification — label the
  checklist "Seller declared" honestly (§5 note).
- `buy.py` — BuyFlow per spec §6: category → budget
  (keyboards.budget_filter → BUDGET_RANGES) → list live listings
  (Listings.live, 5 per page with ◀ ▶ pagination via
  `buy:page:<cat>:<budget>:<offset>`) as cards: title, price, followers,
  country, monetization badge (✅/❌), verification summary
  (e.g. "✔ proof video + 3 screenshots"), seller rating
  (Users.rating_text). Card button → `buy:view:<id>` detail view with
  full description + checklist + keyboards.listing_detail. NO seller
  identity anywhere. `deal:buy:<listing_id>` is handled by WORKER B's
  deal.py — your detail screen just links to it.
- `wallet.py` — `show_wallet`: saved payout method/details (Payouts.get,
  masked details!), buttons to update via PayoutSetup FSM; escrow note:
  "Money is held by the bot — this wallet only stores your payout
  details." Mask sensitive payout details when displaying
  (show first/last few chars).

### WORKER B — deal engine: `bot/core/dealops.py`, `bot/handlers/deal.py`,
### `bot/handlers/rating.py` (+ payments adapters already exist)
`dealops.py` — ALL business logic as async functions (handlers are thin):
- `open_deal(db, bot, listing_id, buyer_id) -> int`: guards — listing LIVE,
  buyer != seller, no active deal on listing (Deals.active_for_listing);
  create deal (commission = settings.commission_cents), set listing SOLD?
  NO — listing stays LIVE until payment (another buyer could open a
  competing negotiation; first PAID wins; others get CANCELLED on payment).
  Notify seller (texts.DEAL_OPENED_SELLER), return deal_id.
- `choose_payment(db, bot, deal_id, method)` — method in ('binance','manual');
  transition NEGOTIATING→AWAITING_PAYMENT; send adapter.instructions.
- `submit_payment_proof(db, bot, deal_id, user_id, proof)` — buyer only,
  deal AWAITING_PAYMENT; store proof in payment_ref; notify admins with
  [Confirm]/[Reject] buttons (`admin:pay_approve:<deal>`,
  `admin:pay_reject:<deal>`).
- `admin_confirm_payment(db, bot, scheduler, vault, deal_id, admin_id)`:
  transition AWAITING_PAYMENT→PAID_HELD; ledger 'hold' entry; then
  `deliver_credentials(...)`.
- `admin_reject_payment(...)`: back to NEGOTIATING (buyer may retry),
  notify buyer with reason.
- `deliver_credentials(db, bot, scheduler, vault, deal_id)`: decrypt vault
  blob, send ONE-TIME full view to buyer (texts.DELIVERED_BUYER), then
  send a follow-up "credentials hidden" notice; set delivered_at,
  handover_until, hold_until; schedule handover_lock + hold_release jobs;
  notify seller (texts.DELIVERED_SELLER); ledger note. (12h delivery
  deadline only applies if you add manual-delivery listings — NOT in
  scope: credentials are always in the vault at listing time, so delivery
  is instant. Still implement `on_delivery_deadline` → auto-cancel +
  auto-refund for safety if delivery_due_at was ever set.)
- `on_handover_lock(db, bot, deal_id)`: set chat_open=0, notify both
  (texts.HANDOVER_LOCKED). `handover_active(deal) -> bool` helper:
  delivered_at set and now < handover_until and chat_open.
- `on_hold_release(db, bot, scheduler, deal_id)`: only if PAID_HELD and
  hold expired and not disputed: `release_funds(...)`.
- `release_funds(db, bot, deal_id, actor)`: transition →RELEASED;
  net = money.release_amount(price, commission); if net is None →
  DO NOT release: notify admins "admin review needed (price ≤ commission)"
  and leave deal PAID_HELD. Else ledger 'release' + 'commission' entries;
  notify seller with payout instruction (texts.RELEASED_SELLER using
  Payouts.get masked); notify buyer; bump deals_count; prompt ratings
  (see rating.py `prompt_ratings`).
- `refund_deal(db, bot, deal_id, actor, reason)`: guards — anti-double-dip:
  only from PAID_HELD/DISPUTED/AWAITING_PAYMENT; NEVER from RELEASED.
  transition →REFUNDED; ledger 'refund'; notify buyer (original method
  note) + seller; prompt ratings.
- `cancel_deal(db, bot, deal_id, user_id)`: buyer cancel only in
  NEGOTIATING/AWAITING_PAYMENT; seller cancel only in NEGOTIATING/
  AWAITING_PAYMENT/PAID_HELD-before-delivery. If money was held
  (PAID_HELD + not delivered) → refund_deal(actor 'user:...'). Else
  transition →CANCELLED. If listing still LIVE keep it; if the cancelling
  deal was the reason it sold... (listings only go SOLD on payment —
  see open_deal) → on payment confirm of a deal, set its listing SOLD
  and auto-CANCEL other NEGOTIATING/AWAITING_PAYMENT deals on it.
- `open_dispute(db, bot, deal_id, buyer_id, reason)`: buyer only, from
  PAID_HELD; transition →DISPUTED; cancel hold_release job (timer paused);
  notify admins (texts.DISPUTE_ADMIN + keyboards.dispute_decide).
- `admin_resolve_dispute(db, bot, scheduler, deal_id, admin_id, decision, reason)`:
  decision in ('refund','release'); refund → refund_deal(actor admin);
  release → release_funds(actor admin).
- `confirm_receipt(db, bot, scheduler, deal_id, buyer_id)`: buyer only,
  PAID_HELD → release_funds immediately; cancel hold job.
- `relay_message(db, bot, deal_id, sender_id, text)`: determine role;
  guards: deal active (not RELEASED/REFUNDED/CANCELLED), chat_open or
  sender is admin; handover = handover_active(deal); mask_text(text,
  handover) → store Messages.add(deal, role, text, masked); forward to
  other party as "🗣 <b>Buyer:</b> ..."/"🏷 <b>Seller:</b> ...". If
  offenses: send texts.MASK_WARNING to sender; increment offense_count;
  if offense_count >= 2 → notify_admins(texts.MASK_ADMIN_ALERT).
- `end_chat(db, bot, deal_id, user_id)`: either party; confirm via
  keyboards.endchat_confirm; sets chat_open=0; does NOT touch money.
- `prompt_ratings(db, bot, deal_id)`: send texts.RATE_PROMPT to both.
`deal.py` — thin handlers: all `deal:` callbacks (buy/open/pay/confirm/
  dispute/cancel/endchat + confirmations), `pay:method:<m>` callbacks,
  plain-text relay (Message handler when user has an "active deal chat":
  track via FSM? Simpler: if text and user has exactly ONE deal with
  chat_open=1 and status in active set → relay; if multiple → ask to tap
  💬 Chat on the deal first, which sets `data['active_deal']`... aiogram
  data dict is per-update. Use FSM: `DealChat.chatting` state with
  state_data deal_id. `deal:open:<id>` sets state. Plain text in that
  state → relay_message. `/cancel` or End Chat clears.)
  Payment proof: in AWAITING_PAYMENT, buyer sends text (Pay ID) or photo
  (screenshot) → submit_payment_proof.
  Dispute reason via DisputeFlow FSM.
`rating.py` — `prompt_ratings(db, bot, deal_id)` (used by dealops);
  `rate:stars:<deal>:<n>` → RateFlow.comment (skip button) → Ratings.add,
  Users.add_rating; guard: rater must be buyer/seller of a completed
  (RELEASED/REFUNDED) deal, within settings.rating_window_days, one per
  side (Ratings.exists). After both rated or window passes — no extra
  action needed. Low-rating alert: after adding, check
  Ratings.low_sellers → notify_admins.

### WORKER C — admin + entrypoint: `bot/handlers/admin.py`, `bot/main.py`
- `admin.py` — guard EVERYTHING with settings.is_admin (texts.NOT_ADMIN).
  `admin:menu` → keyboards.admin_menu.
  - listings: list PENDING with details + proof (send video/photos) +
    keyboards.approve_reject('listing', id) → approve: transition_listing
    PENDING→LIVE (+checklist item "Admin spot-checked" for proof items),
    notify seller; reject: →REJECTED with reason (ask via FSM).
  - payments: deals in AWAITING_PAYMENT with proof → show proof +
    approve/reject → call dealops.admin_confirm_payment /
    admin_reject_payment (needs scheduler, vault from data dict).
  - disputes: disputed deals + transcript (Messages.transcript, last 30)
    + keyboards.dispute_decide → reason via FSM → dealops.admin_resolve_dispute.
  - deals: active deals list with statuses.
  - users: list (Users.list_all, 10/page), `admin:user:<id>` → detail +
    keyboards.user_moderate → warn (WarnFlow reason → Users.warn,
    notify user; 3 warns → auto-ban? NO — alert admins instead, spec
    says bannable by admin) / ban / unban.
  - broadcast: BroadcastFlow text → confirm (shows recipient count) →
    send to all users (catch exceptions, count sent).
  - stats: users count, deals by status, ledger totals (volume held /
    released / refunded / commission earned).
  - ledger: last 20 entries + "📥 Export CSV" → send CSV file
    (id,created_at,kind,amount_usd,actor,deal_id,note).
  - settings: show current (commission $, hold/handover/delivery hours,
    binance mode); SettingsFlow to change commission_cents (int ≥ 0;
    note: changing .env needs restart — store override in DB? Simplest
    honest approach: settings table in DB overriding env. CREATE the
    override: add tiny `kv` table? You may NOT edit schema.sql — instead
    keep a module-level dict override in admin.py? That dies on restart.
    DECISION: commission/hours are read from settings (env) at startup;
    admin panel shows them and explains changes need .env + restart.
    This is honest and matches "config: .env" in spec §2. Document it.)
- `main.py` — `async def main()`: validate settings (print problems, exit
  1); init Database with schema; create vault, scheduler; build Bot with
  default HTML parse mode; Dispatcher; UserMiddleware(db); store in
  workflow_data: db, scheduler, vault, settings; include routers from
  ALL handler modules (start, sell, buy, wallet, deal, rating, admin);
  set_my_commands (/start, /help, /cancel); scheduler.start();
  reschedule: read jobs.due(now) → re-schedule via timers.schedule_job
  (recompute run_at = max(stored run_at, now+60s));
  if settings.webhook_url: webhook mode (SimpleRequestHandler +
  setup_application on port) else polling (delete webhook first).
  `if __name__ == "__main__": asyncio.run(main())`.

## Callback data reference (do not invent new prefixes)
start:home|buy|sell|orders|wallet|help · sell:... · buy:cat|sub|budget|page|view
deal:buy|open|pay|confirm|dispute|cancel|endchat (+_yes variants) · pay:method:<m>
rate:stars|skip · wallet:... · admin:menu|listings|payments|disputes|deals|users|broadcast|stats|ledger|settings (+ _approve/_reject/_decide/_warn/_ban/_unban)

Write clean, commented code. No placeholders, no TODOs — everything must run.
