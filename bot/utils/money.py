"""Money helpers. All amounts are integer USD cents — never floats."""
from __future__ import annotations


def fmt_cents(cents: int) -> str:
    """12345 -> '$123.45' ; negative handled."""
    sign = "-" if cents < 0 else ""
    c = abs(int(cents))
    return f"{sign}${c // 100}.{c % 100:02d}"


def parse_usd(text: str) -> int | None:
    """'49.99' / '$49.99' / '50' -> 4999 / 4999 / 5000. None if invalid."""
    t = text.strip().replace("$", "").replace(",", "")
    try:
        value = round(float(t) * 100)
    except ValueError:
        return None
    return value if value > 0 else None


def commission_for(price_cents: int, commission_cents: int) -> int:
    """Flat commission per completed deal (spec §11)."""
    return max(0, int(commission_cents))


def release_amount(price_cents: int, commission_cents: int) -> int | None:
    """Amount the seller receives. Returns None when price <= commission —
    the spec requires this edge to go to ADMIN REVIEW instead of
    auto-release (releasing $0 or a negative amount would be wrong)."""
    net = int(price_cents) - commission_for(price_cents, commission_cents)
    return net if net > 0 else None


# ------------------------------------------------------------ PKR (local)

def fmt_pkr(pkr: int) -> str:
    """28000 -> 'Rs 28,000'."""
    return f"Rs {int(pkr):,}"


def usd_cents_to_pkr(price_cents: int, usd_pkr_rate: float) -> int:
    """Convert a USD-cent listing price to whole PKR, rounded."""
    return int(round(int(price_cents) / 100 * float(usd_pkr_rate)))


def release_amount_pkr(payable_pkr: int, commission_pkr: int) -> int | None:
    """PKR version of release_amount: None when payable <= commission —
    routes to ADMIN REVIEW, never a $0/negative release."""
    net = int(payable_pkr) - max(0, int(commission_pkr))
    return net if net > 0 else None
