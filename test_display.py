import unittest
import cairo
from display import Display, scene_at, SCENES

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
        from itertools import accumulate
        boundaries=[0,*accumulate(duration for _,duration in SCENES)]
        self.assertEqual([scene_at(t)[0] for t in boundaries],list(range(len(SCENES)))+[0])
        self.assertEqual(scene_at(-1),(0,0,SCENES[0][1]))

    def test_all_scenes_and_crossfades_render(self):
        display=Display()
        frames=[]
        from itertools import accumulate
        times=[0,12]
        for boundary in accumulate(duration for _,duration in SCENES):times.extend((boundary-.7,boundary))
        for t in times:
            surface=cairo.ImageSurface(cairo.FORMAT_ARGB32,1600,1000)
            display.paint(cairo.Context(surface),1600,1000,t)
            frames.append(bytes(surface.get_data()))
        self.assertTrue(all(a!=b for a,b in zip(frames,frames[1:])))
