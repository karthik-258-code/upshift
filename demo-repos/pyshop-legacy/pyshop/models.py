"""Domain models, written against pydantic 1.x."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from pydantic import BaseModel, Field, root_validator, validator
from pydantic.error_wrappers import ValidationError  # re-exported for callers

__all__ = ["Money", "Tags", "Product", "OrderLine", "Order", "ValidationError"]


class Money(BaseModel):
    amount: Decimal = Field(..., ge=0)
    currency: str = Field("USD", min_length=3, max_length=3)

    class Config:
        allow_mutation = False

    @validator("currency")
    def upper_currency(cls, value: str) -> str:
        return value.upper()


class Tags(BaseModel):
    """A bare list of tags, modelled as a custom root type."""

    __root__: list[str]

    def __iter__(self):
        return iter(self.__root__)

    def __len__(self) -> int:
        return len(self.__root__)


class Product(BaseModel):
    sku: str
    name: str
    price: Money
    tags: Tags = Field(default_factory=lambda: Tags(__root__=[]))
    kind: str = Field("physical", const=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    class Config:
        orm_mode = True
        anystr_strip_whitespace = True

    @validator("sku", pre=True)
    def normalise_sku(cls, value: object) -> str:
        return str(value).strip().upper()

    def to_payload(self) -> dict:
        return self.dict()


class OrderLine(BaseModel):
    product: Product
    quantity: int = Field(..., gt=0)

    @property
    def total(self) -> Decimal:
        return self.product.price.amount * self.quantity


class Order(BaseModel):
    id: int
    customer_email: str
    lines: list[OrderLine]
    discount_code: str | None = None

    @root_validator
    def check_lines(cls, values: dict) -> dict:
        if not values.get("lines"):
            raise ValueError("an order needs at least one line")
        return values

    @property
    def total(self) -> Decimal:
        return sum((line.total for line in self.lines), Decimal(0))

    @classmethod
    def from_payload(cls, payload: dict) -> Order:
        return cls.parse_obj(payload)

    def summary(self) -> dict:
        return {
            "id": self.id,
            "total": str(self.total),
            "lines": len(self.lines),
            "fields": list(self.__fields__),
        }
