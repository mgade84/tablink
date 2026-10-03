import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tablink import pipeline  # noqa: E402
from tablink.pipeline import Gst, describe  # noqa: E402


class DescribeTest(unittest.TestCase):
    """The pipeline description must parse with and without the optional h264parse."""

    def check(self, encoder, with_h264parse):
        needed = {"pipewiresrc", "x264enc", "videoconvert"} if encoder == "x264" else \
                 {"pipewiresrc", "vaapipostproc", "vaapih264enc"}
        missing = [e for e in needed if not Gst.ElementFactory.find(e)]
        if missing:
            self.skipTest(f"not installed: {', '.join(missing)}")
        real_have = pipeline.have
        with mock.patch.object(pipeline, "have",
                               lambda e: with_h264parse and real_have(e) if e == "h264parse" else real_have(e)):
            desc = describe(1, 1480, 924, 60, 12000, encoder)
        Gst.parse_launch(desc)  # raises on syntax errors like back-to-back caps filters
        self.assertIn("profile=constrained-baseline", desc)
        self.assertEqual("h264parse" in desc, with_h264parse and bool(real_have("h264parse")))

    def test_vaapi_without_h264parse(self):
        self.check("vaapi", False)

    def test_vaapi_with_h264parse(self):
        self.check("vaapi", True)

    def test_x264_without_h264parse(self):
        self.check("x264", False)

    def test_x264_with_h264parse(self):
        self.check("x264", True)


if __name__ == "__main__":
    unittest.main()
