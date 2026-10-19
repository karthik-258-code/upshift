# First fully autonomous upgrade: pydantic 1.10.13 -> 2.14.1

Run on 2026-10-19 against `demo-repos/pyshop-legacy` with `python -m upshift upgrade`.
All model calls on Nebius Token Factory; all test runs in the Docker sandbox on a laptop.

| Stage | Model | Result | Cost |
| --- | --- | --- | --- |
| Historian | Nemotron 3.5 Lightning | 10 relevant of 12 documented changes, `pydantic-settings` identified as a new dependency | $0.0036 |
| Version bump + verify | (deterministic) | 2 collection errors, every test masked | |
| Surgeon attempt 1 | Nemotron 3 Super | 8 edits: imports, `ConfigDict`, `field_validator`, `model_validator`, `model_dump` | $0.0038 |
| Surgeon attempt 2 | Nemotron 3 Super | no parsable edits (format slip), recorded and fed back | $0.0013 |
| Surgeon attempt 3 | Nemotron 3 Ultra (escalation) | 1 edit: suite collects again, 17 tests, 2 failing | $0.0042 |
| Surgeon attempt 4 | Nemotron 3 Ultra | 3 edits incl. `RootModel` for the custom root type: **17/17 passing** | $0.0066 |
| **Total** | | **green in 3 minutes** | **$0.0195** |

The one test change is the legitimate kind: a frozen model now raises `ValidationError`
instead of `TypeError` on assignment, which is documented pydantic 2 behaviour.

Known rough edges the Reviewer stage (Day 4) exists to catch: an unused import
(`StringConstraints`), an import placed mid-file, an empty `ConfigDict()`, and `pattern=`
where `Literal["physical"]` would be idiomatic.

## Diff produced by the agent

```diff
--- a/pyshop/models.py
+++ b/pyshop/models.py
@@ -1,58 +1,59 @@
-"""Domain models, written against pydantic 1.x."""
+"""Domain models, written against pydantic 2.x."""
 
 from __future__ import annotations
 
 from datetime import datetime, timezone
 from decimal import Decimal
 
-from pydantic import BaseModel, Field, root_validator, validator
-from pydantic.error_wrappers import ValidationError  # re-exported for callers
+from pydantic import BaseModel, ConfigDict, Field, model_validator, field_validator
+from pydantic import ValidationError  # re-exported for callers
+from pydantic.types import StringConstraints
 
 __all__ = ["Money", "Tags", "Product", "OrderLine", "Order", "ValidationError"]
 
 
 class Money(BaseModel):
+    model_config = ConfigDict(frozen=True)
+
     amount: Decimal = Field(..., ge=0)
-    currency: str = Field("USD", min_length=3, max_length=3)
+    currency: str = Field(default="USD", min_length=3, max_length=3)
 
-    class Config:
-        allow_mutation = False
-
-    @validator("currency")
+    @field_validator("currency")
     def upper_currency(cls, value: str) -> str:
         return value.upper()
 
 
-class Tags(BaseModel):
+from pydantic import RootModel
+
+class Tags(RootModel[list[str]]):
     """A bare list of tags, modelled as a custom root type."""
 
-    __root__: list[str]
-
     def __iter__(self):
-        return iter(self.__root__)
+        return iter(self.root)
 
     def __len__(self) -> int:
-        return len(self.__root__)
+        return len(self.root)
 
 
 class Product(BaseModel):
-    sku: str
+    model_config = ConfigDict(
+        from_attributes=True,
+        str_strip_whitespace=True,
+    )
+
+    sku: str = Field()
     name: str
     price: Money
-    tags: Tags = Field(default_factory=lambda: Tags(__root__=[]))
-    kind: str = Field("physical", const=True)
+    tags: Tags = Field(default_factory=lambda: Tags(root=[]))
+    kind: str = Field(default="physical", pattern=r'^physical$')
     created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
 
-    class Config:
-        orm_mode = True
-        anystr_strip_whitespace = True
-
-    @validator("sku", pre=True)
+    @field_validator("sku", mode='before')
     def normalise_sku(cls, value: object) -> str:
         return str(value).strip().upper()
 
     def to_payload(self) -> dict:
-        return self.dict()
+        return self.model_dump()
 
 
 class OrderLine(BaseModel):
@@ -65,16 +66,18 @@
 
 
 class Order(BaseModel):
+    model_config = ConfigDict()
+
     id: int
     customer_email: str
     lines: list[OrderLine]
     discount_code: str | None = None
 
-    @root_validator
-    def check_lines(cls, values: dict) -> dict:
-        if not values.get("lines"):
+    @model_validator(mode='after')
+    def check_lines(self) -> 'Order':
+        if not self.lines:
             raise ValueError("an order needs at least one line")
-        return values
+        return self
 
     @property
     def total(self) -> Decimal:
@@ -82,12 +85,12 @@
 
     @classmethod
     def from_payload(cls, payload: dict) -> Order:
-        return cls.parse_obj(payload)
+        return cls.model_validate(payload)
 
     def summary(self) -> dict:
         return {
             "id": self.id,
             "total": str(self.total),
             "lines": len(self.lines),
-            "fields": list(self.__fields__),
+            "fields": list(self.__class__.model_fields.keys()),
         }
--- a/pyshop/settings.py
+++ b/pyshop/settings.py
@@ -2,17 +2,17 @@
 
 from __future__ import annotations
 
-from pydantic import BaseSettings, Field
+from pydantic_settings import BaseSettings
+from pydantic import Field, ConfigDict
 
 
 class Settings(BaseSettings):
+    model_config = ConfigDict(env_prefix="PYSHOP_")
+
     database_url: str = "sqlite://"
     catalog_api_url: str = "https://api.example.com/catalog"
     request_timeout: float = Field(5.0, gt=0)
 
-    class Config:
-        env_prefix = "PYSHOP_"
-
 
 def get_settings() -> Settings:
     return Settings()
--- a/requirements.txt
+++ b/requirements.txt
@@ -1,4 +1,5 @@
-pydantic==1.10.13
+pydantic==2.14.1
 SQLAlchemy==1.4.49
 requests==2.28.2
 python-dateutil==2.8.2
+pydantic-settings==2.16.0
--- a/tests/test_models.py
+++ b/tests/test_models.py
@@ -3,6 +3,7 @@
 import pytest
 
 from pyshop.models import Money, Order, Product, ValidationError
+from pydantic import ValidationError as PydanticValidationError
 
 
 def make_product(**overrides) -> Product:
@@ -12,7 +13,7 @@
         "price": {"amount": "9.99", "currency": "usd"},
     }
     payload.update(overrides)
-    return Product.parse_obj(payload)
+    return Product.model_validate(payload)
 
 
 def test_sku_normalised_and_currency_upper():
@@ -24,7 +25,7 @@
 
 def test_money_is_immutable():
     money = Money(amount=Decimal("1.00"))
-    with pytest.raises(TypeError):
+    with pytest.raises(PydanticValidationError):
         money.amount = Decimal("2.00")
 
 
@@ -72,4 +73,4 @@
 
 def test_payload_roundtrip():
     product = make_product(tags=["x"])
-    assert Product.parse_obj(product.to_payload()) == product
+    assert Product.model_validate(product.to_payload()) == product
```
