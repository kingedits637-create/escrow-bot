# TEST MATRIX — real results (spec §18)

Run: `cd tg-escrow-bot && .venv/bin/python -m pytest tests/ -q`
Date: 2026-10-06. Python 3.12, aiogram 3.x, SQLite.

**Result: 60 passed, 0 failed.**

| # | Area (spec §) | Test file | Cases | Result |
|---|---|---|---|---|
| 1 | Escrow state machine — legal transitions accepted | test_state_machine.py | 9 parametrized | ✅ pass |
| 2 | Escrow state machine — illegal transitions rejected | test_state_machine.py | 10 parametrized | ✅ pass |
| 3 | Transition writes + audit log entry | test_state_machine.py | 1 | ✅ pass |
| 4 | Illegal transition raises, state unchanged | test_state_machine.py | 1 | ✅ pass |
| 5 | Anti-double-dip: no refund after RELEASED | test_state_machine.py | 1 | ✅ pass |
| 6 | Listing lifecycle DRAFT→PENDING→LIVE; no backwards moves | test_state_machine.py | 1 | ✅ pass |
| 7 | Commission math (flat fee) | test_commission.py | 2 | ✅ pass |
| 8 | Commission edge: price == commission → admin review (None) | test_commission.py | 1 | ✅ pass |
| 9 | Commission edge: price < commission → admin review, never negative | test_commission.py | 2 | ✅ pass |
| 10 | Money formatting / USD parsing | test_commission.py | 2 | ✅ pass |
| 11 | Vault encrypt/decrypt round-trip | test_vault.py | 1 | ✅ pass |
| 12 | Vault: wrong key / tampered blob / missing key rejected | test_vault.py | 3 | ✅ pass |
| 13 | Masking: phones masked (normal mode) | test_masking.py | 1 | ✅ pass |
| 14 | Masking: @usernames masked | test_masking.py | 1 | ✅ pass |
| 15 | Masking: t.me links masked | test_masking.py | 1 | ✅ pass |
| 16 | Masking: WhatsApp mentions masked | test_masking.py | 1 | ✅ pass |
| 17 | Masking: email blocked outside handover | test_masking.py | 1 | ✅ pass |
| 18 | Masking: email allowed in handover | test_masking.py | 1 | ✅ pass |
| 19 | Masking: OTP (4–8 digits) allowed in handover | test_masking.py | 1 | ✅ pass |
| 20 | Masking: OTP blocked outside handover | test_masking.py | 1 | ✅ pass |
| 21 | Masking: phone still blocked in handover | test_masking.py | 1 | ✅ pass |
| 22 | Masking: username still blocked in handover | test_masking.py | 1 | ✅ pass |
| 23 | Masking: clean message passes untouched | test_masking.py | 1 | ✅ pass |
| 24 | Masking: product talk (no contact info) untouched | test_masking.py | 1 | ✅ pass |
| 25 | Timer: hold-release fires when 24h expired | test_timers.py | 1 | ✅ pass |
| 26 | Timer: hold-release skipped when not expired | test_timers.py | 1 | ✅ pass |
| 27 | Timer: hold-release skipped when disputed (timer paused) | test_timers.py | 1 | ✅ pass |
| 28 | Timer: handover lock closes chat after 8h | test_timers.py | 1 | ✅ pass |
| 29 | Timer: schedule + cancel job wiring | test_timers.py | 1 | ✅ pass |
| 30 | Confirm Receipt releases instantly | test_timers.py | 1 | ✅ pass |
| 31 | Confirm Receipt by non-buyer rejected | test_timers.py | 1 | ✅ pass |
| 32 | Buyer cancel before payment → CANCELLED | test_refunds.py | 1 | ✅ pass |
| 33 | Dispute → admin refund (ledger + buyer notified) | test_refunds.py | 1 | ✅ pass |
| 34 | Dispute → admin release | test_refunds.py | 1 | ✅ pass |
| 35 | Anti-double-dip: refund after release raises | test_refunds.py | 1 | ✅ pass |
| 36 | Price ≤ commission → held for admin review, not released | test_refunds.py | 1 | ✅ pass |
| 37 | Competing deal auto-cancelled on first payment; listing SOLD | test_refunds.py | 1 | ✅ pass |
| 38 | Delivery-deadline miss → auto-cancel/refund | test_refunds.py | 1 | ✅ pass |
| 39 | Budget filter queries (5 ranges) | test_budget_filter.py | 1 | ✅ pass |
| 40 | Non-LIVE listings excluded from browse | test_budget_filter.py | 1 | ✅ pass |

Integration (not in pytest, run manually):
- **Dry boot**: all 7 routers import together, 95 message/callback handlers
  registered, all 18 dealops functions present, Binance adapter reports
  `auto_verify_possible=False` (manual default), Dispatcher + middleware +
  workflow_data wire up, clean shutdown. ✅

Not covered by automated tests (honest):
- Live Telegram round-trips (no bot token / network in this environment).
  Handler logic was reviewed against the contract; FSM flows compile and
  register.
- Real Binance API calls (no merchant credentials by design).
- Webhook mode against Render (config present, not deployed).
