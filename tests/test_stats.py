import random
import unittest

from lacuna.analysis import stats as S


class PercentileTests(unittest.TestCase):
    def test_known_values(self):
        xs = [1, 2, 3, 4, 5]
        self.assertEqual(S.percentile(xs, 0), 1)
        self.assertEqual(S.percentile(xs, 50), 3)
        self.assertEqual(S.percentile(xs, 100), 5)
        self.assertAlmostEqual(S.percentile(xs, 25), 2.0)
        self.assertAlmostEqual(S.percentile([10, 20], 50), 15.0)

    def test_single_and_empty(self):
        self.assertEqual(S.percentile([7], 99), 7)
        with self.assertRaises(ValueError):
            S.percentile([], 50)

    def test_unsorted_input(self):
        self.assertEqual(S.percentile([5, 1, 3], 50), 3)

    def test_summary(self):
        s = S.rtt_summary([10.0, 20.0, 30.0])
        self.assertEqual(s["rtt_min"], 10.0)
        self.assertEqual(s["rtt_max"], 30.0)
        self.assertAlmostEqual(s["rtt_mean"], 20.0)
        self.assertAlmostEqual(s["rtt_p50"], 20.0)
        self.assertTrue(all(v is None for v in S.rtt_summary([]).values()))


class JitterTests(unittest.TestCase):
    def test_constant_rtt_has_no_jitter(self):
        self.assertEqual(S.rfc3550_jitter([20.0] * 50), 0.0)

    def test_single_step(self):
        # one |D| of 16 ms moves J by 16/16 = 1 ms
        self.assertAlmostEqual(S.rfc3550_jitter([10.0, 26.0]), 1.0)

    def test_converges_to_mean_abs_delta(self):
        alt = [10.0, 20.0] * 400
        self.assertAlmostEqual(S.rfc3550_jitter(alt), 10.0, places=3)

    def test_short_input(self):
        self.assertEqual(S.rfc3550_jitter([]), 0.0)
        self.assertEqual(S.rfc3550_jitter([5.0]), 0.0)


class LossSequenceTests(unittest.TestCase):
    def test_hex_roundtrip(self):
        rng = random.Random(1)
        for n in (1, 7, 8, 9, 100, 401):
            bits = [1 if rng.random() < 0.3 else 0 for _ in range(n)]
            self.assertEqual(S.hex_to_bits(S.bits_to_hex(bits), n), bits)

    def test_hex_is_msb_first(self):
        self.assertEqual(S.bits_to_hex([1, 0, 0, 0, 0, 0, 0, 1]), "81")
        self.assertEqual(S.bits_to_hex([1]), "80")

    def test_burst_lengths(self):
        self.assertEqual(S.burst_lengths([0, 1, 1, 0, 1, 0, 0, 1, 1, 1]), [2, 1, 3])
        self.assertEqual(S.burst_lengths([0, 0, 0]), [])
        self.assertEqual(S.burst_lengths([1, 1, 1]), [3])

    def test_histogram(self):
        self.assertEqual(S.burst_histogram([1, 3, 1, 2, 1]), {1: 3, 2: 1, 3: 1})

    def test_random_expectation_is_geometric(self):
        exp = S.random_burst_expectation(0.5, 100, 4)
        self.assertEqual([round(x, 6) for x in exp], [50.0, 25.0, 12.5, 6.25])

    def test_loss_pct(self):
        self.assertEqual(S.loss_pct(200, 190), 5.0)
        self.assertEqual(S.loss_pct(0, 0), 0.0)


class RunsTestTests(unittest.TestCase):
    def test_undefined_without_both_symbols(self):
        self.assertIsNone(S.runs_test_z([0] * 20))
        self.assertIsNone(S.runs_test_z([1] * 20))

    def test_blocky_sequence_is_strongly_negative(self):
        bits = ([1] * 5 + [0] * 45) * 10
        self.assertLess(S.runs_test_z(bits), -5)

    def test_iid_sequence_is_not_rejected(self):
        rng = random.Random(7)
        bits = [1 if rng.random() < 0.1 else 0 for _ in range(2000)]
        self.assertLess(abs(S.runs_test_z(bits)), 2.5)


if __name__ == "__main__":
    unittest.main()
