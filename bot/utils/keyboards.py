"""Inline keyboard builders. Callback-data prefixes:
start: | sell: | buy: | deal: | pay: | wallet: | rate: | admin:
"""
from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def _mk(buttons: list[tuple[str, str]], row: int = 2) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for text, data in buttons:
        b.button(text=text, callback_data=data)
    b.adjust(row)
    return b.as_markup()


def main_menu(is_admin: bool = False) -> InlineKeyboardMarkup:
    btns = [
        ("🛒 BUY", "start:buy"),
        ("📦 SELL", "start:sell"),
        ("🧾 My Orders", "start:orders"),
        ("👛 My Wallet", "start:wallet"),
        ("❓ Help / Rules", "start:help"),
    ]
    if is_admin:
        btns.append(("🛠️ Admin Panel", "admin:menu"))
    return _mk(btns)


def back_home() -> InlineKeyboardMarkup:
    return _mk([("🏠 Main menu", "start:home")])


def yes_no(prefix: str) -> InlineKeyboardMarkup:
    return _mk([(t, f"{prefix}:{d}") for t, d in [("✅ Yes", "yes"), ("❌ No", "no")]])


def categories(cat_names: list[str], prefix: str) -> InlineKeyboardMarkup:
    return _mk([(c, f"{prefix}:cat:{i}") for i, c in enumerate(cat_names)], row=2)


def subcategories(sub_names: list[str], prefix: str) -> InlineKeyboardMarkup:
    btns = [(s, f"{prefix}:sub:{i}") for i, s in enumerate(sub_names)]
    btns.append(("✍️ Custom...", f"{prefix}:sub:custom"))
    return _mk(btns, row=2)


def budget_filter() -> InlineKeyboardMarkup:
    return _mk([
        ("Under $50", "buy:budget:0"),
        ("$50–200", "buy:budget:1"),
        ("$200–500", "buy:budget:2"),
        ("$500+", "buy:budget:3"),
        ("Any", "buy:budget:any"),
    ], row=2)


BUDGET_RANGES = {
    "0": (None, 5000),
    "1": (5000, 20000),
    "2": (20000, 50000),
    "3": (50000, None),
    "any": (None, None),
}


def listing_card(listing_id: int) -> InlineKeyboardMarkup:
    return _mk([("👁 View / Buy", f"buy:view:{listing_id}")])


def listing_detail(listing_id: int, deal_id: int | None = None) -> InlineKeyboardMarkup:
    btns = [("💰 Buy Now", f"deal:buy:{listing_id}")]
    if deal_id:
        btns.append(("💬 Open Deal Chat", f"deal:open:{deal_id}"))
    btns.append(("🔙 Back", "start:buy"))
    return _mk(btns, row=1)


def deal_menu(deal_id: int, is_buyer: bool) -> InlineKeyboardMarkup:
    btns = [("💬 Chat", f"deal:open:{deal_id}")]
    if is_buyer:
        btns += [
            ("💰 Buy Now / Pay", f"deal:pay:{deal_id}"),
            ("✅ Confirm Receipt", f"deal:confirm:{deal_id}"),
            ("🚩 Open Dispute", f"deal:dispute:{deal_id}"),
            ("❌ Cancel", f"deal:cancel:{deal_id}"),
        ]
    else:
        btns.append(("❌ Cancel Deal", f"deal:cancel:{deal_id}"))
    btns.append(("🔚 End Chat", f"deal:endchat:{deal_id}"))
    btns.append(("🏠 Main menu", "start:home"))
    return _mk(btns, row=1)


def pay_methods(enabled: list[str] | None = None) -> InlineKeyboardMarkup:
    """Payment method picker — only shows methods enabled in PAY_METHODS."""
    enabled = [m.lower() for m in (enabled or ["binance", "manual"])]
    btns: list[tuple[str, str]] = []
    if "binance" in enabled:
        btns.append(("🟡 Binance USDT", "pay:method:binance"))
    if "bybit" in enabled:
        btns.append(("🔵 Bybit USDT", "pay:method:bybit"))
    if "manual" in enabled:
        btns.append(("🟢 Easypaisa / JazzCash / Bank", "pay:method:manual"))
    btns.append(("🔙 Back", "start:home"))
    return _mk(btns, row=1)


def endchat_confirm(deal_id: int) -> InlineKeyboardMarkup:
    return _mk([
        ("Yes, end chat", f"deal:endchat_yes:{deal_id}"),
        ("Keep chatting", f"deal:open:{deal_id}"),
    ])


def cancel_confirm(deal_id: int) -> InlineKeyboardMarkup:
    return _mk([
        ("Yes, cancel deal", f"deal:cancel_yes:{deal_id}"),
        ("Keep deal", f"deal:open:{deal_id}"),
    ])


def stars(deal_id: int, ratee: str) -> InlineKeyboardMarkup:
    return _mk([(f"{n}⭐", f"rate:stars:{deal_id}:{n}") for n in range(1, 6)], row=5)


def skip_comment(deal_id: int) -> InlineKeyboardMarkup:
    return _mk([("⏭ Skip", f"rate:skip:{deal_id}")])


def admin_menu() -> InlineKeyboardMarkup:
    return _mk([
        ("📋 Pending listings", "admin:listings"),
        ("💳 Payments to confirm", "admin:payments"),
        ("🚩 Open disputes", "admin:disputes"),
        ("💰 Active deals", "admin:deals"),
        ("👥 Users", "admin:users"),
        ("📣 Broadcast", "admin:broadcast"),
        ("📊 Stats", "admin:stats"),
        ("🧾 Ledger / CSV", "admin:ledger"),
        ("⚙️ Settings", "admin:settings"),
        ("🏠 Main menu", "start:home"),
    ], row=2)


def approve_reject(kind: str, item_id: int) -> InlineKeyboardMarkup:
    return _mk([
        ("✅ Approve", f"admin:{kind}_approve:{item_id}"),
        ("❌ Reject", f"admin:{kind}_reject:{item_id}"),
        ("🔙 Back", "admin:menu"),
    ])


def dispute_decide(deal_id: int) -> InlineKeyboardMarkup:
    return _mk([
        ("↩️ Refund Buyer", f"admin:dispute_refund:{deal_id}"),
        ("💰 Release to Seller", f"admin:dispute_release:{deal_id}"),
        ("🔙 Back", "admin:menu"),
    ])


def user_moderate(tg_id: int) -> InlineKeyboardMarkup:
    return _mk([
        ("⚠️ Warn", f"admin:warn:{tg_id}"),
        ("🚫 Ban", f"admin:ban:{tg_id}"),
        ("✅ Unban", f"admin:unban:{tg_id}"),
        ("🔙 Back", "admin:users"),
    ])
