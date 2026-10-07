"""Spec §18: commission math incl. price <= commission edge."""
from bot.utils.money import commission_for, fmt_cents, parse_usd, release_amount


def test_flat_commission():
    assert commission_for(10000, 200) == 200
    assert commission_for(5000, 300) == 300


def test_release_amount_normal():
    assert release_amount(10000, 200) == 9800
    assert release_amount(250, 200) == 50


def test_release_amount_edge_price_lte_commission():
    # price == commission -> admin review (None), never $0 release
    assert release_amount(200, 200) is None
    # price < commission -> admin review, never negative release
    assert release_amount(100, 200) is None
    assert release_amount(0, 200) is None


def test_fmt_cents():
    assert fmt_cents(10000) == "$100.00"
    assert fmt_cents(199) == "$1.99"
    assert fmt_cents(0) == "$0.00"


def test_parse_usd():
    assert parse_usd("150") == 15000
    assert parse_usd("$150.50") == 15050
    assert parse_usd("0") is None
    assert parse_usd("-5") is None
    assert parse_usd("abc") is None
