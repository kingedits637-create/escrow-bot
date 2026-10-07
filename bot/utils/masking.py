"""Smart masking engine for the anonymous deal chat (spec §7).

Normal mode: masks phone numbers, @usernames, t.me/telegram.me links,
email addresses, WhatsApp mentions and short numeric codes (OTPs —
sharing codes outside the handover window is a bypass attempt).
Handover mode (8h window after delivery): email addresses and short
numeric OTP codes (4-8 digits) are ALLOWED; phones/usernames/links/
WhatsApp mentions stay blocked.

mask_text() -> (masked_text, offenses) where offenses is a list of tags:
"phone" | "username" | "link" | "email" | "whatsapp" | "otp".
"""
from __future__ import annotations

import re

HIDDEN = "[hidden]"

# Long digit runs: 9+ digit characters (separators allowed) -> phone number.
_PHONE_RE = re.compile(r"\+?[\d][\d\s\-.]{7,}\d")
# Short standalone digit runs 4-8 -> OTP-style code.
_SHORT_CODE_RE = re.compile(r"(?<!\d)\d{4,8}(?!\d)")
# Telegram usernames: @name (5+ chars to avoid false positives like "@me").
_USERNAME_RE = re.compile(r"@[\w]{5,}")
_LINK_RE = re.compile(r"(?:https?://)?(?:t\.me|telegram\.me)/\S+", re.IGNORECASE)
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
_WHATSAPP_RE = re.compile(r"whats\s?app", re.IGNORECASE)

# Placeholder used to protect allowed emails from the username masker.
_EMAIL_TOKEN = "\x00EMAIL{idx}\x00"


def _digits_len(match: str) -> int:
    return sum(ch.isdigit() for ch in match)


def mask_text(text: str, handover: bool = False) -> tuple[str, list[str]]:
    offenses: list[str] = []
    out = text

    # 0) In handover mode, stash allowed emails behind placeholders so the
    #    @username masker below cannot eat "@gmail" out of "a@gmail.com".
    emails: list[str] = []
    if handover:
        def _stash(m: re.Match) -> str:
            emails.append(m.group(0))
            return _EMAIL_TOKEN.format(idx=len(emails) - 1)
        out = _EMAIL_RE.sub(_stash, out)

    # 1) links (always blocked)
    if _LINK_RE.search(out):
        offenses.append("link")
        out = _LINK_RE.sub(HIDDEN, out)

    # 2) emails — blocked outside handover, allowed (stashed) inside
    if not handover and _EMAIL_RE.search(out):
        offenses.append("email")
        out = _EMAIL_RE.sub(HIDDEN, out)

    # 3) @usernames (always blocked)
    if _USERNAME_RE.search(out):
        offenses.append("username")
        out = _USERNAME_RE.sub(HIDDEN, out)

    # 4) WhatsApp mentions (always blocked)
    if _WHATSAPP_RE.search(out):
        offenses.append("whatsapp")
        out = _WHATSAPP_RE.sub(HIDDEN, out)

    # 5) phone numbers — always blocked (9+ digit runs)
    def _phone_sub(m: re.Match) -> str:
        return HIDDEN if _digits_len(m.group(0)) >= 9 else m.group(0)

    new_out = _PHONE_RE.sub(_phone_sub, out)
    if HIDDEN in new_out and HIDDEN not in out:
        offenses.append("phone")
    out = new_out

    # 6) short numeric codes — OTP-style. Allowed in handover, blocked outside
    #    (sharing a code outside the handover window is a bypass attempt).
    if _SHORT_CODE_RE.search(out):
        if handover:
            pass  # allowed — leave untouched
        else:
            offenses.append("otp")
            out = _SHORT_CODE_RE.sub(HIDDEN, out)

    # 7) restore stashed handover emails
    for i, email in enumerate(emails):
        out = out.replace(_EMAIL_TOKEN.format(idx=i), email)

    return out, sorted(set(offenses))


def is_handover_allowed_sample(text: str) -> bool:
    """Helper used in tests: sample must pass through UNCHANGED in handover mode."""
    masked, offenses = mask_text(text, handover=True)
    return masked == text and not offenses
