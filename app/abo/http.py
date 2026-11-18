"""FastAPI entry for the ABO profile, with per-session catalogue state."""

import json
import os
from contextlib import asynccontextmanager
from hashlib import sha256
from pathlib import Path
from threading import RLock
from time import time
from typing import Literal
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, SecretStr

from app.abo.service import bind_runtime
from app.abo.state import ThreadStore
from app.abo.web_catalog import WebCatalog
from app.abo.web_connection import ChatConnectionError, RemoteDialogue
from app.abo.web_setup import catalog_ready

WEB = Path(__file__).with_name("web")


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=1600)
    session_id: str | None = Field(default=None, min_length=32, max_length=32)
    thread_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")
    request_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")
    history: list[dict] = Field(default_factory=list, max_length=10000)
    mode: Literal["local", "kaggle"] = "kaggle"


class RemoteRequest(BaseModel):
    address: SecretStr


def web_sources(row, catalog=None):
    pages = {
        page["page_id"]: page for page in row.get("retrieved_pages", []) if page.get("page_id")
    }
    claims = {}
    for claim in row.get("claims", []):
        values = claim.get("values") or [claim.get("value")]
        claims.setdefault(claim.get("citation"), []).extend(
            str(value) for value in values if value is not None
        )
    sources = []
    for source in row.get("display_sources", []):
        citation = source["citation"]
        page = pages.get(citation, {})
        origin = page.get("source")
        origin = origin if isinstance(origin, dict) else {}
        identity = catalog.source_identity(citation) if catalog else {}
        sources.append(
            {
                **source,
                **identity,
                "choice": row.get("candidate_positions", {}).get(citation.split(":")[0]),
                "excerpt": " / ".join(claims.get(citation, []))[:650]
                or str(page.get("text") or "")[:650],
                "url": page.get("source_url"),
                "field": page.get("field") or page.get("section"),
                "member": origin.get("member"),
                "line": origin.get("line"),
            }
        )
        if identity:
            sources[-1]["excerpt"] = identity["excerpt"]
    return sources


def create_abo_app():
    sessions = {}
    lock = RLock()
    workspace = bind_runtime()
    catalog = workspace / "data/ecommerce_rag/abo-full-v1"
    browser_catalog = WebCatalog(catalog)
    store = ThreadStore(workspace / "reports/web-threads")
    thread_locks = {}

    def require_catalog():
        if not catalog_ready(catalog):
            raise HTTPException(503, "请运行项目根目录的 start-web.cmd 初始化本地商品目录。")

    @asynccontextmanager
    async def lifespan(_app):
        try:
            yield
        finally:
            for session in sessions.values():
                session.close()

    app = FastAPI(title="ABO Adaptive RAG", lifespan=lifespan)
    if WEB.is_dir():
        app.mount("/web-assets", StaticFiles(directory=WEB), name="abo-web")

        @app.get("/", include_in_schema=False)
        def index():
            version = sha256(
                (WEB / "chat.js").read_bytes() + (WEB / "styles.css").read_bytes()
            ).hexdigest()[:12]
            page = (
                (WEB / "index.html")
                .read_text("utf-8")
                .replace("/web-assets/chat.js", "/web-assets/chat.js?v=" + version)
            )
            page = page.replace("/web-assets/styles.css", "/web-assets/styles.css?v=" + version)
            return HTMLResponse(page, headers={"Cache-Control": "no-cache"})

    @app.get("/api/v1/health")
    def health():
        ready = catalog_ready(catalog)
        stats = {}
        if ready:
            manifest = json.loads((catalog / "manifest.json").read_text("utf-8"))
            stats = {
                "records": manifest["records"],
                "categories": len(manifest["product_types"]),
                "marketplaces": len(manifest["domains"]),
            }
        return {
            "status": "ready" if ready else "setup_required",
            "profile": "abo",
            "gpu": os.environ.get("ABO_GPU") == "1",
            "catalog": stats,
        }

    @app.get("/api/v1/catalog/categories")
    def categories():
        require_catalog()
        return {"items": browser_catalog.categories()}

    @app.get("/api/v1/catalog/categories/{category}/products")
    def category_products(
        category: str, after: int = Query(0, ge=0, le=147702), limit: int = Query(24, ge=1, le=60)
    ):
        require_catalog()
        info = next(
            (item for item in browser_catalog.categories() if item["key"] == category), None
        )
        if info is None:
            raise HTTPException(404, "没有找到这个类目。")
        return {"category": info, **browser_catalog.products(category, after, limit)}

    @app.get("/api/v1/catalog/products/{product_id}")
    def product(product_id: int):
        require_catalog()
        detail = browser_catalog.details(product_id)
        if detail is None:
            raise HTTPException(404, "没有找到这条商品资料。")
        return detail

    @app.post("/api/v1/remote")
    def remote(body: RemoteRequest):
        address = body.address.get_secret_value()
        if not 1 <= len(address) <= 4096:
            raise HTTPException(422, "请粘贴 Kaggle 的连接地址。")
        with lock:
            if len(sessions) >= 32:
                raise HTTPException(503, "会话数量达到上限。")
            key = uuid4().hex
            output = workspace / "reports/ecommerce_rag" / ("abo-adaptive-web-kaggle-" + key)
            output.mkdir(parents=True)
            try:
                sessions[key] = RemoteDialogue(output, address)
            except ChatConnectionError as error:
                raise HTTPException(502, str(error)) from None
            for old in [
                old
                for old, value in sessions.items()
                if old != key and isinstance(value, RemoteDialogue)
            ]:
                sessions.pop(old).close()
        return {"session_id": key, "mode": "kaggle"}

    @app.get("/api/v1/remote/status")
    def remote_status():
        with lock:
            keys = [
                key
                for key, session in sessions.items()
                if isinstance(session, RemoteDialogue) and not session.closed
            ]
        return {"session_id": keys[-1] if keys else None}

    @app.get("/api/v1/threads")
    def thread_history():
        items = store.history()
        if catalog_ready(catalog):
            for thread in items:
                for message in thread["messages"]:
                    for source in message.get("sources", []):
                        source.update(browser_catalog.source_identity(source.get("citation", "")))
        return {"items": items, "deleted": store.deleted()}

    @app.delete("/api/v1/threads/{identity}", status_code=204, response_class=Response)
    def delete_thread(identity: str):
        try:
            store.path(identity)
        except ValueError:
            raise HTTPException(422, "无效的对话编号。") from None
        with lock:
            conversation_lock = thread_locks.setdefault(identity, RLock())
        with conversation_lock:
            existed = not store.get(identity).get("deleted")
            store.delete(identity)
            if existed:
                from common import operation

                operation(
                    "删除网页历史会话",
                    "删除消息和恢复状态，保留无正文删除标记及独立审计日志",
                    "历史删除完成；Kaggle连接不受影响",
                    outputs=[str(store.path(identity))],
                    verification="旧请求返回410，其他对话不受影响",
                )

    @app.delete("/api/v1/chat/{session_id}", status_code=204, response_class=Response)
    def close_session(session_id: str):
        with lock:
            session = sessions.pop(session_id, None)
            if session is not None:
                session.close()

    @app.post("/api/v1/chat")
    def chat(body: ChatRequest):
        if not body.question.strip():
            raise HTTPException(422, "请输入商品问题。")
        identity = body.thread_id or body.session_id or uuid4().hex
        with lock:
            conversation_lock = thread_locks.setdefault(identity, RLock())
        with conversation_lock:
            return respond(body, identity)

    def respond(body, identity):
        previous = store.get(identity)
        if previous.get("deleted"):
            raise HTTPException(410, "这段对话已删除，请开始新对话。")
        if body.request_id and previous.get("request_id") == body.request_id:
            return previous["reply"]
        with lock:
            key = body.session_id or identity
            remote = isinstance(sessions.get(key), RemoteDialogue)
            if not remote or body.mode != "kaggle":
                raise HTTPException(
                    502, "请重新连接正在运行的 Kaggle 会话。历史对话和商品选择仍已保存。"
                )
            session = sessions[key]
        state = previous.get("state")
        if not state and body.history:
            state = legacy_context(body.history)
        try:
            row = session.respond(
                body.question, thread_id=identity, request_id=body.request_id, state=state
            )
            state = session.state
        except ChatConnectionError as error:
            raise HTTPException(502, str(error)) from None
        if row["decision"] == "exit":
            with lock:
                sessions.pop(key).close()
        products = browser_catalog.chat_cards(row) if catalog_ready(catalog) else []
        reply = {
            "session_id": key,
            "answer": row["display_answer"],
            "action": row["decision"],
            "products": products,
            "sources": web_sources(row, browser_catalog)
            if catalog_ready(catalog)
            else web_sources(row),
            "citations": row["citations"],
            "strategy": row["adaptive"]["strategy"],
            "run_name": session.output.name,
            "question_id": row["question_id"],
            "issue_id": row.get("issue_id"),
        }
        messages = previous.get("messages") or body.history
        messages = messages + [
            {"role": "user", "text": body.question},
            {
                "role": "assistant",
                "text": reply["answer"],
                "sources": reply["sources"],
                "products": products,
            },
        ]
        store.save(
            identity,
            {
                "id": identity,
                "mode": "kaggle",
                "state": state,
                "messages": messages,
                "updated": int(time() * 1000),
                "visible": bool(body.thread_id) or previous.get("visible", False),
                "request_id": body.request_id,
                "reply": reply,
            },
        )
        return reply

    def legacy_context(messages):
        # Migrate pre-upgrade browser histories. Text helps resolve intent; attributes are re-read.
        selected, candidates, users = [], [], []
        for message in messages:
            if message.get("role") == "user":
                users.append(str(message.get("text", ""))[:1600])
            if message.get("role") == "assistant" and message.get("products"):
                candidates = []
                for item in message["products"][:5]:
                    detail = (
                        browser_catalog.details(item["id"])
                        if isinstance(item.get("id"), int)
                        else None
                    )
                    if detail:
                        candidates.append(detail["key"])
        from full_reference import ordinal_choice

        for question in users[-3:]:
            chosen = ordinal_choice(question, {"lane": "product"})
            if chosen and not chosen.get("ambiguous") and 0 <= chosen["index"] < len(candidates):
                selected = [candidates[chosen["index"]]]
        return {
            "version": 1,
            "catalog": {
                "selected": selected,
                "candidates": candidates,
                "last_lane": "product",
                "last_task": {"lane": "product", "kind": "search_results"},
            },
            "last_lane": "product",
            "turn_memory": {
                "history": [{"user": question, "assistant": ""} for question in users[-6:]]
            },
        }

    return app
