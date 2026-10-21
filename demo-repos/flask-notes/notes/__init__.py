"""A tiny notes API, written against Flask 2.0."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from flask import Flask, abort, escape, jsonify, request
from flask.json import JSONEncoder
from werkzeug.urls import url_quote

from notes.storage import NoteStore


class DecimalJSONEncoder(JSONEncoder):
    """Prices are Decimals; serialise them as strings so no precision is lost."""

    def default(self, o):
        if isinstance(o, Decimal):
            return str(o)
        return super().default(o)


def create_app(store: NoteStore | None = None) -> Flask:
    app = Flask(__name__)
    app.json_encoder = DecimalJSONEncoder
    app.config["JSON_SORT_KEYS"] = False  # keep insertion order: id, title, price
    app.config["WARMED_UP"] = False
    notes = store or NoteStore()

    @app.before_first_request
    def warm_up() -> None:
        app.config["WARMED_UP"] = True

    @app.post("/notes")
    def create_note():
        payload = request.get_json(force=True, silent=True) or {}
        title = str(payload.get("title", "")).strip()
        if not title:
            abort(400, description="title is required")
        try:
            price = Decimal(str(payload.get("price", "0")))
        except InvalidOperation:
            abort(400, description="price must be a number")
        note = notes.add(title=title, price=price)
        return jsonify(note.as_dict()), 201

    @app.get("/notes")
    def list_notes():
        return jsonify([note.as_dict() for note in notes.all()])

    @app.get("/notes/<int:note_id>")
    def get_note(note_id: int):
        note = notes.get(note_id)
        if note is None:
            abort(404, description="no such note")
        return jsonify(note.as_dict())

    @app.get("/search")
    def search():
        query = request.args.get("q", "")
        return jsonify({
            "query": str(escape(query)),
            "results": [note.as_dict() for note in notes.search(query)],
        })

    @app.get("/link/<path:title>")
    def link(title: str):
        return jsonify({"slug": url_quote(title)})

    @app.errorhandler(400)
    @app.errorhandler(404)
    def as_json(error):
        return jsonify({"error": error.description}), error.code

    return app
