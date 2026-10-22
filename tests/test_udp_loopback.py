import random
import unittest

from lacuna import protocol as P
from lacuna import server as S
from lacuna.analysis import stats
from lacuna.lossim import GilbertLoss, NoLoss, UdpLossSim, UniformLoss, split_loss
from lacuna.probes.udp import UdpProbeError, run_udp

ADDR = ("10.0.0.1", 40000)
BASE = 9420


def hello(state, session=1, addr=ADDR, now=0.0):
    ack = state.handle(P.build_hello(session), addr, now)
    return ack


class ServerRuleTests(unittest.TestCase):
    def setUp(self):
        self.state = S.ServerState(secret=b"s" * 32)

    def test_handshake_then_echo_never_larger(self):
        ack = hello(self.state)
        self.assertLess(len(ack), len(P.build_hello(1)))
        cookie = P.probe_cookie(ack)
        for size in (32, 100, 1400):
            probe = P.build_probe(1, 0, 5, cookie, size)
            echo = self.state.handle(probe, ADDR, 0.1)
            self.assertIsNotNone(echo)
            self.assertLessEqual(len(echo), len(probe))
            self.assertEqual(P.unpack_header(echo).type, P.ECHO)

    def test_wrong_or_missing_cookie_dropped(self):
        hello(self.state)
        bad = P.build_probe(1, 0, 5, b"x" * 8, 100)
        self.assertIsNone(self.state.handle(bad, ADDR, 0.1))
        self.assertIsNone(self.state.handle(P.pack_header(P.Header(P.PROBE, 1, 0, 0)), ADDR, 0.1))

    def test_probe_without_hello_dropped(self):
        self.assertIsNone(self.state.handle(P.build_probe(1, 0, 5, bytes(8), 100), ADDR, 0.0))

    def test_cookie_not_transferable_between_sources(self):
        cookie = P.probe_cookie(hello(self.state))
        probe = P.build_probe(1, 0, 5, cookie, 100)
        self.assertIsNone(self.state.handle(probe, ("10.0.0.2", 40000), 0.1))

    def test_short_hello_and_oversize_dropped(self):
        self.assertIsNone(self.state.handle(P.build_hello(1)[:40], ADDR, 0.0))
        self.assertIsNone(self.state.handle(b"\0" * 1401, ADDR, 0.0))
        self.assertIsNone(self.state.handle(b"junk", ADDR, 0.0))

    def test_stats_reply_not_larger_and_correct(self):
        cookie = P.probe_cookie(hello(self.state))
        for seq in (0, 1, 3, 9):
            self.state.handle(P.build_probe(1, seq, 0, cookie, 64), ADDR, 0.1)
        self.state.handle(P.build_probe(1, 3, 0, cookie, 64), ADDR, 0.1)  # duplicate
        req = P.build_stats_req(1, cookie, P.stats_req_size(10))
        reply = self.state.handle(req, ADDR, 0.2)
        self.assertLessEqual(len(reply), len(req))
        st = P.parse_stats(reply)
        self.assertEqual((st.rx_count, st.max_seq), (4, 9))
        self.assertEqual([st.received(i) for i in range(10)],
                         [True, True, False, True, False, False, False, False, False, True])

    def test_tiny_stats_request_gets_truncated_bitmap(self):
        cookie = P.probe_cookie(hello(self.state))
        req = P.build_stats_req(1, cookie, P.MIN_PROBE)
        self.assertLessEqual(len(self.state.handle(req, ADDR, 0.2)), len(req))

    def test_rate_cap_per_ip(self):
        cookie = P.probe_cookie(hello(self.state))
        probe = P.build_probe(1, 0, 0, cookie, 64)
        answered = sum(self.state.handle(probe, ADDR, 0.5) is not None for _ in range(400))
        self.assertLessEqual(answered, S.RATE_PER_IP)
        self.assertGreater(answered, 150)
        self.assertIsNotNone(self.state.handle(probe, ADDR, 1.6))  # new window

    def test_session_cap_per_ip(self):
        ok = sum(hello(self.state, session=i, addr=("10.0.0.9", 1000 + i), now=i * 0.01) is not None
                 for i in range(20))
        self.assertEqual(ok, S.MAX_SESSIONS_PER_IP)

    def test_finished_sessions_free_their_slot(self):
        # back-to-back runs, 3 s apart, far more than the live-session cap
        for i in range(S.MAX_SESSIONS_PER_IP * 3):
            now = i * 3.0
            addr = ("10.0.0.9", 1000 + i)
            ack = hello(self.state, session=i, addr=addr, now=now)
            self.assertIsNotNone(ack, f"session {i} refused")
            req = P.build_stats_req(i, P.probe_cookie(ack), P.MIN_PROBE)
            self.assertIsNotNone(self.state.handle(req, addr, now + 0.1))

    def test_sessions_expire_when_idle(self):
        cookie = P.probe_cookie(hello(self.state))
        probe = P.build_probe(1, 0, 0, cookie, 64)
        self.assertIsNotNone(self.state.handle(probe, ADDR, 10.0))
        self.assertIsNone(self.state.handle(probe, ADDR, 10.0 + S.SESSION_IDLE_S + 1))
        # a fresh HELLO works again, and expiry freed the slot
        self.assertIsNotNone(hello(self.state, now=200.0))


class LoopbackTests(unittest.TestCase):
    def setUp(self):
        self.srv = S.LacunaServer("127.0.0.1", BASE).start()
        self.sims: list[UdpLossSim] = []

    def tearDown(self):
        for s in self.sims:
            s.stop()
        self.srv.stop()

    def sim(self, port, up=None, down=None, delay_ms=0.0):
        s = UdpLossSim(("127.0.0.1", port), ("127.0.0.1", BASE), up, down, delay_ms).start()
        self.sims.append(s)
        return s

    def test_direct_path_has_no_loss(self):
        s = run_udp("127.0.0.1", BASE, count=120, pps=200, grace=0.4)
        self.assertEqual((s.sent, s.recv, s.loss_pct), (120, 120, 0.0))
        self.assertEqual(s.up_loss_pct, 0.0)
        self.assertEqual(s.down_loss_pct, 0.0)
        self.assertEqual(s.max_burst, 0)
        self.assertIsNotNone(s.rtt_p50)

    def test_uplink_and_downlink_loss_are_separated(self):
        sim = self.sim(BASE + 1, up=UniformLoss(0.10, 11), down=UniformLoss(0.20, 12))
        s = run_udp("127.0.0.1", BASE + 1, count=300, pps=200, grace=0.5)
        # exact: the simulator counts every drop it makes
        self.assertEqual(sim.dropped_up, 300 - round(300 * (1 - s.up_loss_pct / 100)))
        reached = 300 - sim.dropped_up
        self.assertEqual(sim.dropped_down, reached - s.recv)
        self.assertAlmostEqual(s.down_loss_pct, 100 * sim.dropped_down / reached, places=6)
        self.assertGreater(sim.dropped_up, 10)
        self.assertGreater(sim.dropped_down, 20)
        self.assertGreater(s.down_loss_pct, s.up_loss_pct)

    def test_measured_loss_close_to_configured(self):
        up, down = split_loss(0.05)
        sim = self.sim(BASE + 2, UniformLoss(up, 1), UniformLoss(down, 2))
        s = run_udp("127.0.0.1", BASE + 2, count=400, pps=200, grace=0.5)
        self.assertEqual(s.sent - s.recv, sim.dropped_up + sim.dropped_down)
        self.assertAlmostEqual(s.loss_pct, 5.0, delta=3.0)

    def test_bursty_loss_is_visible_in_the_sequence(self):
        self.sim(BASE + 3, GilbertLoss(0.15, 5.0, seed=4), NoLoss())
        s = run_udp("127.0.0.1", BASE + 3, count=400, pps=200, grace=0.5)
        bits = stats.hex_to_bits(s.loss_bits_hex, s.sent)
        self.assertEqual(sum(bits), s.sent - s.recv)
        self.assertGreaterEqual(s.max_burst, 3)
        self.assertLess(stats.runs_test_z(bits), -2)
        self.assertIsNotNone(s.ge_r)

    def test_delay_shows_up_in_rtt(self):
        self.sim(BASE + 4, delay_ms=15)
        s = run_udp("127.0.0.1", BASE + 4, count=40, pps=100, grace=0.5)
        self.assertGreater(s.rtt_p50, 25)
        self.assertLess(s.rtt_p50, 120)

    def test_no_server_gives_clear_error(self):
        with self.assertRaises(UdpProbeError):
            run_udp("127.0.0.1", BASE + 9, count=5, hello_tries=1, hello_wait=0.2)

    def test_bad_arguments(self):
        with self.assertRaises(UdpProbeError):
            run_udp("127.0.0.1", BASE, count=0)
        with self.assertRaises(UdpProbeError):
            run_udp("127.0.0.1", BASE, count=P.MAX_UDP_COUNT + 1)


if __name__ == "__main__":
    random.seed(0)
    unittest.main()
