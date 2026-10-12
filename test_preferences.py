import copy,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from preferences import DEFAULTS,validated,save_settings,load_settings,local_cameras,geocode
from regional_signals import normalize_quakes,normalize_cyclones,normalize_disasters,rss_rows
from world_signals import WorldSignals,normalize_caltrans_roads
from display import Display,scene_at


def local_settings():
    settings=copy.deepcopy(DEFAULTS);settings.update(mode='local',location={'name':'Test City','region':'Test Region','country':'US','lat':34.,'lon':-118.,'query':'','source':'test'})
    return settings

class LocationTests(unittest.TestCase):
    def test_default_is_world_and_invalid_coordinates_cannot_localize(self):
        self.assertEqual(validated({})['mode'],'world')
        self.assertIsNone(DEFAULTS['location'])
        self.assertEqual(validated({'mode':'local','location':{'lat':float('nan'),'lon':0}})['mode'],'world')
    def test_roundtrip_is_private_and_clamps_radii(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'settings.json';settings=local_settings();settings['radius_km']=100000
            save_settings(settings,path);loaded=load_settings(path)
            self.assertEqual(loaded['location']['name'],'Test City');self.assertEqual(loaded['radius_km'],500)
            self.assertEqual(path.stat().st_mode&0o777,0o600)
    def test_world_keeps_catalog_local_excludes_distant_cameras(self):
        cameras=[{'lat':34.,'lon':-118.},{'lat':51.5,'lon':-.1}]
        self.assertEqual(len(local_cameras(cameras,DEFAULTS)),2)
        self.assertEqual(local_cameras(cameras,local_settings()),cameras[:1])
    def test_geocoding_exposes_ambiguous_results_for_selection(self):
        payload={'results':[{'name':'Paris','latitude':48.8,'longitude':2.3,'country_code':'FR'},{'name':'Paris','latitude':33.6,'longitude':-95.5,'country_code':'US'}]}
        with patch('preferences.request_json',return_value=payload):
            self.assertEqual([row['country'] for row in geocode('Paris')],['FR','US'])
    def test_local_flights_do_not_follow_camera_city(self):
        signal=WorldSignals(local_settings(),start=False);signal.set_region('London',51.5,-.1)
        self.assertEqual(signal.target,('Test City',34.,-118.))
        signal=WorldSignals(start=False);signal.set_region('Paris',48.8,2.3)
        self.assertEqual(signal.target,('Paris',48.8,2.3))
    def test_disabled_screens_are_skipped_including_crossfade_order(self):
        settings=local_settings();settings['sections'].update(cameras=False,flights=False,weather=False,earthquakes=False,news=False)
        display=Display(settings=settings)
        self.assertNotIn(0,display.enabled);self.assertNotIn(7,display.enabled)
        self.assertEqual(scene_at(0,display.enabled)[0],1)
        self.assertEqual(scene_at(22,display.enabled)[0],3)

class RegionalFeedTests(unittest.TestCase):
    def test_quake_radius_filter_and_original_timestamp(self):
        def quake(lon,lat):return {'id':str(lon),'geometry':{'coordinates':[lon,lat,8]},'properties':{'mag':3.2,'place':'Test quake','time':1234000}}
        rows=normalize_quakes({'features':[quake(-118,34),quake(0,0)]},local_settings())
        self.assertEqual(len(rows),1);self.assertEqual(rows[0]['time'],1234)
    def test_eastern_pacific_cyclones_are_kept_beyond_local_radius(self):
        payload={'activeStorms':[{'id':'ep012026','latitudeNumeric':15,'longitudeNumeric':-110,'name':'Test Storm'},{'id':'al012026','latitudeNumeric':20,'longitudeNumeric':-40,'name':'Distant Storm'}]}
        rows=normalize_cyclones(payload,local_settings());self.assertEqual([r['name'] for r in rows],['Test Storm'])
    def test_rss_is_text_only_and_preserves_publisher(self):
        raw='<rss><channel><item><title>&lt;b&gt;Headline&lt;/b&gt;</title><source>Publisher</source><pubDate>Yesterday</pubDate></item></channel></rss>'
        self.assertEqual(rss_rows(raw)[0],{'title':'Headline','detail':'','published':'Yesterday','url':'','source':'Publisher'})
    def test_caltrans_labels_future_and_active_and_drops_finished(self):
        def closure(start,end):
            return {'lcs':{'index':str(start),'location':{'begin':{'beginLatitude':'34','beginLongitude':'-118','beginRoute':'I-5','beginLocationName':'Test Exit'}},'closure':{'closureTimestamp':{'closureStartEpoch':str(start),'closureEndEpoch':str(end),'closureStartDate':'2026-10-12','closureStartTime':'09:00'}}}}
        rows=normalize_caltrans_roads({'data':[closure(90,110),closure(110,120),closure(70,80)]},local_settings(),now=100)
        self.assertEqual([r['severity'] for r in rows],['Active','Planned'])

class LocalRenderingTests(unittest.TestCase):
    def test_every_live_workspace_renders_without_network(self):
        import cairo
        from types import SimpleNamespace
        from regional_signals import RegionalSignals
        settings=local_settings();display=Display(settings=settings);display.live=True
        telemetry={'connections':[],'interfaces':[],'bps':0,'pps':0,'load':0,'memory':0,'events':[],'kev':[],'kev_status':'Test catalog','kev_updated':0,'status':'Test'}
        display.telemetry=SimpleNamespace(snapshot=lambda:telemetry)
        display.tracker=SimpleNamespace(snapshot=lambda:{'objects':[],'track':[],'status':'Test orbit','updated':0})
        display.signals=WorldSignals(settings,start=False);display.regional=RegionalSignals(settings,start=False)
        display.regional.data['weather']={'current':{'time':'2026-10-12T09:00','temperature_2m':20,'wind_speed_10m':5,'precipitation':0},'current_units':{'temperature_2m':'°C','wind_speed_10m':'km/h','precipitation':'mm'},'daily':{'time':['2026-10-12'],'temperature_2m_min':[10],'temperature_2m_max':[20],'precipitation_probability_max':[5]}}
        display.regional.data['news']=[{'title':'Example headline','source':'Example publisher','published':'Today','url':'https://example.com','detail':''}]
        display.tiles=SimpleNamespace(draw=lambda *args,**kwargs:None)
        for t in (17,60,90,180,210,245,280,312,350):
            surface=cairo.ImageSurface(cairo.FORMAT_ARGB32,1600,1000)
            display.paint(cairo.Context(surface),1600,1000,t)
            self.assertTrue(any(surface.get_data()))
