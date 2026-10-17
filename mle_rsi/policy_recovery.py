"""Audited host fallback after policy failure; no model requests are involved."""
from dream_rsi.policy_runtime import PolicyWorker
from dream_rsi.errors import PolicyError
from dream_rsi.models import BatchDecision
from dream_rsi.tree import BatchValidator
from dataclasses import replace
import json


def stagnation_draft(decision, view, window=50):
    nodes=list(view.revealed_nodes)
    if len(nodes)<=window or 'r' not in view.eligible_parent_ids or not decision.parent_ids:
        return decision
    def best(rows):
        return max((float(n.get('score') or 0) for n in rows if n.get('valid')),default=0.)
    if best(nodes[-window:])<=best(nodes[:-window])+1e-12:
        return replace(decision,parent_ids=(*decision.parent_ids[:-1],'r'),
                       reason=decision.reason+'; stagnation: one fresh draft after 50 candidates without public gain')
    return decision


class RecoveringPolicyWorker(PolicyWorker):
    def __init__(self, source, limits, *, store, tree_id):
        super().__init__(source,limits)
        self.store,self.tree_id=store,tree_id
        self.failure=None
        self.index=0
        # Read only the round journal, not all historical sources/arrays again.
        encoded = store._encoded_record('tree', tree_id)
        while isinstance(encoded, dict) and '$ref' in encoded:
            encoded = json.loads(store.artifacts.get(encoded['$ref']))
        self.committed_steps = store.codec.decode(encoded['rounds']) if encoded and 'rounds' in encoded else ()

    async def open(self):
        try:await super().open()
        except PolicyError as exc:self.failure=str(exc)

    async def decide(self, view, state=None):
        key=f'{self.tree_id}:{view.round_index}'
        self.index+=1
        historical = next((step for step in self.committed_steps if step.round_index == view.round_index), None)
        def replay_committed():
            decision = BatchDecision(historical.parent_ids, {}, historical.reason, host_guarded=True)
            BatchValidator.validate(decision, view)
            self.store.put('policy_replay_recovery', key, {'error': self.failure,
                'decision': decision, 'rule': 'Replay immutable committed parents after policy process failure; future batches use fallback'})
            return decision
        saved=self.store.get('policy_fallback',key)
        if saved:
            self.failure=saved['error']
            if historical is not None:
                return replay_committed()
            decision=saved['decision']
            BatchValidator.validate(decision,view)
            return decision
        if self.failure is None:
            try:
                decision=stagnation_draft(await super().decide(view,state),view)
                BatchValidator.validate(decision,view)
                return decision
            except PolicyError as exc:self.failure=str(exc)
        if historical is not None:
            return replay_committed()
        nodes={n['node_id']:n for n in view.revealed_nodes}
        eligible=list(view.eligible_parent_ids)
        ranked=sorted(eligible,key=lambda k:(-float(nodes.get(k,{}).get('score') or 0),k))
        if not ranked:raise PolicyError('Fallback has no eligible parents')
        # Same finite, deterministic host rule for replay and fresh decisions.
        width=min(view.max_parallelism,view.remaining_nodes if view.remaining_nodes is not None else view.max_parallelism)
        parents=tuple(ranked[i%len(ranked)] for i in range(width)) if view.parent_repeats_allowed else tuple(ranked[:width])
        decision=BatchDecision(parents,{},'Audited best-public-score fallback after policy process failure')
        BatchValidator.validate(decision,view)
        self.store.put('policy_fallback',key,{'error':self.failure,'decision':decision,
            'rule':'best public score, deterministic ID tie break, cyclic width'})
        return decision
