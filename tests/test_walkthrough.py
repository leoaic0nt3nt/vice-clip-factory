import unittest

from viceclip.walkthrough import rank_windows, windows


class WalkthroughTests(unittest.TestCase):
    def test_windows_cover_whole_recording_with_overlap_and_no_short_tail(self):
        ranges = list(windows(7228.426, 420, 50))
        self.assertEqual(len(ranges), 20)
        self.assertEqual(ranges[0][0], 0)
        self.assertEqual(ranges[-1][1], 7228.426)
        self.assertTrue(all(left[1] - right[0] == 50 for left, right in zip(ranges, ranges[1:])))
        self.assertGreater(ranges[-1][1] - ranges[-1][0], 50)
        self.assertEqual(list(windows(435, 420, 50)), [(0, 435)])

    def test_global_ranking_deduplicates_the_same_payoff_seen_in_adjacent_windows(self):
        first = {"start": 0, "plan": {"clips": [
            {"start": 385, "end": 419, "score": 89, "evidence": {"payoff_at": 407}},
            {"start": 215, "end": 245, "score": 70, "evidence": {"payoff_at": 240}},
        ]}}
        second = {"start": 370, "plan": {"clips": [
            {"start": 13, "end": 47, "score": 93, "evidence": {"payoff_at": 38}},
        ]}}
        chosen = rank_windows([first, second], 3)
        self.assertEqual([(start, clip["score"]) for start, _, _, _, clip in chosen], [(383, 93), (215, 70)])
