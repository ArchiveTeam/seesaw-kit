# encoding=utf8
from __future__ import unicode_literals

import unittest

from seesaw import bandwidth


class BandwidthRegistryTest(unittest.TestCase):
    def tearDown(self):
        bandwidth.reset()

    def test_unset_returns_none(self):
        bandwidth.reset()
        self.assertIsNone(bandwidth.download_kib())
        self.assertIsNone(bandwidth.upload_kib())
        self.assertIsNone(bandwidth.wget_limit_rate())
        self.assertIsNone(bandwidth.rsync_bwlimit())
        self.assertIsNone(bandwidth.curl_limit_rate())

    def test_zero_total_is_unlimited(self):
        bandwidth.set_limits(download=0, upload=0,
                             download_divisor=2, upload_divisor=4)
        self.assertIsNone(bandwidth.download_kib())
        self.assertIsNone(bandwidth.upload_kib())

    def test_download_divided_by_concurrency(self):
        bandwidth.set_limits(download=1000, download_divisor=4)
        self.assertEqual(250, bandwidth.download_kib())
        self.assertEqual("250k", bandwidth.wget_limit_rate())

    def test_upload_divided_and_formatted(self):
        bandwidth.set_limits(upload=1000, upload_divisor=4)
        self.assertEqual(250, bandwidth.upload_kib())
        self.assertEqual("250", bandwidth.rsync_bwlimit())
        self.assertEqual("250k", bandwidth.curl_limit_rate())

    def test_floor_is_one(self):
        bandwidth.set_limits(download=3, download_divisor=10)
        self.assertEqual(1, bandwidth.download_kib())

    def test_string_typed_total_and_divisor(self):
        # Config sources may hand back numeric strings; they must coerce.
        bandwidth.set_limits(download="1000", download_divisor="2")
        self.assertEqual(500, bandwidth.download_kib())
        self.assertEqual("500k", bandwidth.wget_limit_rate())

    def test_divisor_below_one_is_clamped(self):
        bandwidth.set_limits(download=1000, download_divisor=lambda: 0)
        self.assertEqual(1000, bandwidth.download_kib())

    def test_divisor_callable_resolved_live(self):
        state = {"n": 2}
        bandwidth.set_limits(download=1000,
                             download_divisor=lambda: state["n"])
        self.assertEqual(500, bandwidth.download_kib())
        state["n"] = 5
        self.assertEqual(200, bandwidth.download_kib())

    def test_total_realizable_object(self):
        class Live(object):
            value = 800

            def realize(self, item):
                return self.value
        live = Live()
        bandwidth.set_limits(download=live, download_divisor=2)
        self.assertEqual(400, bandwidth.download_kib())
        live.value = 200
        self.assertEqual(100, bandwidth.download_kib())
