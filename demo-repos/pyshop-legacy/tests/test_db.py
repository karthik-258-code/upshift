from pyshop.db import Catalog


def test_catalog_roundtrip():
    catalog = Catalog()
    first = catalog.add("AB-1", "Widget", 999)
    catalog.add("ZZ-9", "Gadget", 150)

    assert catalog.count() == 2
    assert catalog.by_sku("AB-1").name == "Widget"
    assert catalog.by_sku("nope") is None
    assert catalog.all_skus() == ["AB-1", "ZZ-9"]
    assert catalog.get(first).sku == "AB-1"
    assert catalog.get(999) is None


def test_empty_catalog():
    assert Catalog().count() == 0
    assert Catalog().all_skus() == []
