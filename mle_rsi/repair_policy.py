"""Generated policy uses the exact same bounded repair selection as the host."""
import inspect
import textwrap
from dream_rsi.policy import best_first_policy_source
from dream_rsi.repair_guard import repair_parent_ids


def repair_best_first_source(width=2):
    source = best_first_policy_source(width=width, exploration_slots=1 if width >= 3 else 0)
    source = source.replace('    def decide(self, view, state):', '    def _best_first(self, view, state):')
    helper = inspect.getsource(repair_parent_ids).replace('def repair_parent_ids(nodes, eligible, slots):',
                                                       'def _repairs(self, nodes, eligible, slots):')
    return source + '\n' + textwrap.indent(helper, '    ') + '''
    def decide(self, view, state):
        decision = self._best_first(view, state)
        parents = list(decision["parent_ids"])
        repairs = self._repairs(view["revealed_nodes"], view["eligible_parent_ids"], len(parents))
        parents[:len(repairs)] = repairs
        decision["parent_ids"] = parents
        if repairs:
            decision["reason"] += "; bounded repairs: " + ",".join(repairs)
        return decision
'''
