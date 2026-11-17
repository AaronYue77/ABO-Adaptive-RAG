"""LangGraph agent layer."""

__all__ = ["AgentState", "build_graph"]


def __getattr__(name):
    # The ABO application profile does not need to import the order-tool stack.
    if name == "build_graph":
        from app.agents.graph import build_graph

        return build_graph
    if name == "AgentState":
        from app.agents.state import AgentState

        return AgentState
    raise AttributeError(name)
