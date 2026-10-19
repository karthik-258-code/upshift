"""Dates and discounts."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from dateutil import parser
from dateutil.relativedelta import relativedelta

DISCOUNTS = {"WELCOME10": Decimal("0.10"), "VIP25": Decimal("0.25")}


def parse_date(value: str) -> date:
    return parser.parse(value).date()


def warranty_end(purchased: str, months: int = 24) -> date:
    return parse_date(purchased) + relativedelta(months=months)


def apply_discount(total: Decimal, code: str | None) -> Decimal:
    rate = DISCOUNTS.get((code or "").upper(), Decimal(0))
    return (total * (1 - rate)).quantize(Decimal("0.01"))
