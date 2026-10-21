"""In-memory note storage. Nothing here depends on Flask."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from itertools import count


@dataclass
class Note:
    id: int
    title: str
    price: Decimal

    def as_dict(self) -> dict:
        return {"id": self.id, "title": self.title, "price": self.price}


class NoteStore:
    def __init__(self) -> None:
        self._notes: dict[int, Note] = {}
        self._ids = count(1)

    def add(self, title: str, price: Decimal) -> Note:
        note = Note(next(self._ids), title, price)
        self._notes[note.id] = note
        return note

    def get(self, note_id: int) -> Note | None:
        return self._notes.get(note_id)

    def all(self) -> list[Note]:
        return list(self._notes.values())

    def search(self, query: str) -> list[Note]:
        needle = query.lower()
        return [note for note in self._notes.values() if needle in note.title.lower()]
