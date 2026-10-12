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


class Tracker:
    def __init__(self):
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
                selected = int(now//30)%len(objects) if objects else 0
                track = []
                if objects:
                    target = objects[selected]
                    satellite = next(sat for fields,sat in satellites if fields['NORAD_CAT_ID']==target['id'])
                    track = [position(satellite, now+offset) for offset in range(-2700,5401,60)]
                with self.lock:
                    self.data = {'objects': objects, 'track': track, 'selected': selected, 'status': status, 'updated': updated}
            except Exception as exc:
                with self.lock:
                    self.data['status'] = 'Orbit source unavailable: '+str(exc)[:90]
                time.sleep(7200)
            time.sleep(2)
