from datetime import date
from decimal import Decimal

from pyshop.pricing import apply_discount, parse_date, warranty_end


def test_parse_date_accepts_common_formats():
    assert parse_date("2024-01-31") == date(2024, 1, 31)
    assert parse_date("31 Jan 2024") == date(2024, 1, 31)


def test_warranty_end_clamps_month_end():
    assert warranty_end("2024-01-31", months=1) == date(2024, 2, 29)
    assert warranty_end("2023-03-15") == date(2025, 3, 15)


def test_apply_discount():
    assert apply_discount(Decimal("100"), "vip25") == Decimal("75.00")
    assert apply_discount(Decimal("10.00"), "WELCOME10") == Decimal("9.00")
    assert apply_discount(Decimal("10.00"), None) == Decimal("10.00")
    assert apply_discount(Decimal("10.00"), "bogus") == Decimal("10.00")
