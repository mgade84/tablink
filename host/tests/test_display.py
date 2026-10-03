import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tablink.display import Box, _layout  # noqa: E402

DELL = ("DP-1", "DEL", "DELL P2314H", "X")
TAB = ("Meta-0", "MetaVendor", "Virtual remote monitor", "0x000001")
MONS = {DELL: {"mode": "1920x1080@60", "size": (1920, 1080)},
        TAB: {"mode": "1480x924@60", "size": (1480, 924)}}


def boxes(tab_x=1920, tab_y=0, tab_scale=1.0):
    return [Box((0, 0, 1.0, 0, True, [DELL], {}), MONS, 1),
            Box((tab_x, tab_y, tab_scale, 0, False, [TAB], {}), MONS, 1)]


def pos(bs):
    return {b.specs[0][0]: (b.x, b.y) for b in bs}


class LayoutTest(unittest.TestCase):
    def test_left_shifts_main_display_right(self):
        bs = boxes()
        self.assertTrue(_layout(bs, "left"))
        self.assertEqual(pos(bs), {"DP-1": (1480, 0), "Meta-0": (0, 0)})

    def test_right_is_noop_when_already_there(self):
        self.assertFalse(_layout(boxes(), "right"))

    def test_above_and_below(self):
        bs = boxes()
        _layout(bs, "above")
        self.assertEqual(pos(bs), {"DP-1": (0, 924), "Meta-0": (0, 0)})
        bs = boxes()
        _layout(bs, "below")
        self.assertEqual(pos(bs), {"DP-1": (0, 0), "Meta-0": (0, 1080)})

    def test_logical_size_accounts_for_scale(self):
        bs = boxes(tab_scale=2.0)
        _layout(bs, "left")
        self.assertEqual(pos(bs), {"DP-1": (740, 0), "Meta-0": (0, 0)})


if __name__ == "__main__":
    unittest.main()
