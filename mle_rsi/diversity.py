"""Opt-in experiment: conservative code deduplication and explicit batch roles."""
import ast
import hashlib
import json


def source_fingerprint(source):
    # AST preserves literal/parameter changes and docstrings; only formatting and
    # comments disappear. Invalid Python is left to normal evaluation diagnostics.
    try:
        normalized = ast.dump(ast.parse(source), include_attributes=False)
    except SyntaxError:
        normalized = source
    return hashlib.sha256(normalized.encode()).hexdigest()


def record_prediction_fingerprints(store, attempt_id, fingerprints):
    """Durable post-execution observation, never rejection or a hidden retry."""
    matches={}
    for kind, fingerprint in fingerprints.items():
        key=kind+':'+fingerprint
        previous=store.get('prediction_fingerprint',key)
        if previous is None:
            store.put('prediction_fingerprint',key,{'attempt_id':attempt_id})
        elif previous['attempt_id'] != attempt_id:
            matches[kind]=previous['attempt_id']
    return matches


class NoveltyGate:
    def __init__(self, store):
        self.store = store

    def claim(self, artifact, attempt_id):
        key = source_fingerprint(artifact.source)
        old = self.store.get('novelty_claim', key)
        if old is not None and old['attempt_id'] != attempt_id:
            return old['attempt_id']
        # No await between checking and recording: concurrent async generations
        # cannot both claim the same source. Claims survive controller recovery.
        self.store.put('novelty_claim', key, {'attempt_id': attempt_id,
                       'source_hash': artifact.source_hash})
        return None


def candidate_role(stage, attempt_id):
    if stage.value == 'debug':
        return 'repair'
    position = int(attempt_id.rsplit(':a', 1)[1])
    return 'explore' if position == 3 else 'hypothesis' if position == 4 else 'improve'


class DiversityPrompts:
    def __init__(self, wrapped):
        self.wrapped = wrapped

    def build(self, task, stage, parent, history, attempt_id=None):
        messages = self.wrapped.build(task, stage, parent, history, attempt_id)
        role = candidate_role(stage, attempt_id)
        directions = {
            'improve': 'Improve a measured promising approach. Identify one substantive prediction-affecting change and evidence for it.',
            'explore': 'Reserved exploration slot: test a materially different model family, feature representation, or training objective from the parent and dominant recent approach. Rewriting, renaming, or only retuning ensemble weights does not satisfy this slot. Choose the method yourself using public evidence and available resources.',
            'hypothesis': 'Reserved hypothesis-testing slot: identify an untested explanation of the current validation bottleneck. Implement a controlled change or ablation that tests it. State what result would support or refute the hypothesis; do not assert unmeasured gains.',
            'repair': 'Repair the concrete reported failure. Preserve a useful intended experiment rather than merely renaming an already successful solver.',
        }
        instruction = ('BATCH EXPERIMENT ROLE: '+role+'\n'+directions[role]+
            '\nIn your existing Change/Evidence/Expected effect sections state the tested hypothesis, '
            'the closest previous experiment, and the substantive difference. Preserve the fixed split. '
            'Exact code and AST duplicates are rejected before training, with at most one additional '
            'generation attempt. All requests count against the API cap. Do not evade deduplication '
            'through irrelevant edits, comments, names, or dead code. A parameter change is allowed '
            'when it tests a justified hypothesis.')
        return [*messages, {'role': 'user', 'content': instruction}]


def audit(store):
    events = [row for _, row in store.list('novelty_rejection')]
    nodes = [node for _, node in store.list('node')]
    return {'candidate_slots': len(nodes), 'duplicate_responses': len(events),
            'duplicates_skipped_training': sum(bool(n.evaluation.diagnostics.get('duplicate_code_skipped')) for n in nodes),
            'unique_code_ast': len({source_fingerprint(n.artifact.source) for n in nodes if n.artifact.source}),
            'valid_candidates': sum(n.evaluation.valid for n in nodes),
            'roles': {role: sum(candidate_role(n.attempt.stage, n.attempt.attempt_id) == role for n in nodes)
                      for role in ('improve','explore','hypothesis','repair')}}
