"""aiogram FSM states for the multi-step flows."""
from aiogram.fsm.state import State, StatesGroup


class SellFlow(StatesGroup):
    category = State()
    subcategory = State()
    subcategory_custom = State()
    title = State()
    price = State()
    followers = State()
    avg_views = State()  # optional improvement: engagement sanity check
    country = State()
    account_age = State()
    description = State()
    ver_email_access = State()
    ver_violations = State()
    ver_violations_details = State()
    ver_monetized = State()
    ver_proof_video = State()
    ver_proof_shots = State()
    credentials = State()
    payout_method = State()
    payout_details = State()
    review = State()


class BuyFlow(StatesGroup):
    category = State()
    budget = State()
    browsing = State()


class PayoutSetup(StatesGroup):
    method = State()
    details = State()


class RateFlow(StatesGroup):
    stars = State()
    comment = State()


class DisputeFlow(StatesGroup):
    reason = State()


class DealChat(StatesGroup):
    """Anonymous deal-chat relay (WORKER B). deal_id is kept in state data."""
    chatting = State()
    pay_select = State()  # buyer is picking a payment method for deal_id


class BroadcastFlow(StatesGroup):
    text = State()
    confirm = State()


class SettingsFlow(StatesGroup):
    field = State()
    value = State()


class WarnFlow(StatesGroup):
    reason = State()
