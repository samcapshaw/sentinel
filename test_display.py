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
        self.assertEqual([scene_at(t)[0] for t in (0,24,46,70,96,120)],[0,1,2,3,4,4])
        self.assertEqual(scene_at(-1),(0,0,24))

    def test_all_scenes_and_crossfades_render(self):
        display=Display()
        frames=[]
        for t in (0,12,23.3,24,35,45.3,46,69.3,70,77,89,95.3,96):
            surface=cairo.ImageSurface(cairo.FORMAT_ARGB32,1600,1000)
            display.paint(cairo.Context(surface),1600,1000,t)
            frames.append(bytes(surface.get_data()))
        self.assertTrue(all(a!=b for a,b in zip(frames,frames[1:])))
