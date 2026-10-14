"""A bounded repair entrypoint for the existing best-first search.

One failed program may receive one child. It consumes an ordinary candidate
slot; no additional API call, hidden retry or training budget is introduced.
"""
from dream_rsi.policy import best_first_policy_source


def repair_best_first_source(width=2):
    source=best_first_policy_source(width=width)
    # Keep a single Policy class so the normal policy source validator applies.
    source=source.replace('    def decide(self, view, state):','    def _best_first(self, view, state):')
    return source+'''
    def decide(self, view, state):
        decision = self._best_first(view, state)
        parents = list(decision["parent_ids"])
        if not parents:
            return decision
        legal = set(view["eligible_parent_ids"])
        nodes = list(view["revealed_nodes"])
        used = {n["parent_id"] for n in nodes if n["parent_id"] is not None}
        repairable = [n for n in nodes if n["node_id"] in legal
                      and n["parent_id"] is not None and not n["valid"]
                      and n.get("stage") != "debug"
                      and n["node_id"] not in used
                      and n["fail_class"] in ("runtime_error", "timeout", "source_error", "output_contract_error")]
        if repairable:
            parents[0] = repairable[-1]["node_id"]
            decision["parent_ids"] = parents
            decision["reason"] = "One budgeted repair of an unexpanded failed candidate; other slots keep best-first"
        return decision
'''
