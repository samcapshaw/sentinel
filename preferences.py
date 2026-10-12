"""Local-only Sentinel preferences and explicit, approximate location lookup."""
import copy
import json
import math
import os
from pathlib import Path
from urllib.parse import urlencode
from world_signals import request_json

DEFAULTS={'version':1,'mode':'world','location':None,'radius_km':150,'quake_radius_km':600,'news_query':'','units':'metric','sections':{'cameras':True,'flights':True,'roads':True,'earthquakes':True,'weather':True,'news':True,'satellites':True}}

def config_path():return Path(os.environ.get('XDG_CONFIG_HOME',str(Path.home()/'.config')))/'sentinel/settings.json'

def coordinates(lat,lon):
    lat,lon=float(lat),float(lon)
    if not math.isfinite(lat) or not math.isfinite(lon) or not -90<=lat<=90 or not -180<=lon<=180:raise ValueError('Invalid coordinates')
    return lat,lon

def validated(raw):
    result=copy.deepcopy(DEFAULTS)
    if not isinstance(raw,dict):return result
    result['mode']=raw.get('mode') if raw.get('mode') in ('world','local','auto') else 'world'
    for key,low,high in [('radius_km',25,500),('quake_radius_km',50,3000)]:
        try:result[key]=max(low,min(high,int(raw.get(key,result[key]))))
        except (ValueError,TypeError):pass
    result['news_query']=str(raw.get('news_query',''))[:180]
    result['units']='imperial' if raw.get('units')=='imperial' else 'metric'
    sections=raw.get('sections',{})
    if isinstance(sections,dict):
        for key in result['sections']:
            if isinstance(sections.get(key),bool):result['sections'][key]=sections[key]
    try:
        loc=raw['location'];lat,lon=coordinates(loc['lat'],loc['lon'])
        result['location']={'lat':lat,'lon':lon,**{key:str(loc.get(key,''))[:120] for key in ('name','region','country','query','source')}}
    except (KeyError,ValueError,TypeError):pass
    if result['mode']!='world' and not result['location']:result['mode']='world'
    return result

def load_settings(path=None):
    try:return validated(json.loads((path or config_path()).read_text()))
    except (OSError,ValueError):return copy.deepcopy(DEFAULTS)

def save_settings(settings,path=None):
    path=path or config_path();path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix('.tmp');temp.write_text(json.dumps(validated(settings),indent=2)+'\n');temp.chmod(0o600);temp.replace(path)

def is_local(settings):return settings['mode'] in ('local','auto') and bool(settings.get('location'))

def distance_km(lat,lon,other_lat,other_lon):
    p1,p2=map(math.radians,(lat,other_lat));delta=math.radians(other_lon-lon)
    a=math.sin((p2-p1)/2)**2+math.cos(p1)*math.cos(p2)*math.sin(delta/2)**2
    return 6371*2*math.asin(math.sqrt(min(1,max(0,a))))

def geocode(query):
    query=query.strip()
    # US ZIP lookup gives a postal-area center rather than a city's center.
    if len(query)==5 and query.isdigit():
        try:
            payload=request_json('https://api.zippopotam.us/us/'+query);p=payload['places'][0]
            lat,lon=coordinates(p['latitude'],p['longitude'])
            return [dict(name=p['place name']+' '+query,region=p['state'],country='US',lat=lat,lon=lon,query=query,source='Zippopotam.us postal-area center')]
        except (OSError,KeyError,IndexError,ValueError):pass
    payload=request_json('https://geocoding-api.open-meteo.com/v1/search?'+urlencode({'name':query,'count':10,'language':'en','format':'json'}))
    results=[]
    for p in payload.get('results',[]):
        try:
            lat,lon=coordinates(p['latitude'],p['longitude'])
            results.append(dict(name=p['name'],region=p.get('admin1',''),country=p.get('country_code',''),lat=lat,lon=lon,query=query,source='Open-Meteo / GeoNames'))
        except (KeyError,ValueError,TypeError):continue
    if not results:raise ValueError('No location found. Try a city and country, or US ZIP code.')
    return results

def detect_location():
    payload=request_json('https://ipwho.is/',timeout=6)
    if not payload.get('success'):raise ValueError('Automatic location lookup unavailable')
    lat,lon=coordinates(payload['latitude'],payload['longitude'])
    return dict(name=payload.get('city','Approximate location'),region=payload.get('region',''),country=payload.get('country_code',''),lat=lat,lon=lon,query='',source='ipwho.is / approximate public-IP location')

def runtime_settings():
    settings=load_settings()
    if settings['mode']=='auto':
        try:settings['location']=detect_location()
        except (OSError,ValueError,KeyError):pass # Saved approximate location remains usable.
    return settings

def local_cameras(cameras,settings):
    if not is_local(settings):return list(cameras)
    loc=settings['location'];near=sorted(cameras,key=lambda c:distance_km(loc['lat'],loc['lon'],c['lat'],c['lon']))
    near=[c for c in near if distance_km(loc['lat'],loc['lon'],c['lat'],c['lon'])<=settings['radius_km']]
    selected=[];counts={}
    for camera in near:
        route=camera.get('route')
        if route and counts.get(route,0)>=8:continue
        selected.append(camera)
        if route:counts[route]=counts.get(route,0)+1
        if len(selected)==32:break
    if len(selected)<32:selected.extend(c for c in near if c not in selected)
    return selected[:32]

# District centers choose nearby California feeds without probing every district.
DISTRICTS={1:(40.8,-124.1),2:(40.6,-122.4),3:(38.6,-121.5),4:(37.8,-122.3),5:(35.3,-120.7),6:(36.7,-119.8),7:(34.05,-118.24),8:(34.1,-117.3),9:(37.4,-118.4),10:(37.95,-121.3),11:(32.72,-117.16),12:(33.72,-117.9)}
def california_districts(settings):
    if not is_local(settings):return []
    loc=settings['location'];lat,lon=loc['lat'],loc['lon']
    if not 32<=lat<=42.1 or not -124.6<=lon<=-114:return []
    ranked=sorted(DISTRICTS,key=lambda d:distance_km(lat,lon,*DISTRICTS[d]))
    return [d for d in ranked[:3] if distance_km(lat,lon,*DISTRICTS[d])<settings['radius_km']+100]
