"""Bounded public aviation and road data; all reported states retain timestamps."""
import json
import math
import threading
import time
import urllib.request


def request_json(url,timeout=15):
    request=urllib.request.Request(url,headers={'User-Agent':'Sentinel/1.0 (+https://github.com/samcapshaw/sentinel)'})
    with urllib.request.urlopen(request,timeout=timeout) as response:
        raw=response.read(8_000_001)
    if len(raw)>8_000_000:raise ValueError('Source exceeds response limit')
    return json.loads(raw)


def finite_number(value):
    try:
        number=float(value)
        return number if math.isfinite(number) else None
    except (ValueError,TypeError):return None

def normalize_aircraft(payload):
    rows=[]
    for item in payload.get('ac',[]):
        try:
            if item.get('t')=='TWR':continue
            lat=float(item['lat']);lon=float(item['lon']);seen=float(item.get('seen_pos',0))
            if not all(math.isfinite(v) for v in (lat,lon,seen)) or not -90<=lat<=90 or not -180<=lon<=180 or not 0<=seen<=60:continue
            rows.append(dict(id=item['hex'],name=str(item.get('flight') or '').strip() or str(item.get('r') or item['hex']),lat=lat,lon=lon,altitude=item.get('alt_baro'),speed=item.get('gs'),heading=finite_number(item.get('track')),seen=seen,type=str(item.get('t') or '')))
        except (KeyError,TypeError,ValueError):continue
    return rows[:250]


def aircraft_position(aircraft,elapsed):
    """Short presentation estimate from reported heading/speed; never past 20s."""
    speed=finite_number(aircraft.get('speed'));heading=finite_number(aircraft.get('heading'))
    if speed is None or heading is None or speed<0:return aircraft['lon'],aircraft['lat']
    seconds=min(20,max(0,elapsed+aircraft.get('seen',0)));angle=speed*1.852*seconds/3600/6371
    lat,lon=map(math.radians,(aircraft['lat'],aircraft['lon']));bearing=math.radians(heading)
    new_lat=math.asin(max(-1,min(1,math.sin(lat)*math.cos(angle)+math.cos(lat)*math.sin(angle)*math.cos(bearing))))
    new_lon=lon+math.atan2(math.sin(bearing)*math.sin(angle)*math.cos(lat),math.cos(angle)-math.sin(lat)*math.sin(new_lat))
    return (math.degrees(new_lon)+180)%360-180,math.degrees(new_lat)


def normalize_roads(payload):
    rows=[]
    for item in payload:
        try:
            point=json.loads(item['point']);lon,lat=map(float,point)
            if not all(math.isfinite(v) for v in (lat,lon)) or not -90<=lat<=90 or not -180<=lon<=180:continue
            rows.append(dict(id=item['id'],lat=lat,lon=lon,severity=item.get('severity','Unknown'),category=item.get('category','Road disruption'),location=item.get('location',''),detail=item.get('currentUpdate',''),updated=item.get('currentUpdateDateTime','')))
        except (KeyError,TypeError,ValueError):continue
    return rows


def normalize_caltrans_roads(payload,settings,now=None):
    from preferences import coordinates,distance_km
    now=time.time() if now is None else now;loc=settings['location'];rows=[]
    for entry in payload.get('data',[]):
        try:
            r=entry['lcs'];where=r['location']['begin'];closure=r['closure'];times=closure['closureTimestamp']
            lat,lon=coordinates(where['beginLatitude'],where['beginLongitude'])
            if distance_km(loc['lat'],loc['lon'],lat,lon)>settings['radius_km']:continue
            if str(closure.get('code1022',{}).get('isCode1022','')).lower()=='true' or str(closure.get('code1098',{}).get('isCode1098','')).lower()=='true':continue
            begin=float(times['closureStartEpoch']);end=float(times['closureEndEpoch'])
            indefinite=str(times.get('isClosureEndIndefinite')).lower()=='true'
            if not indefinite and end<now:continue
            active=begin<=now and (indefinite or now<end)
            rows.append(dict(id=r['index'],lat=lat,lon=lon,severity='Active' if active else 'Planned',category=closure.get('typeOfClosure','Closure'),location=where['beginRoute']+' / '+where['beginLocationName']+' / '+where.get('beginNearbyPlace',''),detail=closure.get('typeOfWork','')+' / '+closure.get('lanesClosed','?')+' lanes / '+r['location'].get('travelFlowDirection',''),updated=times['closureStartDate']+' '+times['closureStartTime'],source_time_label='START / LOCAL TIME',starts=begin))
        except (KeyError,TypeError,ValueError):continue
    return rows


class WorldSignals:
    def __init__(self,settings=None,start=True):
        from preferences import DEFAULTS,is_local
        import copy
        self.settings=settings or copy.deepcopy(DEFAULTS);self.local=is_local(self.settings);self.stop=threading.Event()
        loc=self.settings['location'] if self.local else {'name':'London','lat':51.5074,'lon':-.1278}
        self.lock=threading.Lock();self.target=(loc['name'],loc['lat'],loc['lon'])
        self.radius_nm=min(250,max(25,round(self.settings['radius_km']/1.852))) if self.local else 180
        self.data={'aircraft':[],'flight_region':loc['name'],'flight_center':(loc['lat'],loc['lon']),'flight_radius_nm':self.radius_nm,'flight_updated':0,'flight_status':'Loading ADSB.lol','roads':[],'road_center':(loc['lat'],loc['lon']),'road_region':loc['name'],'road_updated':0,'road_status':'Loading road source'}
        if start and self.settings['sections']['flights']:threading.Thread(target=self.flights,daemon=True).start()
        if start and self.settings['sections']['roads']:threading.Thread(target=self.roads,daemon=True).start()

    def close(self):self.stop.set()
    def set_region(self,city,lat,lon):
        if self.local:return
        with self.lock:self.target=city,round(lat,3),round(lon,3)

    def snapshot(self):
        with self.lock:return dict(self.data)

    def flights(self):
        while not self.stop.is_set():
            with self.lock:city,lat,lon=self.target
            delay=20
            try:
                payload=request_json(f'https://api.adsb.lol/v2/lat/{lat}/lon/{lon}/dist/{self.radius_nm}')
                aircraft=normalize_aircraft(payload)
                with self.lock:self.data.update(aircraft=aircraft,flight_region=city,flight_center=(lat,lon),flight_updated=time.time(),flight_status=f'ADSB.lol / {self.radius_nm} NM radius / ODbL 1.0')
            except Exception as exc:
                with self.lock:self.data['flight_status']='Flight source unavailable: '+type(exc).__name__
                delay=120
            self.stop.wait(delay)

    def roads(self):
        from preferences import california_districts,distance_km
        from public_feeds import fetch
        districts=california_districts(self.settings)
        loc=self.settings.get('location')
        if self.local and not districts and distance_km(loc['lat'],loc['lon'],51.5074,-.1278)>150:
            with self.lock:self.data['road_status']='No supported road-disruption provider at this location'
            return
        while not self.stop.is_set():
            try:
                if districts:
                    rows=[];stamps=[];failed=0;stale=False
                    for district in districts:
                        try:
                            payload,stamp,cached=fetch(f'https://cwwp2.dot.ca.gov/data/d{district}/lcs/lcsStatusD{district:02}.json')
                            rows.extend(normalize_caltrans_roads(payload,self.settings));stamps.append(stamp);stale|=cached
                        except Exception:failed+=1
                    if not stamps:raise ValueError('Caltrans feeds unavailable')
                    status='Caltrans / active + scheduled closures / next 7 days'+(' / PARTIAL' if failed else '')+(' / CACHED / REFRESH FAILED' if stale else '')
                    center=(loc['lat'],loc['lon']);region=loc['name'];stamp=min(stamps)
                else:
                    payload,stamp,stale=fetch('https://api.tfl.gov.uk/Road/all/Disruption')
                    rows=normalize_roads(payload);center=(51.5074,-.1278);region='London'
                    status='Powered by TfL Open Data / London'+(' / CACHED / REFRESH FAILED' if stale else '')
                with self.lock:self.data.update(roads=rows,road_updated=stamp,road_center=center,road_region=region,road_status=status)
            except Exception as exc:
                with self.lock:self.data['road_status']='Road source unavailable: '+type(exc).__name__
            self.stop.wait(300)
