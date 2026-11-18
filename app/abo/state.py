"""Portable conversation state. Product facts are always read again from ABO."""

import json
import re
from copy import deepcopy
from pathlib import Path
from threading import RLock
from time import time

CATALOG_FIELDS = (
    "selected",
    "candidates",
    "general_history",
    "last_lane",
    "last_product_query",
    "last_task",
    "recent_tasks",
    "overview_shown",
    "catalog_offset",
    "selected_entities",
    "candidate_entities",
)


def empty_state():
    return {
        "version": 1,
        "catalog": {
            "selected": [],
            "candidates": [],
            "general_history": [],
            "last_lane": None,
            "last_product_query": None,
            "last_task": None,
            "recent_tasks": [],
            "overview_shown": {},
            "catalog_offset": 0,
            "selected_entities": [],
            "candidate_entities": [],
        },
        "last_lane": None,
        "device_state": None,
        "pending": False,
        "turn_memory": {},
    }


def clear_state(dialogue, reason):
    import_state(dialogue, empty_state())
    dialogue.journal.event("context_clear", "完成", reason)


def export_state(dialogue):
    engine = dialogue.engine
    return deepcopy(
        {
            "version": 1,
            "catalog": {key: getattr(engine.catalog, key) for key in CATALOG_FIELDS},
            "last_lane": engine.last_lane,
            "device_state": engine.device_state,
            "pending": engine.pending,
            "turn_memory": getattr(dialogue, "turn_memory", {}),
        }
    )


def import_state(dialogue, state):
    """Restore references and user requirements, never cached answers as evidence."""
    if not isinstance(state, dict) or state.get("version") != 1:
        return
    engine = dialogue.engine
    for key in CATALOG_FIELDS:
        if key in state.get("catalog", {}):
            setattr(engine.catalog, key, deepcopy(state["catalog"][key]))
    engine.catalog.ended = False
    # Rebuild display identities from the current source, rather than trusting saved attributes.
    from full_app import public_item

    for key, entities in (("selected", "selected_entities"), ("candidates", "candidate_entities")):
        valid = []
        for identity in getattr(engine.catalog, key)[:50]:
            if isinstance(identity, str) and "@" in identity:
                rows = engine.app.retriever.get(*identity.split("@", 1))
                if rows:
                    valid.append(rows[0])
        setattr(engine.catalog, key, [row["listing_key"] for row in valid])
        setattr(engine.catalog, entities, [public_item(row) for row in valid])
    engine.last_lane = state.get("last_lane")
    engine.device_state = deepcopy(state.get("device_state"))
    engine.pending = bool(state.get("pending"))
    dialogue.turn_memory = deepcopy(state.get("turn_memory") or {})


class ThreadStore:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = RLock()

    def path(self, identity):
        if not re.fullmatch(r"[a-f0-9]{32}", identity):
            raise ValueError("Invalid conversation identity")
        return self.root / (identity + ".json")

    def get(self, identity):
        with self.lock:
            path = self.path(identity)
            return json.loads(path.read_text("utf-8")) if path.is_file() else {}

    def save(self, identity, value):
        with self.lock:
            path = self.path(identity)
            pending = path.with_suffix(".pending")
            pending.write_text(json.dumps(value, ensure_ascii=False) + "\n", "utf-8")
            pending.replace(path)

    def history(self):
        rows = []
        with self.lock:
            for path in self.root.glob("*.json"):
                item = json.loads(path.read_text("utf-8"))
                if item.get("messages") and item.get("visible", True):
                    rows.append(
                        {key: item.get(key) for key in ("id", "mode", "updated", "messages")}
                    )
        return sorted(rows, key=lambda row: row.get("updated") or 0, reverse=True)

    def deleted(self):
        with self.lock:
            return [
                path.stem
                for path in self.root.glob("*.json")
                if json.loads(path.read_text("utf-8")).get("deleted")
            ]

    def delete(self, identity):
        # Retain only an identity tombstone, preventing stale browser copies from resurrecting it.
        self.save(identity, {"id": identity, "deleted": True, "updated": int(time() * 1000)})
