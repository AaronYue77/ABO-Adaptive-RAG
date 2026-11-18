"""Idempotent browser RPC, sharing one GPU model across independent conversations."""

from hashlib import sha256
from threading import RLock

from app.abo.state import ThreadStore, empty_state, export_state, import_state

_lock = RLock()


def cloud_request(chat, payload):
    from common import WORKSPACE

    identity, request_id = payload["thread_id"], payload["request_id"]
    store = ThreadStore(WORKSPACE / "reports/web-cloud-threads")
    requests = ThreadStore(WORKSPACE / "reports/web-cloud-requests")
    cache_id = sha256((identity + ":" + request_id).encode()).hexdigest()[:32]
    with _lock:
        cached = requests.get(cache_id)
        if cached:
            return cached["reply"]
        previous = store.get(identity)
        # Retrying after the websocket lost a completed answer must not run the turn twice.
        if previous.get("request_id") == request_id:
            return previous["reply"]
        chat.journal.event(
            "thread_resume",
            "恢复",
            f"线程 {identity}，请求 {request_id}；从引用和需求恢复，事实重新取证。",
        )
        state = payload.get("state") or previous.get("state")
        # The local client snapshot is authoritative when returning to an older conversation.
        if state:
            import_state(chat, state)
        else:
            import_state(chat, empty_state())
        row = chat.respond(payload["question"])
        row["thread_id"] = identity
        reply = {"row": row, "state": export_state(chat)}
        store.save(identity, {"request_id": request_id, "state": reply["state"], "reply": reply})
        requests.save(cache_id, {"reply": reply})
        return reply
