import random
import re
import unittest
import xml.etree.ElementTree as ET

from lacuna.analysis import gilbert, stats
from lacuna.db import Database, LossSample, TputSample
from lacuna.report import html as H
from lacuna.report import svg

T0 = 1_760_000_000.0  # fixed epoch so output does not depend on the clock


def add_udp(db, profile, ts, bits, up=None, down=None, simulated=False, rtt=20.0):
    sent = len(bits)
    recv = sent - sum(bits)
    runs = stats.burst_lengths(bits)
    rid = db.add_run(profile, "udp", "h:9400", {"simulated": True} if simulated else {}, ts=ts)
    db.add_loss(rid, LossSample(
        sent=sent, recv=recv, loss_pct=stats.loss_pct(sent, recv), up_loss_pct=up, down_loss_pct=down,
        rtt_min=rtt - 1, rtt_mean=rtt, rtt_p50=rtt, rtt_p95=rtt + 4, rtt_p99=rtt + 6, rtt_max=rtt + 9,
        jitter_ms=1.5, dup=0, reordered=0, max_burst=max(runs) if runs else 0,
        loss_bits_hex=stats.bits_to_hex(bits)))


def build_db(case, simulated=False):
    db = Database(":memory:")
    case.addCleanup(db.close)
    rng = random.Random(5)
    for i in range(30):
        ts = T0 + i * 1800
        iid = [1 if rng.random() < 0.04 else 0 for _ in range(300)]
        bursty = gilbert.simulate(*gilbert.params_for(0.04, 5.0), 300, rng)
        add_udp(db, "home", ts, iid, up=3.0, down=1.5, simulated=simulated)
        add_udp(db, "hotspot", ts, bursty, up=5.0, down=2.0, simulated=simulated, rtt=60.0)
    for p in ("home", "hotspot"):
        rid = db.add_run(p, "throughput", "h:9400", {"simulated": True} if simulated else {}, ts=T0 + 3600)
        db.add_tput(rid, TputSample("down", 4, 10.0, 12_000_000, 9.6, rtt_ms=21.0))
    return db


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.db = build_db(self)
        self.html = H.build_report(self.db)

    def test_sections_in_order(self):
        ids = re.findall(r'<h2 id="(\w+)"', self.html)
        self.assertEqual(ids, ["summary", "timeline", "bursts", "heatmap", "mathis", "updown",
                               "compare", "method"])

    def test_header_and_footnote(self):
        self.assertIn("Lacuna report", self.html)
        self.assertIn("home", self.html)
        self.assertIn("hotspot", self.html)
        self.assertIn("docs/METHODOLOGY.md", self.html)

    def test_no_external_urls_outside_footnote(self):
        for n, line in enumerate(self.html.splitlines(), 1):
            if re.search(r"https?://", line):
                self.assertIn("METHODOLOGY.md", line, f"line {n}: {line[:80]}")
        self.assertNotRegex(self.html, r"<script|<link|src=|@import|url\(")

    def test_every_svg_is_well_formed_and_titled(self):
        charts = re.findall(r"<svg.*?</svg>", self.html, re.S)
        self.assertGreaterEqual(len(charts), 8)
        for c in charts:
            root = ET.fromstring(c)
            self.assertTrue(root.get("aria-label"))
            self.assertTrue(root.find("{*}text") is not None or root.find("text") is not None)

    def test_dark_mode_and_viewport(self):
        self.assertIn("prefers-color-scheme:dark", self.html)
        self.assertIn('name="viewport"', self.html)

    def test_tooltips_present(self):
        self.assertIn("<title>", self.html)

    def test_no_em_dash(self):
        self.assertNotIn(chr(0x2014), self.html)  # em dash, built at runtime so no .pyc holds it

    def test_not_labelled_simulated(self):
        self.assertNotIn("Simulated data", self.html)

    def test_comparison_only_with_two_profiles(self):
        single = H.build_report(self.db, ["home"])
        self.assertNotIn('id="compare"', single)

    def test_bursty_profile_gets_bursty_verdict(self):
        loss = {p: self.db.loss_rows([p]) for p in ("home", "hotspot")}
        home = H.profile_summary("home", loss["home"], [])
        hot = H.profile_summary("hotspot", loss["hotspot"], [])
        self.assertTrue(hot.verdict.startswith("Bursty loss"), hot.verdict)
        self.assertTrue(home.verdict.startswith("Loss looks random"), home.verdict)
        self.assertGreater(hot.ge.mean_burst, home.ge.mean_burst)

    def test_since_filter(self):
        out = H.build_report(self.db, since=T0 + 10 * 3600)
        self.assertIn("Samples:", out)
        self.assertNotEqual(out, self.html)


class SimulatedBannerTests(unittest.TestCase):
    def test_banner(self):
        self.assertIn("Simulated data", H.build_report(build_db(self, simulated=True)))


class EmptyAndEdgeTests(unittest.TestCase):
    def test_empty_db_still_builds(self):
        db = Database(":memory:")
        self.addCleanup(db.close)
        out = H.build_report(db)
        self.assertIn("no samples yet", out)

    def test_no_loss_verdict(self):
        db = Database(":memory:")
        self.addCleanup(db.close)
        add_udp(db, "clean", T0, [0] * 200)
        out = H.build_report(db)
        self.assertIn("No loss in 200 packets", out)

    def test_few_losses_not_classified(self):
        self.assertIn("Too few", H.verdict_for([[0] * 100 + [1, 1, 0, 0]]))


class SvgTests(unittest.TestCase):
    def test_ticks(self):
        lo, hi, ticks = svg.nice_range(0, 7.3)
        self.assertEqual((lo, ticks[0], ticks[-1]), (0, 0, hi))
        self.assertGreaterEqual(hi, 7.3)
        self.assertEqual(svg.log_ticks(0.3, 40)[2], [0.1, 1.0, 10.0, 100.0])

    def test_time_ticks_in_range(self):
        t0, t1, ticks = svg.time_ticks(T0, T0 + 7200)
        self.assertTrue(ticks)
        self.assertTrue(all(t0 <= t <= t1 for t, _ in ticks))

    def test_empty_charts_do_not_crash(self):
        for c in (svg.line_chart("t", [], "x", "y"), svg.hist_chart("t", {}, [], 0),
                  svg.scatter_chart("t", [], "x", "y"), svg.heatmap("t", [], {}, 1),
                  svg.bar_chart("t", [], [], [], "y")):
            ET.fromstring(c)

    def test_text_is_escaped(self):
        c = svg.line_chart("a<b & c", [svg.Series("x<y", 0, [(0, 1), (1, 2)])], "x", "y")
        ET.fromstring(c)
        self.assertNotIn("a<b", c)


if __name__ == "__main__":
    unittest.main()
