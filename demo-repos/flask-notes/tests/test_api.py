import json


def test_create_serialises_decimal_price(client):
    response = client.post("/notes", json={"title": "Milk", "price": "9.99"})
    assert response.status_code == 201
    assert response.get_json() == {"id": 1, "title": "Milk", "price": "9.99"}


def test_keys_keep_insertion_order(client):
    client.post("/notes", json={"title": "Milk", "price": "9.99"})
    response = client.get("/notes/1")
    assert list(json.loads(response.data).keys()) == ["id", "title", "price"]


def test_missing_title_is_json_400(client):
    response = client.post("/notes", json={"price": "1"})
    assert response.status_code == 400
    assert response.get_json() == {"error": "title is required"}


def test_bad_price_is_json_400(client):
    response = client.post("/notes", json={"title": "Milk", "price": "lots"})
    assert response.status_code == 400
    assert response.get_json() == {"error": "price must be a number"}


def test_unknown_note_is_json_404(client):
    response = client.get("/notes/99")
    assert response.status_code == 404
    assert response.get_json() == {"error": "no such note"}


def test_list_returns_all_notes(client):
    client.post("/notes", json={"title": "Milk", "price": "9.99"})
    client.post("/notes", json={"title": "Bread", "price": "2.50"})
    assert [n["title"] for n in client.get("/notes").get_json()] == ["Milk", "Bread"]


def test_search_escapes_query_and_finds_notes(client):
    client.post("/notes", json={"title": "Milk", "price": "9.99"})
    client.post("/notes", json={"title": "Bread", "price": "2.50"})
    response = client.get("/search?q=<b>mi</b>")
    body = response.get_json()
    assert body["query"] == "&lt;b&gt;mi&lt;/b&gt;"
    assert body["results"] == []
    assert [n["title"] for n in client.get("/search?q=MI").get_json()["results"]] == ["Milk"]


def test_link_slug_is_url_quoted(client):
    response = client.get("/link/hello world & more")
    assert response.get_json() == {"slug": "hello%20world%20%26%20more"}


def test_warm_up_runs_before_first_request(app, client):
    assert app.config["WARMED_UP"] is False
    client.get("/notes")
    assert app.config["WARMED_UP"] is True
