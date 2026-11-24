import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageStat
from everytensor import media


class DisplayPreviewTests(unittest.TestCase):
    def test_draft_block_rounding_does_not_change_display_size(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'odd-height.jpg'
            Image.new('RGB', (1600, 1077)).save(path)
            preview = media.preview_image(path, {}, (176, 132), display_thumbnail=True)
            self.assertEqual(preview.size, (176, 118))

    def test_decoder_thumbnail_preserves_orientation_and_rendered_dimensions(self):
        photo = Image.new('RGB', (1025, 769), 'white')
        draw = ImageDraw.Draw(photo)
        draw.rectangle((0, 0, 511, 383), fill='red')
        draw.rectangle((512, 384, 1024, 768), fill='navy')
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'rotated.jpg'
            for orientation in range(1, 9):
                with self.subTest(orientation=orientation):
                    exif = Image.Exif()
                    exif[274] = orientation
                    photo.save(path, exif=exif)
                    original = media.preview_image(path, {}, (127, 101))
                    preview = media.preview_image(path, {}, (127, 101), display_thumbnail=True)
                    self.assertEqual(preview.info['original_size'], original.info['original_size'])
                    self.assertEqual(preview.size, original.size)
                    diff = ImageStat.Stat(ImageChops.difference(preview, original))
                    self.assertLess(max(diff.mean), 2.0)
