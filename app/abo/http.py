"""FastAPI entry for the ABO profile, with per-session catalogue state."""

from contextlib import asynccontextmanager
import os
from threading import RLock
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from app.abo.service import AboDialogue, bind_runtime


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=1600)
    session_id: str | None = Field(default=None, min_length=32, max_length=32)


def create_abo_app():
    sessions = {}
    lock = RLock()
    workspace = bind_runtime()

    @asynccontextmanager
    async def lifespan(_app):
        try:
            yield
        finally:
            for session in sessions.values():
                session.close()

    app = FastAPI(title="ABO Adaptive RAG", lifespan=lifespan)

    @app.get("/api/v1/health")
    def health():
        return {"status":"ready", "profile":"abo", "gpu":os.environ.get("ABO_GPU")=="1"}

    @app.post("/api/v1/chat")
    def chat(body: ChatRequest):
        if not body.question.strip():
            raise HTTPException(422, "请输入商品问题。")
        with lock:
            key = body.session_id or uuid4().hex
            if key not in sessions:
                if body.session_id:
                    raise HTTPException(404, "会话已失效，请新建会话。")
                if len(sessions) >= 32:
                    raise HTTPException(503, "会话数量达到上限。")
                output = workspace / "reports/ecommerce_rag" / ("abo-adaptive-api-"+key)
                output.mkdir(parents=True)
                sessions[key] = AboDialogue(output,gpu=os.environ.get("ABO_GPU")=="1",entry="HTTP")
            session = sessions[key]
            row = session.respond(body.question)
            if row["decision"]=="exit":
                sessions.pop(key).close()
        return {"session_id":key,"answer":row["display_answer"],"action":row["decision"],
                "sources":row["display_sources"],"citations":row["citations"],"strategy":row["adaptive"]["strategy"],
                "run_name":session.output.name,"question_id":row["question_id"],"issue_id":row.get("issue_id")}

    return app
