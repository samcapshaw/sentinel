"""Public CelesTrak OMM elements, propagated locally with SGP4."""
import json
import math
from pathlib import Path
import threading
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone


def position(satellite, timestamp):
    from sgp4.api import jday
    from sgp4.propagation import gstime
    d = datetime.fromtimestamp(timestamp, timezone.utc)
    jd, fraction = jday(d.year, d.month, d.day, d.hour, d.minute, d.second+d.microsecond/1e6)
    error, vector, _ = satellite.sgp4(jd, fraction)
    if error:
        return None
    theta = gstime(jd+fraction)
    x, y, z = vector
    longitude = (math.atan2(y, x)-theta+math.pi) % math.tau-math.pi
    radius = math.hypot(x, y)
    latitude = math.atan2(z, radius)
    for _ in range(8):
        n = 6378.137 / math.sqrt(1-0.00669437999014*math.sin(latitude)**2)
        latitude = math.atan2(z+0.00669437999014*n*math.sin(latitude), radius)
    altitude = radius/math.cos(latitude)-n
    return math.degrees(longitude), math.degrees(latitude), altitude


def fetch_json(url, filename, ttl=21600):
    cache = Path.home()/'.cache/sentinel'/filename
    if cache.exists() and time.time()-cache.stat().st_mtime < ttl:
        return json.loads(cache.read_text()), cache.stat().st_mtime
    blocked = cache.with_suffix('.blocked')
    if blocked.exists():
        if cache.exists():return json.loads(cache.read_text()), cache.stat().st_mtime
        raise RuntimeError(blocked.read_text())
    request = urllib.request.Request(url, headers={'User-Agent': 'Sentinel/1.0 public-dashboard'})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = response.read(20_000_001)
        if len(raw)>20_000_000:
            raise ValueError('Feed exceeds size limit')
        value = json.loads(raw)
        cache.parent.mkdir(parents=True, exist_ok=True)
        temporary = cache.with_suffix('.tmp')
        temporary.write_bytes(raw)
        temporary.replace(cache)
        return value, time.time()
    except Exception as exc:
        if isinstance(exc, urllib.error.HTTPError):
            cache.parent.mkdir(parents=True, exist_ok=True)
            blocked.write_text('HTTP '+str(exc.code)+'; automatic requests stopped. Check source before deleting '+str(blocked))
        if cache.exists():
            return json.loads(cache.read_text()), cache.stat().st_mtime
        raise


def look_angles(point,observer):
    """Spherical-Earth look angles for ambient pass context, not navigation."""
    lon,lat,alt=point;phi1,phi2=map(math.radians,(observer['lat'],lat));delta=math.radians(lon-observer['lon'])
    cosine=max(-1,min(1,math.sin(phi1)*math.sin(phi2)+math.cos(phi1)*math.cos(phi2)*math.cos(delta)))
    angle=math.acos(cosine);radius=6371+alt
    elevation=math.degrees(math.atan2(radius*cosine-6371,radius*math.sin(angle)))
    azimuth=math.degrees(math.atan2(math.sin(delta)*math.cos(phi2),math.cos(phi1)*math.sin(phi2)-math.sin(phi1)*math.cos(phi2)*math.cos(delta)))%360
    distance=math.sqrt(max(0,radius*radius+6371**2-2*radius*6371*cosine))
    return {'elevation':elevation,'azimuth':azimuth,'range':distance}


def next_pass(satellite,observer,now,minutes=90):
    start=None;peak=0
    for offset in range(0,minutes*60+1,30):
        point=position(satellite,now+offset)
        if point is None:continue
        elevation=look_angles(point,observer)['elevation']
        if elevation>=10:
            if start is None:start=now+offset
            peak=max(peak,elevation)
        elif start is not None:return {'start':start,'end':now+offset,'peak':peak}
    return {'start':start,'end':None,'peak':peak} if start is not None else None


class Tracker:
    def __init__(self,observer=None):
        self.observer=observer
        self.lock = threading.Lock()
        self.data = {'objects': [], 'track': [], 'status': 'Loading CelesTrak orbital elements', 'updated': 0}
        threading.Thread(target=self.run, daemon=True).start()

    def snapshot(self):
        with self.lock:
            return dict(self.data)

    def run(self):
        try:
            from sgp4.api import Satrec
            from sgp4 import omm
        except ImportError:
            with self.lock:
                self.data['status'] = 'Install sgp4 to enable orbital tracking'
            return
        satellites = []
        refreshed = 0
        pass_updated=0;passes=[]
        while True:
            try:
                if time.time()-refreshed>21600:
                    collection = []
                    dates = []
                    failures = []
                    for group in ('stations', 'starlink'):
                        try:
                            fields, updated = fetch_json('https://celestrak.org/NORAD/elements/gp.php?GROUP='+group+'&FORMAT=json', group+'.json')
                            dates.append(updated)
                            for entry in fields[:64 if group=='starlink' else 12]:
                                sat = Satrec()
                                omm.initialize(sat, entry)
                                collection.append((entry, sat))
                        except Exception as exc:
                            failures.append(group+': '+type(exc).__name__)
                    if not collection:
                        raise RuntimeError('; '.join(failures))
                    satellites = collection
                    refreshed = time.time()
                    updated = min(dates)
                    status = 'SGP4 / CelesTrak OMM'+(' / '+', '.join(failures) if failures else '')
                now = time.time()
                objects = []
                for fields, sat in satellites:
                    point = position(sat, now)
                    if point:
                        objects.append({'name': fields['OBJECT_NAME'], 'id': fields['NORAD_CAT_ID'], 'epoch': fields['EPOCH'], 'point': point})
                selected = int(now//8)%len(objects) if objects else 0
                if objects and self.observer:
                    from preferences import distance_km
                    nearby=sorted(range(len(objects)),key=lambda i:distance_km(self.observer['lat'],self.observer['lon'],objects[i]['point'][1],objects[i]['point'][0]))[:5]
                    selected=nearby[int(now//8)%len(nearby)]
                    status='SGP4 / nearest ground positions to '+self.observer['name']+' / tracked subset'
                if self.observer:
                    for obj in objects:obj['look']=look_angles(obj['point'],self.observer)
                    if now-pass_updated>=60:
                        passes=[]
                        for fields,sat in satellites:
                            prediction=next_pass(sat,self.observer,now)
                            if prediction:passes.append({'name':fields['OBJECT_NAME'],'id':fields['NORAD_CAT_ID'],**prediction})
                        passes.sort(key=lambda p:p['start']);pass_updated=now
                track = []
                if objects:
                    target = objects[selected]
                    satellite = next(sat for fields,sat in satellites if fields['NORAD_CAT_ID']==target['id'])
                    track = [position(satellite, now+offset) for offset in range(-2700,5401,60)]
                with self.lock:
                    self.data = {'objects': objects, 'track': track, 'selected': selected, 'status': status, 'updated': updated,'passes':passes[:12],'pass_updated':pass_updated,'sampled':now}
            except Exception as exc:
                with self.lock:
                    self.data['status'] = 'Orbit source unavailable: '+str(exc)[:90]
                time.sleep(7200)
            time.sleep(1)
