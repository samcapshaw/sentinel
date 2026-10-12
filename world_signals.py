"""Bounded public aviation and road data; all reported states retain timestamps."""
import json
import math
import threading
import time
import urllib.request


def request_json(url):
    request=urllib.request.Request(url,headers={'User-Agent':'Sentinel/1.0 (+https://github.com/samcapshaw/sentinel)'})
    with urllib.request.urlopen(request,timeout=15) as response:
        raw=response.read(8_000_001)
    if len(raw)>8_000_000:raise ValueError('Source exceeds response limit')
    return json.loads(raw)


def normalize_aircraft(payload):
    rows=[]
    for item in payload.get('ac',[]):
        try:
            if item.get('t')=='TWR':continue
            lat=float(item['lat']);lon=float(item['lon']);seen=float(item.get('seen_pos',0))
            if not all(math.isfinite(v) for v in (lat,lon,seen)) or not -90<=lat<=90 or not -180<=lon<=180 or not 0<=seen<=60:continue
            rows.append(dict(id=item['hex'],name=item.get('flight','').strip() or item.get('r') or item['hex'],lat=lat,lon=lon,altitude=item.get('alt_baro'),speed=item.get('gs'),heading=item.get('track'),seen=seen,type=item.get('t','')))
        except (KeyError,TypeError,ValueError):continue
    return rows[:250]


def normalize_roads(payload):
    rows=[]
    for item in payload:
        try:
            point=json.loads(item['point']);lon,lat=map(float,point)
            if not all(math.isfinite(v) for v in (lat,lon)) or not -90<=lat<=90 or not -180<=lon<=180:continue
            rows.append(dict(id=item['id'],lat=lat,lon=lon,severity=item.get('severity','Unknown'),category=item.get('category','Road disruption'),location=item.get('location',''),detail=item.get('currentUpdate',''),updated=item.get('currentUpdateDateTime','')))
        except (KeyError,TypeError,ValueError):continue
    return rows


class WorldSignals:
    def __init__(self):
        self.lock=threading.Lock();self.target=('London',51.5074,-.1278)
        self.data={'aircraft':[],'flight_region':'London','flight_center':(51.5074,-.1278),'flight_updated':0,'flight_status':'Loading ADSB.lol','roads':[],'road_updated':0,'road_status':'Loading TfL road disruptions'}
        threading.Thread(target=self.flights,daemon=True).start();threading.Thread(target=self.roads,daemon=True).start()

    def set_region(self,city,lat,lon):
        with self.lock:self.target=city,round(lat,3),round(lon,3)

    def snapshot(self):
        with self.lock:return dict(self.data)

    def flights(self):
        while True:
            with self.lock:city,lat,lon=self.target
            try:
                payload=request_json(f'https://api.adsb.lol/v2/lat/{lat}/lon/{lon}/dist/180')
                aircraft=normalize_aircraft(payload)
                with self.lock:self.data.update(aircraft=aircraft,flight_region=city,flight_center=(lat,lon),flight_updated=time.time(),flight_status='ADSB.lol / 180 NM radius / ODbL 1.0')
                time.sleep(20)
            except Exception as exc:
                with self.lock:self.data['flight_status']='Flight source unavailable: '+type(exc).__name__
                time.sleep(120)

    def roads(self):
        while True:
            try:
                payload=request_json('https://api.tfl.gov.uk/Road/all/Disruption')
                rows=normalize_roads(payload)
                with self.lock:self.data.update(roads=rows,road_updated=time.time(),road_status='Powered by TfL Open Data / London')
            except Exception as exc:
                with self.lock:self.data['road_status']='Road source unavailable: '+type(exc).__name__
            time.sleep(300)
