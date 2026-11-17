"""Bind the Adaptive-rag graph to the scoped ABO catalog, Qwen and accessory roles."""

import os
from pathlib import Path
import sys
from threading import RLock

from app.agents.abo_graph import build_abo_graph
from app.abo.presentation import present
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
    def __init__(self, output, *, gpu=False, model_dir=None):
        bind_runtime()
        from d_chat import StageDChat

        self.engine = StageDChat(output, gpu=gpu, model_dir=model_dir)
        self.output = self.engine.output
        self.app = self.engine.app
        self._lock = RLock()
        self.graph = build_abo_graph(self)

    def route(self, state):
        from d_chat import compatibility_request
        from full_conversation import direct_business_request, price_request, scope_route
        from full_evidence import comparison_axis, fields_alongside_price, plan_field_lookups
        from full_retrieval import exact_skus

        question = state["question"].strip()
        session = self.engine.catalog
        identifiers = exact_skus(question)
        last_lane = self.engine.last_lane
        selected = session.last_lane=="product" and bool(session.selected)
        context = bool(identifiers or selected or session.candidates)
        scoped = scope_route(question, last_lane, bool(identifiers))
        if scoped:
            strategy = RetrievalStrategy.REFUSE if scoped["intent"] == "out_of_scope" else RetrievalStrategy.DIRECT
            reason = scoped["reason"]
        elif price_request(question, context) or direct_business_request(question):
            has_fields = fields_alongside_price(question)
            strategy = RetrievalStrategy.MULTI if has_fields else RetrievalStrategy.DIRECT
            reason = "readable_fields_and_unavailable_prices" if has_fields else "unavailable_business_data"
        elif compatibility_request(question, pending=self.engine.pending,
                                   recent=last_lane == "compatibility", selected=selected):
            strategy, reason = RetrievalStrategy.AGENT, "read_only_accessory_roles"
        elif len(identifiers) > 1 or comparison_axis(question) or plan_field_lookups(question):
            strategy, reason = RetrievalStrategy.MULTI, "independent_source_fields_or_comparison"
        else:
            strategy, reason = RetrievalStrategy.SINGLE, "catalogue_or_listing_evidence"
        return {"strategy": strategy, "routing_reason": reason}

    def _execute(self, state, *, compatibility):
        return {"result": self.engine.respond(state["question"], allow_compatibility=compatibility,
                                               presenter=lambda row: present(row,self.app.retriever), adaptive={
            "strategy": state["strategy"].value, "reason": state["routing_reason"],
        })}

    def execute(self, state):
        return self._execute(state,compatibility=False)

    def check_compatibility(self, state):
        return self._execute(state,compatibility=True)

    def respond(self, text):
        if not isinstance(text, str) or not 0 < len(text.strip()) <= 1600:
            raise ValueError("请输入 1 到 1600 个字符。")
        with self._lock:
            return self.graph.invoke({"question": text})["result"]

    def close(self):
        with self._lock:
            self.engine.close()
