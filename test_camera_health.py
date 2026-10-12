import unittest
from camera_health import offline_pixels,healthy_catalog
from unittest.mock import patch

class CameraHealthTests(unittest.TestCase):
    def test_provider_offline_card_is_rejected_without_rejecting_dark_roads(self):
        pixels=bytearray([255,255,255]*64*36)
        for y in range(14,19):
            for x in range(20,40):pixels[(y*64+x)*3:(y*64+x)*3+3]=bytes((0,0,128))
        self.assertTrue(offline_pixels(pixels,3,64*3))
        self.assertFalse(offline_pixels(bytes([25,25,30]*64*36),3,64*3))
        self.assertFalse(offline_pixels(bytes([255,255,255]*64*36),3,64*3))

    def test_catalog_skips_unavailable_frames_and_preserves_corridor_order(self):
        cameras=[{'view':str(i),'fallback_url':'https://example.com/'+str(i)} for i in range(64)]
        with patch('camera_health.probe_frame',side_effect=lambda url:int(url.rsplit('/',1)[1])>=5):
            healthy=healthy_catalog(cameras)
        self.assertEqual([row['view'] for row in healthy],[str(i) for i in range(5,64)])
