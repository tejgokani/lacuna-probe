import random
import unittest

from lacuna.analysis import gilbert as G


class FitTests(unittest.TestCase):
    def test_recovers_parameters(self):
        p, r = 0.02, 0.25
        bits = G.simulate(p, r, 60000, random.Random(3))
        fit = G.fit(bits)
        self.assertAlmostEqual(fit.p, p, delta=0.005)
        self.assertAlmostEqual(fit.r, r, delta=0.03)
        self.assertAlmostEqual(fit.mean_burst, 1 / r, delta=0.5)
        self.assertAlmostEqual(fit.stationary_loss, p / (p + r), delta=0.01)
        self.assertTrue(fit.reliable)

    def test_hand_counted_sequence(self):
        # 0 1 1 0 0 1 : transitions 0>1, 1>1, 1>0, 0>0, 0>1
        fit = G.fit([0, 1, 1, 0, 0, 1])
        self.assertAlmostEqual(fit.p, 2 / 3)  # good->bad 2 of 3 good-prev
        self.assertAlmostEqual(fit.r, 1 / 2)  # bad->good 1 of 2 bad-prev
        self.assertEqual(fit.losses, 3)

    def test_small_sample_is_flagged(self):
        fit = G.fit([0, 1, 0, 0, 1, 1, 0] * 10)
        self.assertFalse(fit.reliable)
        self.assertIn("too small", fit.note)

    def test_no_loss(self):
        fit = G.fit([0] * 300)
        self.assertEqual(fit.p, 0.0)
        self.assertIsNone(fit.r)
        self.assertIsNone(fit.mean_burst)
        self.assertIn("no losses", fit.note)

    def test_all_lost(self):
        fit = G.fit([1] * 300)
        self.assertIsNone(fit.p)
        self.assertEqual(fit.r, 0.0)

    def test_empty(self):
        fit = G.fit([])
        self.assertIsNone(fit.p)
        self.assertIsNone(fit.r)

    def test_pooling_does_not_join_runs(self):
        # joined, this would count a 1>0 transition across the seam
        joined = G.fit([0, 0, 1] + [0, 0, 1])
        pooled = G.fit_many([[0, 0, 1], [0, 0, 1]])
        self.assertEqual(joined.n, pooled.n)
        self.assertIsNone(pooled.r)  # never saw a bad->good transition


class ParamsTests(unittest.TestCase):
    def test_params_for_hits_target(self):
        p, r = G.params_for(0.05, 4.0)
        self.assertAlmostEqual(r, 0.25)
        self.assertAlmostEqual(p / (p + r), 0.05)

    def test_zero_loss(self):
        self.assertEqual(G.params_for(0.0, 4.0), (0.0, 0.25))

    def test_bad_input(self):
        with self.assertRaises(ValueError):
            G.params_for(1.0, 4.0)
        with self.assertRaises(ValueError):
            G.params_for(0.1, 0.5)


if __name__ == "__main__":
    unittest.main()
