"""Logical response coverage must not permanently poison a valid JSON cache."""

import unittest

from rent_rules.pipeline import checked_generate


class RecoverableModel:
    def __init__(self, responses, offline=False):
        self.responses = iter(responses)
        self.offline = offline
        self.calls = 0
        self.invalidated = []

    def generate(self, prompt, schema, stage):
        self.calls += 1
        return next(self.responses), {"stage": stage, "cache_key": str(self.calls)}

    def invalidate_response(self, metadata, reason):
        self.invalidated.append((metadata, reason))


def require_complete(response):
    if response != {"ids": ["expected"]}:
        raise ValueError("The response omitted a required record.")


class ResponseRecoveryTests(unittest.TestCase):
    def test_incomplete_cached_response_is_invalidated_and_retried_once(self):
        model = RecoverableModel([{"ids": []}, {"ids": ["expected"]}])
        result, metadata = checked_generate(model, "prompt", {}, "review", require_complete)
        self.assertEqual(result, {"ids": ["expected"]})
        self.assertEqual(model.calls, 2)
        self.assertEqual(len(model.invalidated), 1)
        self.assertEqual(len(metadata["rejected_responses"]), 1)

    def test_repeated_incomplete_response_fails_and_does_not_loop(self):
        model = RecoverableModel([{"ids": []}, {"ids": []}])
        with self.assertRaisesRegex(ValueError, "omitted"):
            checked_generate(model, "prompt", {}, "extract", require_complete)
        self.assertEqual(model.calls, 2)
        self.assertEqual(len(model.invalidated), 2)

    def test_offline_replay_never_invalidates_or_requests_a_replacement(self):
        model = RecoverableModel([{"ids": []}], offline=True)
        with self.assertRaisesRegex(ValueError, "omitted"):
            checked_generate(model, "prompt", {}, "extract", require_complete)
        self.assertEqual(model.calls, 1)
        self.assertEqual(model.invalidated, [])


if __name__ == "__main__":
    unittest.main()
