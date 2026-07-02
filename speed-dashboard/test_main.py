"""Unit tests for the build_point helper in main.py."""

import os
import unittest
from unittest.mock import patch

# Provide the required env var before importing main (module-level code reads it).
os.environ.setdefault("input", "test-topic")

from main import build_point  # noqa: E402


class TestBuildPoint(unittest.TestCase):
    def test_speed_and_accgx_returned(self):
        row = {"timestamp_ms": 1000, "speedKmh": 100.0, "accG_x": 0.5}
        point = build_point(row)
        self.assertEqual(point, {"t": 1000.0, "v": 100.0, "a": 0.5, "b": None, "g": None})

    def test_no_accgx_defaults_to_none(self):
        row = {"timestamp_ms": 2000, "speedKmh": 150.0}
        point = build_point(row)
        self.assertEqual(point, {"t": 2000.0, "v": 150.0, "a": None, "b": None, "g": None})

    def test_no_speed_returns_none(self):
        row = {"timestamp_ms": 3000, "accG_x": 1.2}
        point = build_point(row)
        self.assertIsNone(point)

    def test_empty_row_returns_none(self):
        point = build_point({})
        self.assertIsNone(point)

    def test_alternate_field_names(self):
        """SpeedKmh (capital K) should be accepted as a fallback for speed."""
        row = {"Timestamp": 4000, "SpeedKmh": 200.0, "accG_x": -1.0}
        point = build_point(row)
        self.assertEqual(point, {"t": 4000.0, "v": 200.0, "a": -1.0, "b": None, "g": None})

    def test_negative_accgx(self):
        row = {"timestamp_ms": 5000, "speedKmh": 80.0, "accG_x": -2.5}
        point = build_point(row)
        self.assertEqual(point, {"t": 5000.0, "v": 80.0, "a": -2.5, "b": None, "g": None})

    def test_accgx_zero_not_treated_as_absent(self):
        """accG_x == 0.0 is a valid measurement and must not be replaced with None."""
        row = {"timestamp_ms": 6000, "speedKmh": 90.0, "accG_x": 0.0}
        point = build_point(row)
        self.assertEqual(point, {"t": 6000.0, "v": 90.0, "a": 0.0, "b": None, "g": None})

    def test_returned_values_are_floats(self):
        row = {"timestamp_ms": 7000, "speedKmh": 120, "accG_x": 1, "brake": 1, "gas": 1}  # integer inputs
        point = build_point(row)
        self.assertIsInstance(point["t"], float)
        self.assertIsInstance(point["v"], float)
        self.assertIsInstance(point["a"], float)
        self.assertIsInstance(point["b"], float)
        self.assertIsInstance(point["g"], float)

    def test_accgx_none_when_key_missing(self):
        row = {"timestamp_ms": 8000, "speedKmh": 55.0}
        point = build_point(row)
        self.assertIsNone(point["a"])

    def test_brake_returned(self):
        row = {"timestamp_ms": 9000, "speedKmh": 100.0, "brake": 0.75}
        point = build_point(row)
        self.assertEqual(point, {"t": 9000.0, "v": 100.0, "a": None, "b": 0.75, "g": None})

    def test_no_brake_defaults_to_none(self):
        row = {"timestamp_ms": 10000, "speedKmh": 120.0}
        point = build_point(row)
        self.assertIsNone(point["b"])

    def test_brake_zero_not_treated_as_absent(self):
        """brake == 0.0 is a valid measurement and must not be replaced with None."""
        row = {"timestamp_ms": 11000, "speedKmh": 200.0, "brake": 0.0}
        point = build_point(row)
        self.assertEqual(point["b"], 0.0)

    def test_timestamp_fallback_uses_current_time(self):
        """When neither timestamp_ms nor Timestamp is present, time.time() is used."""
        fake_secs = 1_000_000.0
        with patch("main.time") as mock_time:
            mock_time.time.return_value = fake_secs
            row = {"speedKmh": 100.0}
            point = build_point(row)
        self.assertIsNotNone(point)
        self.assertAlmostEqual(point["t"], fake_secs * 1000, places=1)

    def test_gas_returned(self):
        row = {"timestamp_ms": 12000, "speedKmh": 150.0, "gas": 0.85}
        point = build_point(row)
        self.assertEqual(point, {"t": 12000.0, "v": 150.0, "a": None, "b": None, "g": 0.85})

    def test_no_gas_defaults_to_none(self):
        row = {"timestamp_ms": 13000, "speedKmh": 120.0}
        point = build_point(row)
        self.assertIsNone(point["g"])

    def test_gas_zero_not_treated_as_absent(self):
        """gas == 0.0 (no throttle) is a valid measurement and must not be replaced with None."""
        row = {"timestamp_ms": 14000, "speedKmh": 50.0, "gas": 0.0}
        point = build_point(row)
        self.assertEqual(point["g"], 0.0)

    def test_gas_full_throttle(self):
        row = {"timestamp_ms": 15000, "speedKmh": 280.0, "gas": 1.0}
        point = build_point(row)
        self.assertEqual(point["g"], 1.0)

    def test_all_channels_present(self):
        row = {"timestamp_ms": 16000, "speedKmh": 180.0, "accG_x": 1.5, "brake": 0.0, "gas": 0.9}
        point = build_point(row)
        self.assertEqual(point, {"t": 16000.0, "v": 180.0, "a": 1.5, "b": 0.0, "g": 0.9})


if __name__ == "__main__":
    unittest.main()
