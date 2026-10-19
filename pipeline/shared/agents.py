"""Strict, user-editable model settings and the shared worker admission cap.

Validation here checks configuration syntax, not launcher availability. The
launcher must reject unsupported model/effort combinations before execution.
The live max_parallel value governs admission; model settings can be pinned in
an operation's historical processing policy by the caller.
"""
import json
import re
from pathlib import Path

MODEL_RE = re.compile(r'[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}\Z')
REASONING_EFFORTS = frozenset({'low', 'medium', 'high', 'xhigh', 'max', 'ultra'})


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate agents JSON key: ' + key)
        result[key] = value
    return result


def load_agents(root):
    """Load required settings relative to the ai-platform-feedreader repository root."""
    path = Path(root) / 'pipeline/config/agents.json'
    try:
        config = json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=_unique_object)
    except (OSError, ValueError) as exc:
        raise ValueError('Cannot load agents configuration: ' + str(exc)) from exc
    return validate_agents(config)


def validate_agents(config):
    """Return the validated configuration; never silently supply defaults."""
    if (not isinstance(config, dict)
            or set(config) != {'schema_version', 'classify', 'summary', 'max_parallel'}
            or type(config['schema_version']) is not int
            or config['schema_version'] != 1):
        raise ValueError('Invalid agents configuration schema')
    for stage in ('classify', 'summary'):
        settings = config[stage]
        if not isinstance(settings, dict) or set(settings) != {'model', 'reasoning_effort'}:
            raise ValueError('Invalid ' + stage + ' agent settings schema')
        model = settings['model']
        if not isinstance(model, str) or not MODEL_RE.fullmatch(model):
            raise ValueError(stage + ' model must be a model identifier (1–128 characters)')
        effort = settings['reasoning_effort']
        if not isinstance(effort, str) or effort not in REASONING_EFFORTS:
            raise ValueError('Invalid ' + stage + ' reasoning effort')
    if type(config['max_parallel']) is not int or config['max_parallel'] < 1:
        raise ValueError('max_parallel must be a positive integer')
    return config
