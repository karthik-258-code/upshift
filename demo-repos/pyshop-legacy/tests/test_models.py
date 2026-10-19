from decimal import Decimal

import pytest

from pyshop.models import Money, Order, Product, ValidationError


def make_product(**overrides) -> Product:
    payload = {
        "sku": " ab-1 ",
        "name": "Widget",
        "price": {"amount": "9.99", "currency": "usd"},
    }
    payload.update(overrides)
    return Product.parse_obj(payload)


def test_sku_normalised_and_currency_upper():
    product = make_product()
    assert product.sku == "AB-1"
    assert product.price.currency == "USD"
    assert product.kind == "physical"


def test_money_is_immutable():
    money = Money(amount=Decimal("1.00"))
    with pytest.raises(TypeError):
        money.amount = Decimal("2.00")


def test_tags_root_model():
    product = make_product(tags=["sale", "new"])
    assert list(product.tags) == ["sale", "new"]
    assert len(product.tags) == 2


def test_const_kind_rejected():
    with pytest.raises(ValidationError):
        make_product(kind="digital")


def test_negative_price_rejected():
    with pytest.raises(ValidationError):
        make_product(price={"amount": "-1"})


def test_order_requires_lines():
    with pytest.raises(ValidationError):
        Order.from_payload({"id": 1, "customer_email": "a@b.c", "lines": []})


def test_order_total_and_summary():
    order = Order.from_payload(
        {
            "id": 7,
            "customer_email": "a@b.c",
            "lines": [
                {"product": make_product().to_payload(), "quantity": 2},
                {
                    "product": make_product(sku="zz-9", price={"amount": "0.01"}).to_payload(),
                    "quantity": 1,
                },
            ],
        }
    )
    assert order.total == Decimal("19.99")
    summary = order.summary()
    assert summary["lines"] == 2
    assert summary["total"] == "19.99"
    assert "customer_email" in summary["fields"]


def test_payload_roundtrip():
    product = make_product(tags=["x"])
    assert Product.parse_obj(product.to_payload()) == product
