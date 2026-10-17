"""Static resource declarations and host checks for the next experiment stage."""
import ast


def resource_request(source, task, spec):
    if not spec.get('stage2', {}).get('candidate_resources'):
        return task in spec['execution']['gpu_tasks']
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False  # existing source preflight reports the syntax error
    declarations = [n.value for n in tree.body if isinstance(n, ast.Assign)
                    and any(isinstance(t, ast.Name) and t.id == 'MLE_RESOURCE' for t in n.targets)]
    if len(declarations) != 1:
        raise ValueError('Candidate must declare exactly one literal MLE_RESOURCE = "cpu" or "gpu"')
    value = ast.literal_eval(declarations[0])
    if value not in ('cpu', 'gpu'):
        raise ValueError('Invalid MLE_RESOURCE declaration')
    return value == 'gpu'


def validate_training_contract(value):
    if not isinstance(value, dict) or not value.get('members'):
        raise ValueError('Missing training-contract members')
    for member in value['members']:
        if not member.get('name') or not member.get('validation') or member.get('validation') != member.get('final'):
            raise ValueError('Validation/final model configuration differs or is missing')
    return value


class StagePrompts:
    def __init__(self, wrapped, spec):
        self.wrapped, self.spec = wrapped, spec

    def build(self, *args, **kwargs):
        messages = self.wrapped.build(*args, **kwargs)
        return [*messages, {'role':'user', 'content':'''NEW STAGE CONTRACT:
Declare exactly one top-level literal MLE_RESOURCE = "cpu" or "gpu". Lexical,
TF-IDF and linear CPU work requests cpu; BGE, torch transformers and GPU neural
training request gpu. A GPU request receives an exclusive GPU; do not silently
substitute CPU for an unavailable GPU.
Keep validation and submission model families, weights, iterations and ensemble
members identical. Reserve time for final training and inference before fitting.
Use mle_runtime.record_training_contract(members), with each member a dict of
name, validation and final; the latter two are identical configuration dicts.
Record actual executed settings, not desired settings. Never silently reduce
final boosting iterations. Complete fewer identical members when time is tight.
Use mle_runtime.commit_outputs() only after ALL four required output files and
the training contract are complete. It atomically preserves a complete bundle;
partial files are never accepted as a valid timeout result.
For fixed pretrained features you may use deterministic feature caching through
mle_runtime.feature_cache_get/put with complete data, split, weights and
preprocessing hashes. Never cache stochastic augmentation or finetuned features.
Keep the existing validation split. An independent selection-confirmation split
is a separate retraining experiment; never mix scores from different splits.
When public gains stagnate, try a materially different model or representation;
report the algorithm, features and hypothesis instead of only a new branch name.
'''}]
