"""Bind the Adaptive-rag graph to the scoped ABO catalog, Qwen and accessory roles."""

import os
import sys
from pathlib import Path
from threading import RLock
from time import perf_counter

from app.abo.presentation import present
from app.agents.abo_graph import build_abo_graph
from app.core.enums import RetrievalStrategy


def bind_runtime():
    default = Path(__file__).resolve().parents[2] / "abo_runtime"
    workspace = Path(os.environ.setdefault("DOC_VQA_WORKSPACE", str(default)))
    upstream = workspace / "upstream/VisRAG/personal"
    for name in ("ecommerce_rag", "rag_common", "visrag_repro"):
        path = str(upstream / name)
        if path not in sys.path:
            sys.path.insert(0, path)
    return workspace


class AboDialogue:
    def __init__(self, output, *, gpu=False, visual=False, model_dir=None, entry="程序调用"):
        bind_runtime()
        from d_chat import StageDChat

        from app.abo.journal import SessionJournal

        self.output = output
        self._lock = RLock()
        self.journal = SessionJournal(output, entry)
        try:
            if gpu:
                from app.abo.dialogue_adapter import prepare as prepare_dialogue_adapter

                prepare_dialogue_adapter()
            package = Path(__file__).resolve().parent
            extra = [
                *package.glob("*.py"),
                package.parent / "agents/abo_graph.py",
                package.parent / "core/enums.py",
            ]
            self.engine = StageDChat(
                output, gpu=gpu, visual=visual, model_dir=model_dir, application_sources=extra
            )
            self.app = self.engine.app
            self.graph = build_abo_graph(self)
            self.turn_memory = {}
            if gpu:
                self.journal.event(
                    "model_load", "开始", "在就绪前加载一个8B底座和已验收的适配器，后续问答复用。"
                )
                runtime = self.app.reader.ensure_reader().vlm
                self.app.reader.adapter.mount(runtime)
                from app.abo.dialogue_adapter import generation_context

                with generation_context(runtime, "adaptive_turn_plan"):
                    pass
                self.journal.event(
                    "model_load", "完成", "8B底座已加载，图文与对话适配器按任务切换。"
                )
            profile = "Qwen GPU" if gpu else "本地索引"
            if visual:
                profile += " + VisRAG-Ret"
            self.journal.ready(profile, self.engine.config_sha)
        except Exception as error:
            self.journal.failed("初始化", error)
            if getattr(self, "engine", None) is not None:
                try:
                    self.engine.close()
                except Exception:
                    pass
            self.journal.close()
            raise

    def route(self, state):
        from d_chat import compatibility_request
        from full_conversation import direct_business_request, price_request, scope_route
        from full_evidence import comparison_axis, fields_alongside_price, plan_field_lookups
        from full_retrieval import exact_skus

        question = state["question"].strip()
        session = self.engine.catalog
        identifiers = exact_skus(question)
        last_lane = self.engine.last_lane
        selected = session.last_lane == "product" and bool(session.selected)
        context = bool(identifiers or selected or session.candidates)
        scoped = scope_route(question, last_lane, bool(identifiers))
        if scoped:
            strategy = (
                RetrievalStrategy.REFUSE
                if scoped["intent"] == "out_of_scope"
                else RetrievalStrategy.DIRECT
            )
            reason = scoped["reason"]
        elif price_request(question, context) or direct_business_request(question):
            has_fields = fields_alongside_price(question)
            strategy = RetrievalStrategy.MULTI if has_fields else RetrievalStrategy.DIRECT
            reason = (
                "readable_fields_and_unavailable_prices"
                if has_fields
                else "unavailable_business_data"
            )
        elif compatibility_request(
            question,
            pending=self.engine.pending,
            recent=last_lane == "compatibility",
            selected=selected,
        ):
            strategy, reason = RetrievalStrategy.AGENT, "read_only_accessory_roles"
        elif len(identifiers) > 1 or comparison_axis(question) or plan_field_lookups(question):
            strategy, reason = RetrievalStrategy.MULTI, "independent_source_fields_or_comparison"
        else:
            strategy, reason = RetrievalStrategy.SINGLE, "catalogue_or_listing_evidence"
        return {"strategy": strategy, "routing_reason": reason}

    def _execute(self, state, *, compatibility):
        return {
            "result": self.engine.respond(
                state["question"],
                allow_compatibility=compatibility,
                presenter=self._present,
                adaptive={
                    "strategy": state["strategy"].value,
                    "reason": state["routing_reason"],
                },
            )
        }

    def _present(self, row):
        display = present(row, self.app.retriever)
        problem = self.journal.attach_issue(row)
        if problem:
            display["issue_id"] = problem
        return display

    def execute(self, state):
        return self._execute(state, compatibility=False)

    def check_compatibility(self, state):
        return self._execute(state, compatibility=True)

    def respond(self, text):
        if not isinstance(text, str) or not 0 < len(text.strip()) <= 1600:
            raise ValueError("请输入 1 到 1600 个字符。")
        with self._lock:
            if self.journal.closed:
                raise RuntimeError("会话已结束。")
            try:
                started = perf_counter()
                from app.abo.turns import AdaptiveTurn

                result = AdaptiveTurn(self).run(text)
                if result is None:
                    result = self.graph.invoke({"question": text})["result"]
                if any(call.get("model_executed") for call in result.get("model_calls", [])):
                    result["model"] = self.app.reader.model_name
                    result["model_revision"] = self.app.reader.revision
                    result["generation_seconds"] = sum(
                        call.get("generation_seconds", 0) for call in result["model_calls"]
                    )
                result["total_seconds"] = perf_counter() - started
            except Exception as error:
                self.journal.failed("对话请求", error, text)
                raise
            self.journal.turn(result)
            return result

    def close(self):
        with self._lock:
            if not self.journal.closed:
                try:
                    self.engine.close()
                except Exception as error:
                    self.journal.failed("关闭", error)
                    raise
                finally:
                    self.journal.close()
