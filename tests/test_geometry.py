from __future__ import annotations

import unittest

from docwork.geometry import DisplayTransform, PixelBox


class DisplayTransformTests(unittest.TestCase):
    def test_crop_only(self) -> None:
        transform = DisplayTransform(200, 120, PixelBox(20, 10, 180, 110))
        box = transform.box(PixelBox(20, 10, 60, 40))
        self.assertEqual((box.left, box.top, box.right, box.bottom), (0, 0, .25, .3))

    def test_right_angle_rotations(self) -> None:
        source = PixelBox(20, 10, 60, 40)
        crop = PixelBox(0, 0, 200, 100)
        expected = {
            90: (.6, .1, .9, .3),
            180: (.7, .6, .9, .9),
            270: (.1, .7, .4, .9),
        }
        for rotation, coordinates in expected.items():
            with self.subTest(rotation=rotation):
                box = DisplayTransform(200, 100, crop, rotation).box(source)
                for observed, target in zip((box.left, box.top, box.right, box.bottom), coordinates):
                    self.assertAlmostEqual(observed, target)

    def test_crop_then_rotate(self) -> None:
        transform = DisplayTransform(200, 120, PixelBox(20, 10, 180, 110), 270)
        box = transform.box(PixelBox(20, 10, 60, 40))
        self.assertEqual((box.left, box.top, box.right, box.bottom), (0, .75, .3, 1))

    def test_reject_outside_crop_and_bad_rotation(self) -> None:
        with self.assertRaises(ValueError):
            DisplayTransform(100, 100, PixelBox(0, 0, 100, 100), 45)
        transform = DisplayTransform(100, 100, PixelBox(10, 10, 90, 90))
        with self.assertRaises(ValueError):
            transform.box(PixelBox(0, 0, 20, 20))


if __name__ == "__main__":
    unittest.main()
