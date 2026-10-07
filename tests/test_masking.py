"""Spec §18: relay masking — phones, @usernames, t.me links masked;
OTP/email allowed ONLY in handover mode."""
from bot.utils.masking import mask_text


def test_phone_masked_normal():
    for sample in ["call me 03001234567", "my number is +92 300 1234567",
                   "contact 0300-1234567 now"]:
        masked, offenses = mask_text(sample)
        assert "[hidden]" in masked, sample
        assert "phone" in offenses, sample


def test_username_masked():
    masked, offenses = mask_text("dm me @secretdealer")
    assert "[hidden]" in masked and "username" in offenses


def test_tme_link_masked():
    for sample in ["join t.me/secretgroup", "https://t.me/joinchat/abc"]:
        masked, offenses = mask_text(sample)
        assert "[hidden]" in masked, sample
        assert "link" in offenses, sample


def test_whatsapp_mention_masked():
    masked, offenses = mask_text("let's talk on WhatsApp")
    assert "[hidden]" in masked and "whatsapp" in offenses


def test_email_blocked_outside_handover():
    masked, offenses = mask_text("send to myemail@gmail.com please")
    assert "[hidden]" in masked and "email" in offenses


def test_email_allowed_in_handover():
    masked, offenses = mask_text("send to myemail@gmail.com please", handover=True)
    assert masked == "send to myemail@gmail.com please"
    assert not offenses


def test_otp_allowed_in_handover():
    masked, offenses = mask_text("the code is 482913", handover=True)
    assert masked == "the code is 482913"
    assert not offenses


def test_otp_blocked_outside_handover():
    masked, _ = mask_text("the code is 482913")
    assert "[hidden]" in masked


def test_phone_still_blocked_in_handover():
    masked, offenses = mask_text("call 03001234567, code 482913", handover=True)
    assert "03001234567" not in masked and "phone" in offenses
    assert "482913" in masked  # OTP survives


def test_username_still_blocked_in_handover():
    masked, offenses = mask_text("hi @secretdealer code 482913", handover=True)
    assert "[hidden]" in masked and "username" in offenses


def test_clean_message_untouched():
    text = "Is the account monetized? What is the average views per video?"
    masked, offenses = mask_text(text)
    assert masked == text and not offenses


def test_product_handle_allowed():
    # Discussing the FOR-SALE account handle is legitimate product talk,
    # but a bare @username still gets masked by design (anti-bypass wins).
    masked, offenses = mask_text("the TikTok account gets 50k views daily")
    assert masked == "the TikTok account gets 50k views daily"
    assert not offenses
