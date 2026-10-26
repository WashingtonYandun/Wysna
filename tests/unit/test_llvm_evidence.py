"""Reject partial or stale LLVM evidence instead of publishing a qualified decision."""
from copy import deepcopy
import json
from pathlib import Path
import unittest
from tests.engine_harness import corpus_paths
from tools.benchmark_llvm import require_qualification, decision, ROOT


class LlvmEvidenceTests(unittest.TestCase):
    def qualification(self):
        positives, negatives = corpus_paths()
        rows = [{'case': path.relative_to(ROOT).as_posix(), 'profile': profile, 'status': 'passed'}
                for path in positives for profile in ('dev', 'release', 'size')]
        rows.extend({'case': path.relative_to(ROOT).as_posix(), 'kind': 'source-rejection', 'status': 'passed'}
                    for path in negatives)
        # A synthetic contract fixture, never persisted or used as measured evidence.
        return {'schema': 'wysna.llvm-qualification/1', 'provenance': {'fixture': True},
                'profiles': ['dev', 'release', 'size'], 'rows': len(rows), 'results': rows,
                'passed': len(rows), 'failed': 0, 'unsupported': 0, 'source_unchanged': True,
                'positive_cases': len(positives), 'negative_cases': len(negatives)}

    def test_missing_duplicate_and_failed_cases_cannot_claim_qualification(self):
        report = self.qualification()
        require_qualification(report, {'fixture': True}, ['dev', 'release', 'size'])
        for mutate in (lambda rows: rows.pop(), lambda rows: rows.append(deepcopy(rows[0])),
                       lambda rows: rows[0].update(status='failed')):
            broken = deepcopy(report)
            mutate(broken['results'])
            with self.assertRaises(ValueError):
                require_qualification(broken, {'fixture': True}, ['release'])

    def test_stale_provenance_and_unqualified_profile_are_rejected(self):
        report = self.qualification()
        with self.assertRaises(ValueError):
            require_qualification(report, {'fixture': False}, ['release'])
        report['profiles'] = ['dev']
        with self.assertRaises(ValueError):
            require_qualification(report, {'fixture': True}, ['release'])

    def test_budget_regressions_are_retained_without_changing_the_default(self):
        policy = json.loads((ROOT / 'bench/llvm_policy.json').read_text())
        row = {'case': 'contract-fixture', 'profile': 'release',
               'ratios': {metric: limit + 1 for metric, limit in policy['limits'].items()}}
        result = decision([row], policy)
        self.assertEqual(result['backend_default'], 'c99')
        self.assertFalse(result['automatic_promotion'])
        self.assertEqual(len(result['budget_violations']), len(policy['limits']))


if __name__ == '__main__':
    unittest.main()
