import unittest
from cartography import mercator, orthographic
from world_signals import normalize_aircraft, normalize_roads

class WorldTests(unittest.TestCase):
    def test_projection_center_and_hidden_hemisphere(self):
        self.assertEqual(mercator(0,0,0),(128,128))
        self.assertEqual(orthographic(0,0,0,0),(0,0,1))
        self.assertLess(orthographic(180,0,0,0)[2],0)
    def test_aircraft_rejects_invalid_and_stale_positions(self):
        valid={'hex':'123abc','lat':51.5,'lon':-.1,'flight':' TEST1 ','seen_pos':2}
        rows=normalize_aircraft({'ac':[valid,dict(valid,lat=91),dict(valid,lon=float('nan')),dict(valid,seen_pos=61),dict(valid,seen_pos=-1),{}]})
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['name'],'TEST1')
    def test_road_coordinates_and_source_timestamp(self):
        good={'id':'road','point':'[-0.1,51.5]','currentUpdateDateTime':'2026-10-11T12:00:00Z'}
        rows=normalize_roads([good,dict(good,point='[0,100]'),dict(good,point='invalid'),{}])
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['updated'],good['currentUpdateDateTime'])
