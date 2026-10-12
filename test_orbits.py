import math
import unittest
from datetime import datetime, timezone
from sgp4.api import Satrec
from orbits import position

class OrbitTests(unittest.TestCase):
    def test_reference_orbit_and_ground_track(self):
        # Vallado's published SGP4 verification satellite 00005.
        sat=Satrec.twoline2rv('1 00005U 58002B   00179.78495062  .00000023  00000-0  28098-4 0  4753','2 00005  34.2682 348.7242 1859667 331.7664  19.3264 10.82419157413667')
        stamp=datetime(2000,6,27,18,50,19,733568,tzinfo=timezone.utc).timestamp()
        points=[position(sat,stamp+offset) for offset in (0,60,3600)]
        for lon,lat,alt in points:
            self.assertTrue(-180<=lon<=180)
            self.assertTrue(-90<=lat<=90)
            self.assertTrue(100<alt<5000)
            self.assertTrue(all(math.isfinite(v) for v in (lon,lat,alt)))
        self.assertGreater(abs(points[0][0]-points[1][0]),.1)

    def test_equatorial_earth_fixed_conversion(self):
        class Satellite:
            def sgp4(self,jd,fraction):return 0,(7000,0,0),(0,7,0)
        lon,lat,alt=position(Satellite(),946728000)
        self.assertAlmostEqual(lat,0)
        self.assertAlmostEqual(alt,621.863,places=3)

class PassTests(unittest.TestCase):
    def test_look_angles_overhead_and_far_side(self):
        from orbits import look_angles
        observer={'lat':0,'lon':0}
        overhead=look_angles((0,0,500),observer)
        self.assertAlmostEqual(overhead['elevation'],90)
        self.assertAlmostEqual(overhead['range'],500)
        self.assertLess(look_angles((180,0,500),observer)['elevation'],0)
    def test_pass_prediction_uses_geometric_threshold_and_source_times(self):
        from unittest.mock import patch
        from orbits import next_pass
        with patch('orbits.position',side_effect=[(90,0,500),(0,0,500),(90,0,500)]):
            predicted=next_pass(None,{'lat':0,'lon':0},1000,minutes=1)
        self.assertEqual(predicted['start'],1030)
        self.assertEqual(predicted['end'],1060)
        self.assertAlmostEqual(predicted['peak'],90)
