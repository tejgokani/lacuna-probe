import os
import struct
import tempfile
import unittest

from lacuna import server as S
from lacuna.lossim import TcpDelayProxy
from lacuna.probes import split_host_port
from lacuna.probes import throughput as T

BASE = 9440


def fake_tcp_info(retrans=7, cwnd=10, rtt=12345, rttvar=321, ssthresh=44):
    fields = [0] * 24
    fields[T._TCP_INFO_FIELDS.index("total_retrans")] = retrans
    fields[T._TCP_INFO_FIELDS.index("snd_cwnd")] = cwnd
    fields[T._TCP_INFO_FIELDS.index("rtt")] = rtt
    fields[T._TCP_INFO_FIELDS.index("rttvar")] = rttvar
    fields[T._TCP_INFO_FIELDS.index("snd_ssthresh")] = ssthresh
    return struct.pack("=8B24I", *([1] * 8), *fields).ljust(192, b"\0")


class TcpInfoTests(unittest.TestCase):
    def test_struct_matches_linux_layout(self):
        # 8 bytes + 24 u32, offsets from linux/tcp.h: total_retrans is the 24th u32
        self.assertEqual(T._TCP_INFO.size, 104)
        self.assertEqual(len(T._TCP_INFO_FIELDS), 24)
        self.assertEqual(T._TCP_INFO_FIELDS[0], "rto")
        self.assertEqual(T._TCP_INFO_FIELDS[2], "snd_mss")
        self.assertEqual(T._TCP_INFO_FIELDS[8], "fackets")
        self.assertEqual(T._TCP_INFO_FIELDS[15], "rtt")
        self.assertEqual(T._TCP_INFO_FIELDS[18], "snd_cwnd")

    def test_parse(self):
        info = T.parse_tcp_info(fake_tcp_info())
        self.assertEqual(info, T.TcpInfo(7, 10, 12345, 321, 44))

    def test_short_buffer_is_none(self):
        self.assertIsNone(T.parse_tcp_info(b"\0" * 50))

    def test_summarize_uses_deltas(self):
        a = T.TcpInfo(2, 10, 10_000, 1, 50)
        b = T.TcpInfo(9, 20, 30_000, 1, 70)
        out = T.summarize_info([a], [b], with_retrans=True)
        self.assertEqual(out["retrans"], 7)
        self.assertEqual(out["cwnd"], 20)
        self.assertAlmostEqual(out["rtt_ms"], 30.0)
        self.assertIsNone(T.summarize_info([a], [b], with_retrans=False)["retrans"])

    def test_summarize_without_info(self):
        self.assertEqual(T.summarize_info([None], [None], True)["cwnd"], None)


class HostPortTests(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(split_host_port("example.com:9400"), ("example.com", 9400))
        self.assertEqual(split_host_port("example.com", 80), ("example.com", 80))
        for bad in ("example.com", ":80", "h:abc", "h:0", "h:70000"):
            with self.assertRaises(ValueError):
                split_host_port(bad)


class LoopbackThroughputTests(unittest.TestCase):
    def setUp(self):
        self._old = os.environ.get("XDG_DATA_HOME")
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["XDG_DATA_HOME"] = self._tmp.name
        self.srv = S.LacunaServer("127.0.0.1", BASE).start()

    def tearDown(self):
        self.srv.stop()
        self._tmp.cleanup()
        if self._old is None:
            os.environ.pop("XDG_DATA_HOME", None)
        else:
            os.environ["XDG_DATA_HOME"] = self._old

    def test_up_and_down(self):
        for direction in ("up", "down"):
            sample, per = T.run_tcp_throughput("127.0.0.1", BASE, secs=0.5, streams=2,
                                               direction=direction)
            self.assertEqual(sample.direction, direction)
            self.assertEqual(sample.streams, 2)
            self.assertGreater(sample.bytes, 100_000)
            self.assertGreater(sample.mbps, 10)
            self.assertEqual(len(per), 2)
            self.assertAlmostEqual(sum(per), sample.mbps, delta=sample.mbps * 0.01)

    def test_through_delay_proxy(self):
        proxy = TcpDelayProxy(("127.0.0.1", BASE + 11), ("127.0.0.1", BASE + 1), 10).start()
        try:
            sample, _ = T.run_tcp_throughput("127.0.0.1", BASE + 10, secs=0.6, direction="up")
        finally:
            proxy.stop()
        self.assertGreater(sample.bytes, 10_000)

    def test_rate_cap_on_proxy(self):
        proxy = TcpDelayProxy(("127.0.0.1", BASE + 13), ("127.0.0.1", BASE + 1), 5, rate_mbps=20).start()
        try:
            sample, _ = T.run_tcp_throughput("127.0.0.1", BASE + 12, secs=1.0, direction="up")
        finally:
            proxy.stop()
        self.assertLess(sample.mbps, 35)
        self.assertGreater(sample.mbps, 8)

    def test_limits(self):
        with self.assertRaises(T.ThroughputError):
            T.run_tcp_throughput("127.0.0.1", BASE, secs=61)
        with self.assertRaises(T.ThroughputError):
            T.run_tcp_throughput("127.0.0.1", BASE, secs=1, streams=99)
        with self.assertRaises(T.ThroughputError):
            T.run_tcp_throughput("127.0.0.1", BASE, secs=1, direction="sideways")

    def test_one_at_a_time(self):
        with T.one_at_a_time():
            if T.fcntl is not None:
                with self.assertRaises(T.ThroughputError):
                    with T.one_at_a_time():
                        pass

    def test_server_refuses_bad_header(self):
        import socket

        with socket.create_connection(("127.0.0.1", BASE + 1), timeout=2) as s:
            s.sendall(b"NOPE" + bytes(5))
            self.assertEqual(s.recv(10), b"")


if __name__ == "__main__":
    unittest.main()
