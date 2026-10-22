import unittest

from lacuna import protocol as P


class HeaderTests(unittest.TestCase):
    def test_roundtrip(self):
        h = P.Header(P.PROBE, 0xDEADBEEF, 42, 123456789012, flags=3)
        data = P.pack_header(h)
        self.assertEqual(len(data), P.HEADER_LEN)
        self.assertEqual(P.unpack_header(data), h)

    def test_rejects_garbage(self):
        with self.assertRaises(P.ProtocolError):
            P.unpack_header(b"short")
        with self.assertRaises(P.ProtocolError):
            P.unpack_header(b"XXXX" + bytes(30))
        good = bytearray(P.pack_header(P.Header(P.PROBE, 1, 1, 1)))
        good[4] = 9
        with self.assertRaises(P.ProtocolError):
            P.unpack_header(bytes(good))
        good[4] = P.VERSION
        good[5] = 77
        with self.assertRaises(P.ProtocolError):
            P.unpack_header(bytes(good))

    def test_layout_is_little_endian(self):
        data = P.pack_header(P.Header(P.PROBE, 1, 2, 3))
        self.assertEqual(data[:4], b"LCNA")
        self.assertEqual(data[8:12], (1).to_bytes(4, "little"))
        self.assertEqual(data[12:16], (2).to_bytes(4, "little"))
        self.assertEqual(data[16:24], (3).to_bytes(8, "little"))


class PacketTests(unittest.TestCase):
    def test_cookie_depends_on_every_input(self):
        base = P.make_cookie(b"k", "1.2.3.4", 5000, 7)
        self.assertEqual(len(base), P.COOKIE_LEN)
        self.assertNotEqual(base, P.make_cookie(b"x", "1.2.3.4", 5000, 7))
        self.assertNotEqual(base, P.make_cookie(b"k", "1.2.3.5", 5000, 7))
        self.assertNotEqual(base, P.make_cookie(b"k", "1.2.3.4", 5001, 7))
        self.assertNotEqual(base, P.make_cookie(b"k", "1.2.3.4", 5000, 8))
        self.assertTrue(P.cookie_ok(base, base))
        self.assertFalse(P.cookie_ok(base, bytes(8)))
        self.assertFalse(P.cookie_ok(base, base[:4]))

    def test_hello_is_larger_than_ack(self):
        hello = P.build_hello(1)
        ack = P.build_hello_ack(1, bytes(8))
        self.assertGreater(len(hello), len(ack))

    def test_probe_size_clamped(self):
        cookie = b"c" * 8
        self.assertEqual(len(P.build_probe(1, 0, 0, cookie, 10)), P.MIN_PROBE)
        self.assertEqual(len(P.build_probe(1, 0, 0, cookie, 5000)), P.MAX_PACKET)
        self.assertEqual(len(P.build_probe(1, 0, 0, cookie, 200)), 200)

    def test_echo_keeps_size_and_payload(self):
        probe = P.build_probe(9, 3, 55, b"c" * 8, 300)
        echo = P.to_echo(probe)
        self.assertEqual(len(echo), len(probe))
        h = P.unpack_header(echo)
        self.assertEqual((h.type, h.session, h.seq, h.t_send_ns), (P.ECHO, 9, 3, 55))
        self.assertEqual(P.probe_cookie(echo), b"c" * 8)

    def test_stats_never_exceeds_request(self):
        bitmap = bytes(range(200))
        for limit in (32, 40, 100, 232, 1400):
            reply = P.build_stats(1, 10, 99, bitmap, limit)
            self.assertLessEqual(len(reply), max(limit, P.STATS_FIXED))

    def test_stats_roundtrip_and_bit_lookup(self):
        bitmap = bytes([0b00000101, 0b10000000])
        st = P.parse_stats(P.build_stats(1, 3, 15, bitmap, 1400))
        self.assertEqual((st.rx_count, st.max_seq), (3, 15))
        self.assertTrue(st.received(0))
        self.assertFalse(st.received(1))
        self.assertTrue(st.received(2))
        self.assertTrue(st.received(15))
        self.assertFalse(st.received(500))

    def test_stats_req_size_fits_bitmap(self):
        self.assertEqual(P.stats_req_size(0), P.MIN_PROBE)
        self.assertEqual(P.stats_req_size(8), P.STATS_FIXED + 1)
        self.assertEqual(P.stats_req_size(500), P.STATS_FIXED + 63)
        self.assertEqual(P.stats_req_size(10**6), P.MAX_PACKET)


if __name__ == "__main__":
    unittest.main()
