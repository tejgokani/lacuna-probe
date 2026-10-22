import unittest

from lacuna.analysis import mathis as M


def loss_row(ts, loss_pct, rtt=50.0, profile="p", kind="udp"):
    return {"ts": ts, "loss_pct": loss_pct, "rtt_p50": rtt, "rtt_mean": rtt,
            "profile": profile, "kind": kind}


def tput_row(ts, mbps, profile="p"):
    return {"ts": ts, "mbps": mbps, "profile": profile}


class MathisTests(unittest.TestCase):
    def test_known_value(self):
        # 1460 B, 100 ms, 1% loss -> 1460*8/0.1 * 1.2247 / 0.1 = 1.4305 Mbit/s
        self.assertAlmostEqual(M.mathis_mbps(1460, 100, 0.01), 1.4305, places=3)

    def test_unbounded_at_zero_loss(self):
        self.assertIsNone(M.mathis_mbps(1460, 100, 0.0))
        self.assertIsNone(M.mathis_mbps(1460, 0, 0.01))

    def test_scaling(self):
        base = M.mathis_mbps(1460, 100, 0.01)
        self.assertAlmostEqual(M.mathis_mbps(1460, 50, 0.01) / base, 2.0)
        self.assertAlmostEqual(M.mathis_mbps(1460, 100, 0.04) / base, 0.5)

    def test_curve_skips_zero(self):
        pts = M.curve(1460, 100, [0.0, 0.01, 0.04])
        self.assertEqual([p for p, _ in pts], [0.01, 0.04])


class PairingTests(unittest.TestCase):
    def test_pairs_nearest_loss_sample(self):
        loss = [loss_row(100, 1.0), loss_row(1000, 4.0)]
        pairs = M.pair_samples([tput_row(960, 5.0)], loss)
        self.assertEqual(len(pairs), 1)
        self.assertAlmostEqual(pairs[0].loss_frac, 0.04)
        self.assertAlmostEqual(pairs[0].ratio, 5.0 / pairs[0].predicted_mbps)

    def test_window_and_profile_respected(self):
        loss = [loss_row(0, 1.0, profile="a")]
        self.assertEqual(M.pair_samples([tput_row(10, 5.0, profile="b")], loss), [])
        self.assertEqual(M.pair_samples([tput_row(400, 5.0, profile="a")], loss), [])
        self.assertEqual(len(M.pair_samples([tput_row(299, 5.0, profile="a")], loss)), 1)

    def test_zero_loss_and_tcp_connect_skipped(self):
        self.assertEqual(M.pair_samples([tput_row(0, 5.0)], [loss_row(0, 0.0)]), [])
        self.assertEqual(M.pair_samples([tput_row(0, 5.0)], [loss_row(0, 2.0, kind="tcp")]), [])


if __name__ == "__main__":
    unittest.main()
