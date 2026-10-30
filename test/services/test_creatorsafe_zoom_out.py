import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image
from moviepy import VideoFileClip

from app.models.schema import ImageMotion, VideoParams
from app.services import video


class ZoomOutTests(unittest.TestCase):
    def test_new_mode_is_explicit_and_existing_default_is_preserved(self):
        self.assertEqual(VideoParams(video_subject="test").image_motion, ImageMotion.zoom)
        self.assertEqual(VideoParams(video_subject="test", image_motion="zoom_out").image_motion,
                         ImageMotion.zoom_out)

    def test_native_zoom_out_preserves_canvas_and_reveals_more_of_image(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "gradient.png"
            pixels = np.zeros((640, 480, 3), dtype=np.uint8)
            pixels[:, :, 0] = np.linspace(0, 255, 480).astype(np.uint8)
            pixels[:, :, 1] = 120
            pixels[:, :, 2] = 80
            Image.fromarray(pixels).save(source)
            rendered = video.render_image_zoom_video(str(source), 2, "zoom_out")
            with VideoFileClip(rendered) as clip:
                self.assertEqual(clip.size, [480, 640])
                first, last = clip.get_frame(0.05), clip.get_frame(1.95)
                self.assertGreater(float(first[:, :16, 0].mean()), float(last[:, :16, 0].mean()) + 2)
                for frame in (first, last):
                    self.assertGreater(float(frame[:, :, 1].min()), 90)


if __name__ == "__main__":
    unittest.main()
