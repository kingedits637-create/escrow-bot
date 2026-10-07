"""All user-facing text in one place."""
from __future__ import annotations

MAIN_MENU = (
    "🛡️ <b>Escrow Marketplace</b>\n\n"
    "Buy and sell monetized social accounts safely.\n"
    "Money is held in escrow — never pay a stranger directly.\n\n"
    "Choose an option:"
)

HELP_RULES = (
    "📖 <b>How it works</b>\n\n"
    "1️⃣ <b>SELL</b> — list your account. Our team reviews it, then it goes live.\n"
    "2️⃣ <b>BUY</b> — browse verified listings, tap Buy Now.\n"
    "3️⃣ <b>Deal chat</b> — talk to the other side anonymously through the bot. "
    "Phone numbers, usernames and links are masked automatically.\n"
    "4️⃣ <b>Pay inside the bot</b> — money goes to escrow, never to the seller directly.\n"
    "5️⃣ <b>Delivery</b> — credentials are released from the encrypted vault.\n"
    "6️⃣ <b>8h handover</b> — exchange emails/OTPs to secure the account.\n"
    "7️⃣ <b>24h hold</b> — verify the account. Confirm receipt any time, or the "
    "money auto-releases to the seller after 24h (minus the platform fee).\n\n"
    "⚠️ <b>Rules</b>\n"
    "• Never try to move the deal outside the bot — masked messages are logged.\n"
    "• The admin's decision in disputes is final.\n"
    "• No refunds after release, except proven fraud within 7 days.\n"
    "• Past violations/strikes are <i>declared by the seller</i> — no platform "
    "exposes strike history, so always check the proof video yourself."
)

BUY_CATEGORIES = "🛒 <b>Buy</b> — choose a category:"
BUDGET_PROMPT = "💰 Filter by budget (optional):"
NO_LISTINGS = "No live listings in this category yet. Check back soon!"
SELL_CATEGORIES = "📦 <b>Sell</b> — choose the account's category:"
SUBCATEGORY_PROMPT = "Choose the account type (or Custom to type your own):"
SUBCATEGORY_CUSTOM_PROMPT = "✍️ Type the account type (e.g. 'TikTok Agency Account'):"

SELL_TITLE = "📝 Listing title (e.g. 'TikTok Shop 120K followers, USA'):"
SELL_PRICE = "💵 Price in USD (e.g. 150 or 150.00):"
SELL_FOLLOWERS = "👥 Follower / subscriber count (numbers only, e.g. 120000):"
SELL_COUNTRY = "🌍 Country of the account's audience (e.g. USA):"
SELL_AGE = "📅 Account age (e.g. '2 years'):"
SELL_DESC = "🧾 Short description (niche, earnings, what's included):"

VER_EMAIL = "✉️ Do you still have access to the ORIGINAL email of this account?"
VER_VIOL = "🚩 Has this account ever had violations/strikes? (Declared by you — answer honestly.)"
VER_VIOL_DTL = "✍️ Give brief details of the violations/strikes:"
VER_MONETIZED = "💲 Is the account currently monetized / earning?"
VER_VIDEO = (
    "🎥 Upload a SCREEN RECORDING of the account backend / analytics "
    "(this is required proof — buyers will see that proof exists)."
)
VER_SHOTS = (
    "🖼️ Now send screenshots as proof (analytics, monetization page). "
    "Send one or more photos, then tap ✅ Done."
)
VER_CREDENTIALS = (
    "🔐 Now send the account CREDENTIALS (login email/username + password, "
    "any 2FA notes).\n\n"
    "They are encrypted immediately and stored in the vault. The buyer sees "
    "them only AFTER payment is confirmed."
)
PAYOUT_METHOD = "💳 Where should you be paid after a sale?"
PAYOUT_DETAILS = "✍️ Send your payout details (e.g. Binance UID or email, Easypaisa/JazzCash number, IBAN):"

YES_NO_KB = [("✅ Yes", "yes"), ("❌ No", "no")]

ENGAGEMENT_FLAG_NOTE = (
    "\n\n⚠️ <i>Auto-check: the follower/view numbers look unusual — "
    "an admin will spot-check this listing.</i>"
)

LISTING_SUBMITTED = (
    "✅ Listing submitted for review!\n\n"
    "An admin will approve it shortly. You'll be notified when it goes live."
)

DEAL_OPENED_BUYER = (
    "💬 <b>Deal chat opened</b> (Deal #{deal_id})\n\n"
    "You are talking to the <b>seller</b> — anonymously. They can't see your name.\n"
    "Type your message and it will be relayed. Tap <b>Buy Now</b> when ready to pay."
)
DEAL_OPENED_SELLER = (
    "🔔 <b>A buyer is interested in your listing:</b> {title}\n\n"
    "Deal #{deal_id} — chat anonymously through the bot. "
    "Phone numbers, @usernames and links are masked."
)

PAY_CHOOSE = "💳 Choose a payment method. Money goes to <b>escrow</b>, not to the seller."
PAY_BINANCE_MANUAL = (
    "🟡 <b>Pay with Binance (USDT)</b>\n\n"
    "Send exactly <b>{amount}</b> USDT to the escrow wallet:\n"
    "<code>{address}</code>\n\n"
    "Then send your <b>Binance Pay ID</b> here (the numbers from your payment)."
)
PAY_EASYPAISA = (
    "🟢 <b>Pay with {method}</b>\n\n"
    "Send exactly <b>{amount}</b> to:\n<code>{address}</code>\n\n"
    "Then upload a <b>screenshot</b> of your payment here. An admin will confirm it."
)

MASK_WARNING = (
    "⚠️ That message contained contact info ({kinds}) which was hidden from the "
    "other side. Keep all communication inside the bot — repeat attempts are "
    "reported to the admin."
)
MASK_ADMIN_ALERT = (
    "🚨 <b>Bypass attempt</b> — Deal #{deal_id}, user {user_id} ({role})\n"
    "Masked: {kinds}\nOriginal (for review):\n<code>{original}</code>"
)

DELIVERED_BUYER = (
    "🎉 <b>Payment confirmed — credentials delivered!</b>\n\n"
    "<code>{creds}</code>\n\n"
    "⏳ <b>Handover mode (8h):</b> you may now exchange <b>emails and OTP codes</b> "
    "with the seller to secure the account. Phones/usernames/links stay blocked.\n"
    "⏳ <b>Hold (24h):</b> verify everything. Tap <b>Confirm Receipt</b> any time, "
    "or funds auto-release after 24h."
)
DELIVERED_SELLER = (
    "✅ Buyer paid — credentials were auto-delivered from the vault.\n\n"
    "⏳ Handover mode (8h) is open: you may exchange emails/OTP codes. "
    "After 24h hold the funds release to you (minus fee)."
)

HANDOVER_LOCKED = (
    "🔒 Handover window ended — this deal chat is now read-only. "
    "The 24h hold continues; the buyer can still confirm receipt."
)

RELEASED_SELLER = (
    "💰 <b>Funds released!</b> Deal #{deal_id}\n"
    "Amount: <b>{amount}</b> (fee {fee} deducted)\n"
    "Payout goes to your saved {method} details. An admin will process it shortly."
)
RELEASED_BUYER = "✅ Deal #{deal_id} completed — funds released to the seller. Please rate your experience!"

REFUNDED_BUYER = "↩️ Deal #{deal_id} refunded: <b>{amount}</b> ({reason}). It will return via your original payment method."
REFUNDED_SELLER = "↩️ Deal #{deal_id} was refunded to the buyer ({reason})."

DISPUTE_OPENED = "🚩 Dispute opened on deal #{deal_id}. The 24h timer is paused. An admin will review the transcript and proofs."
DISPUTE_ADMIN = "🚩 <b>Dispute</b> — Deal #{deal_id}\nReason: {reason}\n\nReview the transcript, then decide."

RATE_PROMPT = "⭐ Rate deal #{deal_id} — how was the <b>{other}</b>? Tap stars (1–5):"
RATE_COMMENT = "💬 Optional: add a short comment (or tap Skip)."
RATE_DONE = "Thanks! Your rating was recorded."

ADMIN_MENU = "🛠️ <b>Admin panel</b> — choose:"
NOT_ADMIN = "⛔ This section is for admins only."

# ------------------------------------------------------- auto mode (v2)

LISTING_AUTO_LIVE = (
    "✅ <b>Your listing is LIVE!</b>\n\n"
    "Buyers can see it right now — no admin review needed."
)

PAY_AUTO_VERIFIED = (
    "⚡ <b>Payment auto-verified!</b> Your deposit was detected. "
    "Delivering your credentials…"
)

PAY_NOT_DETECTED_BUYER = (
    "⏳ <b>Payment not detected yet</b> — Deal #{deal_id}\n\n"
    "We couldn't find your deposit automatically. It may still be "
    "confirming on-chain, or the amount/network may differ.\n\n"
    "👉 Send your <b>Pay ID / TXID</b> (or a screenshot) here and an "
    "admin will confirm it manually."
)
