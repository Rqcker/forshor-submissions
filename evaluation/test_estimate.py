import copy
import json
import tempfile
import unittest
from pathlib import Path

from estimate import ROOT, generate


class ExistingModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.state = json.loads((ROOT / 'fixtures/baseline-state.json').read_text())
        cls.temp = tempfile.TemporaryDirectory()
        cls.archive = Path(cls.temp.name)
        (cls.archive / 'leaderboard').mkdir()
        (cls.archive / 'leaderboard/results.json').write_text(json.dumps(cls.state['archive']))
        (cls.archive / 'results').mkdir()
        (cls.archive / 'results/policies').symlink_to(ROOT / 'estimator/examples/sto_archive', target_is_directory=True)
        cls.result = generate(cls.archive, cls.state)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_every_published_resource_estimate_and_breakdown_is_reproduced(self):
        for board in ('t', 'gates'):
            expected = {p['topPolicyFull'] + ':' + str(p['topK']): p for p in self.state['legacy']['boards'][board]}
            actual = {p['topPolicyFull'] + ':' + str(p['topK']): p for p in self.result['legacy']['boards'][board]}
            self.assertEqual(actual.keys(), expected.keys())
            for key in expected:
                a, e = copy.deepcopy(actual[key]), copy.deepcopy(expected[key])
                a.pop('rank'); e.pop('rank')
                self.assertEqual(a, e, (board, key))

    def test_winners_and_individual_champions_are_preserved(self):
        json.dumps(self.result)  # The real publisher input must be serializable.
        self.assertEqual(self.result['legacy']['best'], self.state['legacy']['best'])
        self.assertEqual(self.result['legacy']['tables'], self.state['legacy']['tables'])
        self.assertEqual(self.result['legacy']['history'], self.state['legacy']['history'])

    def test_a_stale_archive_cannot_remove_accepted_submissions(self):
        state = copy.deepcopy(self.state)
        state['archive']['policies'].append({'policy_id': 'a' * 40})
        with self.assertRaisesRegex(ValueError, 'drop an accepted'):
            generate(self.archive, state)


if __name__ == '__main__':
    unittest.main()
