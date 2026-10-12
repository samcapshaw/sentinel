"""Refresh only nearby public Caltrans catalogs; no media is downloaded in bulk."""
import hashlib
import json
from pathlib import Path
import threading
import time
from preferences import california_districts,local_cameras
from world_signals import request_json


def parse_caltrans(payload):
    result=[]
    for entry in payload.get('data',[]):
        try:
            row=entry['cctv'];loc=row['location'];media=row['imageData']
            if str(row.get('inService')).lower()!='true':continue
            stream=media.get('streamingVideoURL','');image=media.get('static',{}).get('currentImageURL','')
            url=stream or image
            if not url.startswith('https://'):continue
            result.append(dict(city=loc.get('nearbyPlace') or loc.get('county') or 'California',view=loc['locationName'],provider='Caltrans District '+str(loc['district']),route=loc.get('route',''),lat=float(loc['latitude']),lon=float(loc['longitude']),media_url=url,kind='hls' if stream else 'image',url='https://cwwp2.dot.ca.gov/',fallback_url=image if stream else ''))
        except (KeyError,TypeError,ValueError):continue
    return result


class RegionalCameras:
    def __init__(self,settings):
        self.settings=settings;self.result=None;self.status='';self.lock=threading.Lock()
        key=hashlib.sha256(json.dumps(['v2',settings.get('location'),settings['radius_km']],sort_keys=True).encode()).hexdigest()[:16]
        self.path=Path.home()/'.cache/sentinel/cameras'/f'{key}.json'
        self.districts=california_districts(settings)
        if self.districts:threading.Thread(target=self.refresh,daemon=True).start()
    def refresh(self):
        try:
            if self.path.exists() and time.time()-self.path.stat().st_mtime<21600:
                result=json.loads(self.path.read_text())
            else:
                all_rows=[]
                for district in self.districts:
                    try:all_rows.extend(parse_caltrans(request_json(f'https://cwwp2.dot.ca.gov/data/d{district}/cctv/cctvStatusD{district:02}.json')))
                    except Exception:continue
                result=local_cameras(all_rows,self.settings)
                if not result:raise ValueError('No nearby Caltrans cameras available')
                self.path.parent.mkdir(parents=True,exist_ok=True);temp=self.path.with_suffix('.tmp');temp.write_text(json.dumps(result));temp.replace(self.path)
            with self.lock:self.result=result;self.status='Caltrans / nearby cameras'
        except Exception as exc:
            with self.lock:self.status='Nearby catalog unavailable: '+type(exc).__name__
    def take(self):
        with self.lock:result=self.result;self.result=None;return result
