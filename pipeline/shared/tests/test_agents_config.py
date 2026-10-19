"""Agent settings fail closed without a fixed model allowlist or fallback."""
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

from pipeline.shared.agents import load_agents, validate_agents

ROOT = Path(__file__).resolve().parents[3]


class AgentsConfigTests(unittest.TestCase):
    def setUp(self):
        self.config = {
            'schema_version': 1,
            'classify': {'model': 'gpt-6.1-sol', 'reasoning_effort': 'high'},
            'summary': {'model': 'gpt-6.1-sol', 'reasoning_effort': 'high'},
            'max_parallel': 6,
        }

    def test_checked_in_defaults(self):
        self.assertEqual(load_agents(ROOT), self.config)

    def test_custom_models_efforts_and_shared_cap(self):
        for effort in ('low', 'medium', 'high', 'xhigh', 'max', 'ultra'):
            config = copy.deepcopy(self.config)
            config['classify'] = {'model': 'vendor/custom-model-2030:preview', 'reasoning_effort': effort}
            config['summary']['model'] = 'future_model.v7'
            config['max_parallel'] = 23
            self.assertEqual(validate_agents(config), config)

    def test_required_keys_and_no_extra_keys(self):
        for key in self.config:
            config = copy.deepcopy(self.config)
            del config[key]
            with self.subTest(missing=key), self.assertRaises(ValueError):
                validate_agents(config)
        config = {**self.config, 'extra': True}
        with self.assertRaises(ValueError):
            validate_agents(config)
        for stage in ('classify', 'summary'):
            for key in ('model', 'reasoning_effort'):
                config = copy.deepcopy(self.config)
                del config[stage][key]
                with self.subTest(stage=stage, missing=key), self.assertRaises(ValueError):
                    validate_agents(config)
            config = copy.deepcopy(self.config)
            config[stage]['max_parallel'] = 1
            with self.assertRaises(ValueError):
                validate_agents(config)

    def test_malformed_values(self):
        for value in (None, [], '', 1, True):
            with self.subTest(root=value), self.assertRaises(ValueError):
                validate_agents(value)
        for value in (True, False, 1.0, '1', 0, 2, None):
            with self.subTest(version=value), self.assertRaises(ValueError):
                validate_agents({**self.config, 'schema_version': value})
        for value in (True, False, 0, -1, 1.5, '6', None, [], float('inf')):
            with self.subTest(cap=value), self.assertRaises(ValueError):
                validate_agents({**self.config, 'max_parallel': value})
        for stage in ('classify', 'summary'):
            for value in (None, [], '', True):
                with self.subTest(stage=stage, settings=value), self.assertRaises(ValueError):
                    validate_agents({**self.config, stage: value})
            for field, values in {
                'model': (None, [], True, '', ' ', ' gpt-6.1-sol', 'gpt-6.1-sol\n', '{model}', 'a;echo', 'a' * 129),
                'reasoning_effort': (None, [], True, '', 'inherit', 'HIGH', 'default', 'none'),
            }.items():
                for value in values:
                    config = copy.deepcopy(self.config)
                    config[stage][field] = value
                    with self.subTest(stage=stage, field=field, value=value), self.assertRaises(ValueError):
                        validate_agents(config)

    def test_missing_malformed_and_duplicate_json_fail_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = root / 'pipeline/config/agents.json'
            with self.assertRaises(ValueError):
                load_agents(root)
            path.parent.mkdir(parents=True)
            valid = json.dumps(self.config)
            invalid = (
                '{',
                valid.replace('"schema_version": 1', '"schema_version": 1, "schema_version": 1'),
                valid.replace('"model": "gpt-6.1-sol"', '"model": "gpt-6.1-sol", "model": "another"', 1),
                valid.replace('"max_parallel": 6', '"max_parallel": NaN'),
            )
            for text in invalid:
                path.write_text(text, encoding='utf-8')
                with self.subTest(text=text), self.assertRaises(ValueError):
                    load_agents(root)
            path.write_text(valid, encoding='utf-8')
            self.assertEqual(load_agents(root), self.config)


if __name__ == '__main__':
    unittest.main()
