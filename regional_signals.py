"""Official hazards and separately labeled media reports for World or local mode."""
import html,math,re,threading,time
from urllib.parse import urlencode
import xml.etree.ElementTree as ET
from preferences import is_local,distance_km,coordinates
from public_feeds import fetch

GDACS={'g':'http://www.gdacs.org','geo':'http://www.w3.org/2003/01/geo/wgs84_pos#'}

def plain(text):return ' '.join(html.unescape(re.sub('<[^>]+>',' ',text or '')).split())

def rss_rows(raw):
    rows=[]
    for item in ET.fromstring(raw).findall('.//item')[:100]:
        title=plain(item.findtext('title'));source=plain(item.findtext('source'))
        if source and title.endswith(' - '+source):title=title[:-len(source)-3]
        rows.append({'title':title,'detail':plain(item.findtext('description')),'published':item.findtext('pubDate',''),'url':item.findtext('link',''),'source':item.findtext('source','')})
    return rows

def normalize_quakes(payload,settings):
    rows=[];local=is_local(settings);loc=settings.get('location')
    for item in payload.get('features',[]):
        try:
            lon,lat,depth=item['geometry']['coordinates'];lat,lon=coordinates(lat,lon);p=item['properties'];magnitude=float(p['mag'])
            if not math.isfinite(magnitude):continue
            distance=distance_km(loc['lat'],loc['lon'],lat,lon) if local else None
            if local and distance>settings['quake_radius_km']:continue
            rows.append(dict(id=item['id'],lat=lat,lon=lon,depth=depth,mag=magnitude,title=plain(p.get('place')),time=float(p['time'])/1000,url=p.get('url',''),distance=distance))
        except (KeyError,ValueError,TypeError):continue
    return sorted(rows,key=lambda r:r['time'],reverse=True)[:80]

def normalize_cyclones(payload,settings):
    local=is_local(settings);loc=settings.get('location');rows=[]
    for p in payload.get('activeStorms',[]):
        try:
            lat,lon=coordinates(p['latitudeNumeric'],p['longitudeNumeric']);basin=p['id'][:2].lower()
            # NHC eastern Pacific is relevant to the US west coast even beyond a local radius.
            relevant=not local or (basin=='ep' and -180<=loc['lon']<-100) or distance_km(loc['lat'],loc['lon'],lat,lon)<4000
            if not relevant:continue
            rows.append(dict(name=p['name'],basin=basin,lat=lat,lon=lon,classification=p.get('classification',''),wind=p.get('intensity',''),pressure=p.get('pressure',''),heading=p.get('movementDir'),speed=p.get('movementSpeed'),updated=p.get('lastUpdate',''),url=p.get('publicAdvisory',{}).get('url','')))
        except (KeyError,ValueError,TypeError):continue
    return rows

def normalize_disasters(raw,settings):
    rows=[];local=is_local(settings);loc=settings.get('location')
    for item in ET.fromstring(raw).findall('.//item'):
        if item.findtext('g:iscurrent','',GDACS).lower()=='false':continue
        try:
            lat,lon=coordinates(item.findtext('.//geo:lat',namespaces=GDACS),item.findtext('.//geo:long',namespaces=GDACS))
            if local and distance_km(loc['lat'],loc['lon'],lat,lon)>max(1000,settings['quake_radius_km']):continue
            rows.append(dict(lat=lat,lon=lon,title=plain(item.findtext('title')),kind=item.findtext('g:eventtype','',GDACS),severity=item.findtext('g:alertlevel','',GDACS),published=item.findtext('pubDate',''),url=item.findtext('link','')))
        except (ValueError,TypeError):continue
    seen=set();unique=[]
    for row in rows:
        if row['url'] in seen:continue
        seen.add(row['url']);unique.append(row)
    return unique[:40]

class RegionalSignals:
    def __init__(self,settings,start=True):
        self.settings=settings;self.lock=threading.Lock();self.stop=threading.Event()
        self.data={key:[] for key in ('quakes','alerts','cyclones','disasters','outlook','news')};self.data['weather']={}
        for key in ('quakes','alerts','cyclones','disasters','outlook','news','weather'):self.data[key+'_updated']=0;self.data[key+'_status']='Loading source'
        sources=[];sections=settings['sections'];local=is_local(settings);loc=settings.get('location')
        if not sections['earthquakes']:self.data['quakes_status']='Earthquake source disabled'
        if not sections['news']:self.data['news_status']='News source disabled'
        if not sections['weather']:
            for key in ('weather','alerts','cyclones','disasters','outlook'):self.data[key+'_status']='Weather source disabled'
        if sections['earthquakes']:sources.append(('quakes',300,self.quakes))
        if sections['weather']:
            sources.extend([('cyclones',600,self.cyclones),('disasters',900,self.disasters),('outlook',600,self.outlook)])
            if local:
                sources.append(('weather',900,self.weather))
                if loc['country']=='US':sources.append(('alerts',300,self.alerts))
                else:self.data['alerts_status']='NWS point alerts cover the US; see GDACS for regional events'
            else:self.data['alerts_status']='World mode / global events shown through GDACS';self.data['weather_status']='World mode / regional conditions available in location mode'
        if sections['news']:sources.append(('news',900,self.news))
        if start:
            for key,interval,fn in sources:threading.Thread(target=self.worker,args=(key,interval,fn),daemon=True).start()
    def snapshot(self):
        with self.lock:return dict(self.data)
    def close(self):self.stop.set()
    def worker(self,key,interval,fn):
        while not self.stop.is_set():
            try:
                value,stamp,stale,label=fn()
                with self.lock:self.data.update({key:value,key+'_updated':stamp,key+'_status':label+(' / CACHED / REFRESH FAILED' if stale else '')})
            except Exception as exc:
                with self.lock:self.data[key+'_status']='Source unavailable: '+type(exc).__name__
            self.stop.wait(interval)
    def quakes(self):
        data,stamp,stale=fetch('https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/2.5_week.geojson')
        return normalize_quakes(data,self.settings),stamp,stale,'USGS / M2.5+ / past 7 days'
    def cyclones(self):
        data,stamp,stale=fetch('https://www.nhc.noaa.gov/CurrentStorms.json',600)
        return normalize_cyclones(data,self.settings),stamp,stale,'NOAA NHC / Atlantic + Eastern Pacific'
    def disasters(self):
        raw,stamp,stale=fetch('https://www.gdacs.org/xml/rss.xml',900,True)
        return normalize_disasters(raw,self.settings),stamp,stale,'GDACS / published global disaster events'
    def outlook(self):
        local=is_local(self.settings);loc=self.settings.get('location');ep=not local or loc['lon']<-100
        raw,stamp,stale=fetch('https://www.nhc.noaa.gov/xml/'+('TWOEP' if ep else 'TWOAT')+'.xml',600,True)
        rows=rss_rows(raw)[:1]
        if rows and 'Active Systems:' in rows[0]['detail']:rows[0]['detail']=rows[0]['detail'].split('Active Systems:',1)[1].strip()
        return rows,stamp,stale,'NHC / '+('Eastern Pacific' if ep else 'Atlantic')+' outlook'
    def alerts(self):
        loc=self.settings['location'];data,stamp,stale=fetch('https://api.weather.gov/alerts/active?'+urlencode({'point':f"{loc['lat']:.4f},{loc['lon']:.4f}"}))
        alerts=[]
        for item in data.get('features',[])[:30]:
            p=item.get('properties',{});alerts.append(dict(title=plain(p.get('event')),detail=plain(p.get('headline') or p.get('description')),severity=p.get('severity',''),published=p.get('sent',''),expires=p.get('expires',''),url=p.get('@id') or item.get('id','')))
        return alerts,stamp,stale,'NOAA NWS / active alerts at your location'
    def weather(self):
        loc=self.settings['location'];params={'latitude':loc['lat'],'longitude':loc['lon'],'current':'temperature_2m,precipitation,wind_speed_10m,weather_code','daily':'temperature_2m_max,temperature_2m_min,precipitation_probability_max,wind_speed_10m_max','forecast_days':3,'timezone':'auto'}
        if self.settings['units']=='imperial':params.update(temperature_unit='fahrenheit',wind_speed_unit='mph',precipitation_unit='inch')
        data,stamp,stale=fetch('https://api.open-meteo.com/v1/forecast?'+urlencode(params),900)
        return data,stamp,stale,'Open-Meteo / modeled current conditions + 3-day forecast'
    def news(self):
        query=self.settings['news_query'].strip()
        if not query and is_local(self.settings):
            loc=self.settings['location'];query='"'+re.sub(r'\s+\d{5}$','',loc['name'])+'" '+loc['region']
        url='https://news.google.com/rss/headlines/section/topic/WORLD?hl=en-US&gl=US&ceid=US:en'
        if query:url='https://news.google.com/rss/search?'+urlencode({'q':'('+query+') when:2d','hl':'en-US','gl':'US','ceid':'US:en'})
        raw,stamp,stale=fetch(url,900,True)
        return rss_rows(raw)[:40],stamp,stale,'Google News RSS / '+(query or 'World headlines')+' / publisher reports'
