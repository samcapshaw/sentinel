import unittest
import cairo
from display import Display, scene_at

class DisplayTests(unittest.TestCase):
    def test_render_at_hidpi_and_preview_sizes(self):
        display=Display()
        for width,height in ((3840,2160),(3240,2160),(1920,1080),(480,270)):
            surface=cairo.ImageSurface(cairo.FORMAT_ARGB32,width,height)
            cr=cairo.Context(surface)
            display.paint(cr,width,height,17)
            self.assertEqual(cr.status() if hasattr(cr,'status') else cairo.STATUS_SUCCESS,cairo.STATUS_SUCCESS)
            self.assertTrue(any(surface.get_data()))

    def test_scene_sequence_and_wrap(self):
        self.assertEqual([scene_at(t)[0] for t in (0,54,76,172,198,228,264,298,340,376)],[0,1,2,3,4,5,6,7,8,0])
        self.assertEqual(scene_at(-1),(0,0,54))

    def test_all_scenes_and_crossfades_render(self):
        display=Display()
        frames=[]
        for t in (0,12,53.3,54,75.3,76,171.3,172,197.3,198,227.3,228,263.3,264,297.3,298,339.3,340,375.3,376):
            surface=cairo.ImageSurface(cairo.FORMAT_ARGB32,1600,1000)
            display.paint(cairo.Context(surface),1600,1000,t)
            frames.append(bytes(surface.get_data()))
        self.assertTrue(all(a!=b for a,b in zip(frames,frames[1:])))
