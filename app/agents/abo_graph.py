"""Adaptive-rag graph profile for read-only ABO evidence and accessory checks."""

from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from app.core.enums import RetrievalStrategy


class AboTurn(TypedDict, total=False):
    question: str
    strategy: RetrievalStrategy
    routing_reason: str
    result: dict[str, Any]


def build_abo_graph(dialogue):
    """Use the same classify/conditional-dispatch structure as the upstream graph."""
    graph = StateGraph(AboTurn)
    graph.add_node("route_complexity", dialogue.route)
    handlers = {
        RetrievalStrategy.DIRECT: ("direct_or_unavailable", dialogue.execute),
        RetrievalStrategy.SINGLE: ("one_listing_or_catalog", dialogue.execute),
        RetrievalStrategy.MULTI: ("compare_independent_evidence", dialogue.execute),
        RetrievalStrategy.AGENT: ("check_accessory_compatibility", dialogue.check_compatibility),
        RetrievalStrategy.REFUSE: ("explain_service_scope", dialogue.execute),
    }
    for name, handler in handlers.values():
        graph.add_node(name, handler)
        graph.add_edge(name, END)
    graph.add_edge(START, "route_complexity")
    graph.add_conditional_edges(
        "route_complexity",
        lambda state: state["strategy"].value,
        {strategy.value: handler[0] for strategy, handler in handlers.items()},
    )
    return graph.compile()
