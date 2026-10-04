"""Submission aliases must preserve identity, evidence, and reference closure."""

from copy import deepcopy
import unittest

from rent_rules.submission_ids import extend_registry, export_with_short_ids, validate_registry


class SubmissionIdTests(unittest.TestCase):
    def fixture(self):
        rules = [
            {"team_rule_id": "r-bbbb", "overrides": ["r-aaaa"],
             "interaction": "r-bbbb supersedes r-aaaa; not prefix-r-aaaa or r-aaaa-suffix.",
             "interactions": [{"from_rule_id": "r-bbbb", "to_rule_id": "r-aaaa"}],
             "historical_interactions": [{"from_rule_id": "r-retired", "to_rule_id": "r-bbbb"}],
             "quoted_span": "Exact source text r-aaaa.",
             "consolidation": {"member_rule_ids": ["r-retired"]}},
            {"team_rule_id": "r-aaaa", "overrides": []},
        ]
        lookups = {"as_of": "2026-10-01", "lookups": {"A0001": [
            {"team_rule_id": "r-bbbb", "result": "unknown", "conflict_flag": True,
             "explanation": "Missing case.r-bbbb.event_date."}]}}
        return rules, lookups

    def test_initial_assignment_does_not_depend_on_input_order(self):
        rules, _ = self.fixture()
        a = extend_registry(rules)
        self.assertEqual(a, extend_registry(list(reversed(rules))))
        self.assertEqual(a['internal_to_submission'], {'r-aaaa': 'r-0001', 'r-bbbb': 'r-0002'})

    def test_incremental_ids_survive_insertions_and_removals(self):
        rules, _ = self.fixture()
        old = extend_registry(rules)
        new = extend_registry([rules[0], {'team_rule_id': 'r-0000'}], old)
        self.assertEqual(new['internal_to_submission']['r-bbbb'], 'r-0002')
        self.assertEqual(new['internal_to_submission']['r-aaaa'], 'r-0001')
        self.assertEqual(new['internal_to_submission']['r-0000'], 'r-0003')
        self.assertEqual(len(old['internal_to_submission']), 2)

    def test_export_remaps_all_current_links_without_mutating_sources(self):
        rules, lookups = self.fixture()
        originals = deepcopy((rules, lookups))
        output = export_with_short_ids(rules, lookups, extend_registry(rules))
        exported = output['rules.json']['rules'][0]
        self.assertEqual(exported['team_rule_id'], 'r-0002')
        self.assertEqual(exported['overrides'], ['r-0001'])
        self.assertEqual(exported['interactions'], [{'from_rule_id': 'r-0002', 'to_rule_id': 'r-0001'}])
        self.assertEqual(exported['interaction'], 'r-0002 supersedes r-0001; not prefix-r-aaaa or r-aaaa-suffix.')
        self.assertEqual(exported['historical_interactions'], [{'from_rule_id': 'r-retired', 'to_rule_id': 'r-0002'}])
        self.assertEqual(exported['quoted_span'], rules[0]['quoted_span'])
        self.assertEqual(exported['consolidation'], rules[0]['consolidation'])
        row = output['lookups.json']['lookups']['A0001'][0]
        self.assertEqual(row, {**lookups['lookups']['A0001'][0], 'team_rule_id': 'r-0002'})
        self.assertEqual(output['rule_id_map.json']['submission_to_internal']['r-0002'], 'r-bbbb')
        self.assertEqual((rules, lookups), originals)

    def test_duplicate_missing_or_malformed_mapping_is_rejected(self):
        rules, lookups = self.fixture()
        for mapping in ({'r-aaaa': 'r-0001', 'r-bbbb': 'r-0001'}, {'r-aaaa': 'r-abcd'},
                        {'r-aaaa': 'r-0000'}, {'r-aaaa': 'r-00001'}):
            with self.subTest(mapping=mapping), self.assertRaises(ValueError):
                validate_registry({'schema_version': '1.0', 'internal_to_submission': mapping})
        with self.assertRaises(ValueError):
            export_with_short_ids(rules, lookups, extend_registry(rules[:1]))
        with self.assertRaises(ValueError):
            extend_registry(rules + rules[:1])

    def test_dangling_current_rule_links_and_lookup_ids_are_rejected(self):
        rules, lookups = self.fixture()
        registry = extend_registry(rules)
        broken = deepcopy(rules)
        broken[0]['overrides'] = ['r-absent']
        with self.assertRaises(ValueError):
            export_with_short_ids(broken, lookups, registry)
        lookups['lookups']['A0001'][0]['team_rule_id'] = 'r-absent'
        with self.assertRaises(ValueError):
            export_with_short_ids(rules, lookups, registry)


if __name__ == '__main__':
    unittest.main()
