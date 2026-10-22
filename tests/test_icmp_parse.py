"""ICMP parsing.

tests/fixtures/ping_macos_*.txt are captured from a real macOS ping. The
Linux text below follows the iputils-ping format (seq counts from 1, summary
says "received" and "mdev"). It is written by hand, not captured, because the
project is developed on a Mac. CI on Ubuntu runs the live `lacuna doctor`.
"""

import unittest
from pathlib import Path

from lacuna.probes import icmp

FIX = Path(__file__).parent / "fixtures"

LINUX_PARTIAL = """\
PING 1.1.1.1 (1.1.1.1) 56(84) bytes of data.
64 bytes from 1.1.1.1: icmp_seq=1 ttl=57 time=12.1 ms
64 bytes from 1.1.1.1: icmp_seq=2 ttl=57 time=12.4 ms
64 bytes from 1.1.1.1: icmp_seq=5 ttl=57 time=30.0 ms
64 bytes from 1.1.1.1: icmp_seq=6 ttl=57 time=12.2 ms
64 bytes from 1.1.1.1: icmp_seq=6 ttl=57 time=12.9 ms (DUP!)
64 bytes from 1.1.1.1: icmp_seq=8 ttl=57 time=12.0 ms

--- 1.1.1.1 ping statistics ---
8 packets transmitted, 5 received, 37.5% packet loss, time 1403ms
rtt min/avg/max/mdev = 12.0/15.7/30.0/7.2 ms
"""

LINUX_ALL_LOST = """\
PING 10.255.255.1 (10.255.255.1) 56(84) bytes of data.

--- 10.255.255.1 ping statistics ---
4 packets transmitted, 0 received, 100% packet loss, time 3072ms
"""


class ParseTests(unittest.TestCase):
    def test_macos_clean_run(self):
        res = icmp.parse_ping((FIX / "ping_macos_ok.txt").read_text(), seq_base=0)
        self.assertEqual((res.transmitted, res.received), (5, 5))
        self.assertEqual([s for s, _ in res.replies], [0, 1, 2, 3, 4])
        self.assertAlmostEqual(res.replies[3][1], 0.244)

    def test_macos_total_loss(self):
        res = icmp.parse_ping((FIX / "ping_macos_loss.txt").read_text(), seq_base=0)
        self.assertEqual((res.transmitted, res.received, res.replies), (3, 0, []))

    def test_linux_partial_with_duplicate(self):
        res = icmp.parse_ping(LINUX_PARTIAL, seq_base=1)
        self.assertEqual((res.transmitted, res.received), (8, 5))
        self.assertEqual([s for s, _ in res.replies], [0, 1, 4, 5, 7])
        self.assertEqual(res.duplicates, 1)

    def test_missing_summary_raises(self):
        with self.assertRaises(icmp.PingError):
            icmp.parse_ping("PING x\n")


class SampleTests(unittest.TestCase):
    def test_loss_sequence_from_gaps(self):
        s = icmp.sample_from_result(icmp.parse_ping(LINUX_PARTIAL, seq_base=1))
        self.assertEqual((s.sent, s.recv), (8, 5))
        self.assertAlmostEqual(s.loss_pct, 37.5)
        # seq 2,3 lost (0-based) and 6 lost: 0 0 1 1 0 0 1 0 -> 0x32
        self.assertEqual(s.loss_bits_hex, "32")
        self.assertEqual(s.max_burst, 2)
        self.assertEqual(s.dup, 1)
        self.assertEqual(s.rtt_min, 12.0)
        self.assertEqual(s.rtt_max, 30.0)
        self.assertGreater(s.jitter_ms, 0)

    def test_total_loss_has_no_rtt(self):
        s = icmp.sample_from_result(icmp.parse_ping(LINUX_ALL_LOST, seq_base=1))
        self.assertEqual((s.sent, s.recv, s.loss_pct), (4, 0, 100.0))
        self.assertIsNone(s.rtt_p50)
        self.assertIsNone(s.jitter_ms)
        self.assertEqual(s.max_burst, 4)

    def test_clean_macos_sample(self):
        s = icmp.sample_from_result(icmp.parse_ping((FIX / "ping_macos_ok.txt").read_text(), 0))
        self.assertEqual((s.loss_pct, s.max_burst), (0.0, 0))
        self.assertEqual(s.loss_bits_hex, "00")


if __name__ == "__main__":
    unittest.main()
