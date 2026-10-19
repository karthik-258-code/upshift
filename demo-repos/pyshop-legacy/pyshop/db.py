"""Persistence layer, written against SQLAlchemy 1.4."""

from __future__ import annotations

from sqlalchemy import Column, Integer, String, create_engine, select
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

Base = declarative_base()


class ProductRow(Base):
    __tablename__ = "products"

    id = Column(Integer, primary_key=True)
    sku = Column(String(32), unique=True, nullable=False)
    name = Column(String(200), nullable=False)
    price_cents = Column(Integer, nullable=False)


class Catalog:
    def __init__(self, url: str = "sqlite://") -> None:
        self.engine = create_engine(url)
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

    def add(self, sku: str, name: str, price_cents: int) -> int:
        session = self.Session()
        try:
            row = ProductRow(sku=sku, name=name, price_cents=price_cents)
            session.add(row)
            session.commit()
            return row.id
        finally:
            session.close()

    def count(self) -> int:
        return self.engine.execute("SELECT COUNT(*) FROM products").scalar()

    def by_sku(self, sku: str) -> ProductRow | None:
        session = self.Session()
        try:
            return session.query(ProductRow).filter_by(sku=sku).first()
        finally:
            session.close()

    def all_skus(self) -> list[str]:
        with self.engine.connect() as conn:
            rows = conn.execute(select([ProductRow.sku]).order_by(ProductRow.sku))
            return [row[0] for row in rows]

    def get(self, product_id: int) -> ProductRow | None:
        session = self.Session()
        try:
            return session.query(ProductRow).get(product_id)
        finally:
            session.close()
