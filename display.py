#!/usr/bin/env python3
"""High-DPI vector display for Sentinel's live and demonstration telemetry."""
import argparse
import math
import os
from pathlib import Path
import signal
import sys
import time
import cairo
import gi
gi.require_version('Gtk','3.0')
from gi.repository import Gtk, Gdk, GLib
from sentinel import Dashboard, LAND, NODES, palette

SCENES = (("GLOBAL OBSERVATION",54),("NETWORK",22),("CITY CAMERAS",96),("THREAT INTEL",26),("ORBITAL",30),("AVIATION",36),("ROAD CONDITIONS",34),("NATURAL HAZARDS",42),("MEDIA EVENTS",36))

def scene_at(t,enabled=None):
    active=[(i,row) for i,row in enumerate(SCENES) if enabled is None or i in enabled] or [(1,SCENES[1])]
    moment=max(0,t)%sum(row[1] for _,row in active)
    for index,(_,duration) in active:
        if moment<duration:return index,moment,duration
        moment-=duration
    return 0,0,SCENES[0][1]

class Display:
    def __init__(self,live=False,settings=None):
        from preferences import DEFAULTS,is_local
        import copy
        self.settings=settings or copy.deepcopy(DEFAULTS);self.local=is_local(self.settings)
        sections=self.settings['sections'];self.enabled=[i for i in range(len(SCENES)) if {0:sections['cameras'],2:sections['cameras'],4:sections['satellites'],5:sections['flights'],6:sections['roads'],7:sections['earthquakes'] or sections['weather'],8:sections['news']}.get(i,True)]
        self.live=live
        if live:
            from live import Telemetry
            from orbits import Tracker
            self.telemetry=Telemetry()
            if sections['satellites']:self.tracker=Tracker(observer=self.settings['location'] if self.local else None)
            else:
                from types import SimpleNamespace
                self.tracker=SimpleNamespace(snapshot=lambda:{'objects':[],'track':[],'status':'Satellite source disabled','updated':0})
        from cartography import Globe
        self.globe=Globe();self.orbit_globe=Globe()
        if live:
            from cartography import Tiles
            from world_signals import WorldSignals
            from regional_signals import RegionalSignals
            self.tiles=Tiles();self.signals=WorldSignals(self.settings);self.regional=RegionalSignals(self.settings)
        self.colors=palette();self.model=Dashboard();self.start=time.monotonic();self.tick=-1
    def color(self,cr,key,alpha=1):
        h=self.colors[key].lstrip('#');cr.set_source_rgba(*(int(h[i:i+2],16)/255 for i in (0,2,4)),alpha)
    def text(self,cr,x,y,value,size=11,color='foreground',alpha=1):
        self.color(cr,color,alpha);cr.select_font_face('JetBrains Mono',cairo.FONT_SLANT_NORMAL,cairo.FONT_WEIGHT_NORMAL);cr.set_font_size(size);cr.move_to(x,y);cr.show_text(str(value))
    def line(self,cr,x1,y1,x2,y2,color='muted',alpha=.55,width=.65):
        self.color(cr,color,alpha);cr.set_line_width(width);cr.move_to(x1,y1);cr.line_to(x2,y2);cr.stroke()
    def dot(self,cr,x,y,r=2,color='accent',alpha=1):
        self.color(cr,color,alpha);cr.arc(x,y,r,0,math.tau);cr.fill()
    def panel(self,cr,x,y,w,h,number,title,right=''):
        self.color(cr,'foreground',.018);cr.rectangle(x,y,w,h);cr.fill()
        self.color(cr,'muted',.48);cr.set_line_width(.7);cr.rectangle(x+.5,y+.5,w-1,h-1);cr.stroke()
        self.text(cr,x+18,y+25,number,10,'accent');self.text(cr,x+47,y+25,title,10,'bright_foreground')
        if right:self.text(cr,x+w-len(right)*6-18,y+25,right,9,'dark_foreground')
        self.line(cr,x,y+42,x+w,y+42,alpha=.35)
    def paint(self,cr,width,height,t):
        scale=min(width/1600,height/1000)
        self.color(cr,'background');cr.paint();cr.save();cr.translate((width-1600*scale)/2,(height-1000*scale)/2);cr.scale(scale,scale)
        if int(t*2)!=self.tick:self.tick=int(t*2);self.model.advance(t)
        self.text(cr,32,40,'S E N T I N E L',18,'bright_foreground')
        scene,phase,duration=scene_at(t,self.enabled)
        self.text(cr,266,39,'/ '+SCENES[scene][0],10,'dark_foreground')
        self.dot(cr,1312,35,2,'green');self.text(cr,1324,39,'LIVE' if self.live else 'DEMO',9,'green')
        from datetime import datetime,timezone
        self.text(cr,1430,39,datetime.now(timezone.utc).strftime('%H:%M:%S UTC'),10,'accent')
        self.line(cr,32,61,1568,61)
        metrics=[('SENSOR MESH','07','EDGES ONLINE','accent'),('ACTIVE FLOWS',str(248+int(17*math.sin(t/4))),'CORRELATED SESSIONS','foreground'),('INGEST RATE',f'{1.28+.08*math.sin(t/6):.2f}','Gb/s  /  SYNTHETIC','cyan'),('EXPOSURE SCORE','18','LOW  /  BASELINE NORMAL','green')]
        if self.live:
            self.current=self.telemetry.snapshot();self.orbits=self.tracker.snapshot();self.world_data=self.signals.snapshot();self.regional_data=self.regional.snapshot()
            data=self.current
            metrics=[('NETWORK INTERFACES',str(len(data['interfaces'])),'LOCAL / READ ONLY','accent'),('CONNECTIONS',str(len(data['connections'])),'ESTABLISHED TCP + UDP','foreground'),('NETWORK RATE',f"{data['bps']/1e6:.2f}",'Mb/s / INTERFACE COUNTERS','cyan'),('MEMORY USED',f"{data['memory']:.0f}%",'LOAD '+str(round(data['load'],2)),'green')]
        for i,(label,value,note,col) in enumerate(metrics):
            x=34+i*390
            self.text(cr,x,88,label,9,'dark_foreground');self.text(cr,x,125,value,29,col)
            self.text(cr,x+94,120,note,9,'dark_foreground')
            if i<3:self.line(cr,x+365,78,x+365,132,alpha=.3)
        # Render the outgoing and incoming workspace into separate Cairo groups.
        # The final 1.4 seconds blend them without flashing or changing type scale.
        blend=max(0,min(1,(phase-(duration-1.4))/1.4))
        blend=blend*blend*(3-2*blend)
        cr.push_group();self.workspace(cr,scene,t,phase);current=cr.pop_group()
        cr.set_source(current);cr.paint_with_alpha(1-blend)
        if blend:
            cr.push_group();self.workspace(cr,self.enabled[(self.enabled.index(scene)+1)%len(self.enabled)],t,0);incoming=cr.pop_group()
            cr.set_source(incoming);cr.paint_with_alpha(blend)
        for tab,i in enumerate(self.enabled):
            label=SCENES[i][0];x=32+tab*1536/len(self.enabled)
            self.text(cr,x,959,label,8,'accent' if i==scene else 'dark_foreground')
            self.line(cr,x,965,x+160,965,alpha=.35)
            if i==scene:self.line(cr,x,965,x+160*(phase/duration),965,'accent',.8,1)
        self.text(cr,32,975,('LIVE METADATA / PUBLIC FEEDS / NO PACKET PAYLOAD CAPTURE' if self.live else 'DEMO TELEMETRY / CAMERA SIMULATION'),9,'dark_foreground')
        self.text(cr,1210,975,'S: SETTINGS  /  MOVE MOUSE TO DISMISS',9,'dark_foreground')
        cr.restore()
    def workspace(self,cr,scene,t,phase):
        if self.live:
            self.live_workspace(cr,scene,t,phase);return
        if scene>=4:
            self.panel(cr,32,155,1536,787,"05","ORBITAL TRACKING","DEMO / NETWORK DISABLED")
            self.text(cr,62,245,"Use live mode to load public ISS and Starlink orbit data.",14);return
        if scene==0:self.overview(cr,t)
        elif scene==1:self.traffic(cr,t,phase)
        elif scene==2:self.camera_array(cr,t)
        else:self.investigation(cr,t,phase)
        if scene in (0,1) and 8<phase<18:self.inspector(cr,t,phase,scene)

    def live_workspace(self,cr,scene,t,phase):
        data=self.current
        if scene==4:
            self.orbital(cr,t);return
        if scene==5:
            self.aviation(cr,t);return
        if scene==6:
            self.road_conditions(cr,t);return
        if scene==7:
            self.hazards(cr,t);return
        if scene==8:
            self.media_events(cr,t);return
        if scene in (0,2):
            self.camera_map(cr,t);return
        if scene==3:
            self.panel(cr,32,155,1536,787,'04','KNOWN EXPLOITED VULNERABILITIES','CISA / PUBLIC CATALOG')
            self.text(cr,58,225,data['kev_status'],11,'accent')
            age=(time.time()-data['kev_updated'])/3600 if data['kev_updated'] else None
            self.text(cr,58,251,('Cache age %.1f hours'%age) if age is not None else 'Waiting for public source',10,'dark_foreground')
            for i,item in enumerate(data['kev'][:9]):
                y=310+i*61
                self.text(cr,58,y,item['cveID'],12,'cyan');self.text(cr,250,y,(item['vendorProject']+' / '+item['product'])[:110],11)
                self.text(cr,250,y+22,'Added '+item['dateAdded']+' / '+item['vulnerabilityName'][:135],9,'dark_foreground')
            self.text(cr,58,915,'Catalog inclusion describes known exploitation globally; it is not a finding about this machine.',10,'dark_foreground')
            return
        self.panel(cr,32,155,950,787,'01','ESTABLISHED CONNECTIONS','ss / SOCKET METADATA / NO PAYLOADS')
        self.text(cr,52,220,'TRANSPORT   LOCAL ENDPOINT                         REMOTE ENDPOINT',10,'dark_foreground')
        for i,(proto,local,remote) in enumerate(data['connections'][:25]):
            y=258+i*24
            self.text(cr,52,y,proto,10,'cyan');self.text(cr,155,y,local[:43],10);self.text(cr,500,y,remote[:55],10)
        if not data['connections']:self.text(cr,52,260,data['status'],11,'dark_foreground')
        self.panel(cr,998,155,570,355,'02','HOST TELEMETRY','READ ONLY / TWO SECOND SAMPLE')
        values=[('Interfaces',', '.join(data['interfaces'])),('Packets / second',f"{data['pps']:.0f}"),('Network / Mb/s',f"{data['bps']/1e6:.3f}"),('System load / 1 min',f"{data['load']:.2f}"),('Memory used',f"{data['memory']:.1f}%")]
        for i,(label,value) in enumerate(values):
            y=225+i*51;self.text(cr,1020,y,label,10,'dark_foreground');self.text(cr,1270,y,value[:38],11,'accent')
        self.panel(cr,998,526,570,416,'03','CONNECTION ACTIVITY','OBSERVED CHANGES / LOCAL CLOCK')
        for i,(stamp,message) in enumerate(data['events'][:12]):
            y=594+i*25;self.text(cr,1016,y,stamp,9,'dark_foreground');self.text(cr,1090,y,message[:70],9)

    def camera_map(self,cr,t):
        viewer=getattr(self,'camera_viewer',None);cameras=viewer.cameras if viewer else []
        selected=viewer.index if viewer and viewer.index>=0 else 0
        loc=self.settings.get('location') if self.local else None
        cam=cameras[selected] if cameras else {'city':loc['name'] if loc else 'WORLD','view':'No nearby camera loaded / waiting for supported catalog','provider':'','lon':loc['lon'] if loc else 0,'lat':loc['lat'] if loc else 20}
        status=viewer.status if viewer else 'WAITING FOR VIEWER'
        self.panel(cr,32,155,632,787,'01','GEOSPATIAL OBSERVATION','ORTHOGRAPHIC / WGS84')
        self.text(cr,58,237,cam['city'].upper(),24,'bright_foreground')
        self.text(cr,58,266,cam['view'][:65],10,'accent')
        markers=[];marked=set()
        for i,item in enumerate(cameras):
            if i!=selected and item['city'] in marked:continue
            marked.add(item['city']);markers.append(dict(lon=item['lon'],lat=item['lat'],active=i==selected,label=item['city']))
        self.globe.draw(self,cr,348,493,211,cam['lon'],cam['lat']*.8,t,markers)
        self.text(cr,58,739,'GROUND CONTEXT / AERIAL IMAGERY',9,'dark_foreground')
        self.tiles.draw(self,cr,58,755,580,157,cam['lon'],cam['lat'],14,[dict(lon=cam['lon'],lat=cam['lat'],color='green')])
        # An independent video surface overlays this frame only after playback settles.
        self.panel(cr,680,236,850,578,'↗',cam['city'].upper()+' / '+cam['view'][:42].upper(),status)
        self.text(cr,710,520,'ACQUIRING OBSERVATION…',14,'accent')
        self.text(cr,700,788,cam['provider'][:95]+' / MUTED',9,'dark_foreground')
        self.text(cr,690,194,'O P T I C A L   O B S E R V A T I O N',11,'bright_foreground')
        self.text(cr,690,220,('LOCAL FOCUS / '+self.settings['location']['name'].upper()+' / '+str(self.settings['radius_km'])+' KM') if self.local else 'WORLD MODE / PUBLIC SOURCES / PRELOADED HANDOFF',9,'dark_foreground')
        for i,(label,value,color) in enumerate([('CAMERA POSITION',f"{cam['lat']:.4f}° / {cam['lon']:.4f}°",'accent'),('COVERAGE',f"{len(cameras)} SOURCES / {len({c['city'] for c in cameras})} CITIES",'cyan'),('NEXT OBSERVATION',('%02d seconds'%max(0,45-(time.monotonic()-viewer.started))) if viewer else 'WAITING','green')]):
            x=690+i*292;self.text(cr,x,859,label,9,'dark_foreground');self.text(cr,x,886,value,12,color)
        self.text(cr,690,928,'BASEMAP IS STORED AERIAL IMAGERY / CAMERA MEDIA IS LABELED BY TYPE',9,'dark_foreground')

    def aviation(self,cr,t):
        data=self.world_data;aircraft=data['aircraft'];lat,lon=data['flight_center']
        age=time.time()-data['flight_updated'] if data['flight_updated'] else None
        self.panel(cr,32,155,1000,787,'01','AIRSPACE OBSERVATION',data['flight_region'].upper()+' / '+str(data['flight_radius_nm'])+' NM')
        markers=[dict(lon=a['lon'],lat=a['lat'],heading=a['heading'],label=a['name'] if i<14 else '',color='cyan') for i,a in enumerate(aircraft)]
        self.tiles.draw(self,cr,48,211,968,535,lon,lat,7,markers)
        self.text(cr,58,783,data['flight_status'],10,'accent')
        self.text(cr,58,810,('RECEIVED %.0f SECONDS AGO'%age) if age is not None else 'WAITING FOR PUBLIC AVIATION SOURCE',10,'dark_foreground')
        self.text(cr,58,839,'Reported ADS-B positions / coverage varies / no route or destination inferred',10,'dark_foreground')
        for i,a in enumerate(aircraft[:3]):
            y=875+i*22;self.text(cr,58,y,f"{a['name']:<12} {str(a['altitude']):>7} ft   {str(a['speed']):>6} kt   {a['type']}",11,'foreground')
        self.panel(cr,1048,155,520,787,'02','LIVE CONTACTS',str(len(aircraft))+' REPORTED')
        for i,a in enumerate(aircraft[:25]):
            y=224+i*27;self.text(cr,1068,y,a['name'][:11],11,'bright_foreground');self.text(cr,1195,y,('GROUND' if a['altitude']=='ground' else str(a['altitude'])+' ft' if a['altitude'] is not None else '—'),10,'accent');self.text(cr,1310,y,(str(a['speed'])+' kt' if a['speed'] is not None else '—'),10,'cyan');self.text(cr,1420,y,a['type'][:10],10,'dark_foreground')
        self.text(cr,1068,922,'ADSB.lol / ODbL 1.0 / poll every 20s',9,'dark_foreground')

    def road_conditions(self,cr,t):
        data=self.world_data;roads=data['roads'];age=time.time()-data['road_updated'] if data['road_updated'] else None
        self.panel(cr,32,155,1000,787,'01','ROAD CONDITIONS',data['road_region'].upper()+' / PUBLISHED REPORTS')
        lat,lon=data['road_center']
        markers=[dict(lon=r['lon'],lat=r['lat'],color='red' if r['severity'].lower() in ('serious','severe','active') else 'yellow') for r in roads]
        self.tiles.draw(self,cr,48,211,968,610,lon,lat,11 if self.local else 12,markers)
        self.text(cr,58,856,data['road_status'],11,'accent')
        self.text(cr,58,885,f"{len(roads)} published disruptions / "+(('received %.0fs ago'%age) if age is not None else 'waiting for source'),10,'dark_foreground')
        self.text(cr,58,915,'Real incident locations / no simulated vehicle movement or inferred congestion speeds',10,'dark_foreground')
        self.panel(cr,1048,155,520,787,'02','ROAD BULLETIN','FIVE MINUTE REFRESH')
        ordered=sorted(roads,key=lambda r:0 if r['severity'].lower() in ('serious','severe','active') else 1)
        offset=(int(t/12)*6)%max(1,len(ordered))
        for i,r in enumerate((ordered+ordered)[offset:offset+6]):
            y=225+i*110;self.text(cr,1068,y,r['severity'].upper()+' / '+r['category'],11,'yellow');self.text(cr,1068,y+25,r['location'][:65],9,'bright_foreground');self.text(cr,1068,y+47,r['detail'][:65],9,'foreground');self.text(cr,1068,y+69,r['updated'][:19]+' / '+r.get('source_time_label','SOURCE UPDATE'),8,'dark_foreground')

    def wrapped(self,cr,x,y,text,width=72,lines=3,size=11,color='foreground',spacing=20):
        import textwrap
        for i,line in enumerate(textwrap.wrap(str(text),width=width)[:lines]):self.text(cr,x,y+i*spacing,line,size,color)

    def source_age(self,data,key):
        stamp=data[key+'_updated']
        stale=' / STALE' if stamp and ('CACHED' in data[key+'_status'] or 'unavailable' in data[key+'_status']) else ''
        return (('received %.0f min ago'%max(0,(time.time()-stamp)/60))+stale) if stamp else 'waiting for source'

    def hazards(self,cr,t):
        data=self.regional_data;sections=self.settings['sections'];loc=self.settings.get('location') if self.local else None
        region=loc['name'].upper() if loc else 'WORLD'
        quakes=data['quakes'] if sections['earthquakes'] else [];storms=data['cyclones'] if sections['weather'] else [];disasters=data['disasters'] if sections['weather'] else []
        self.panel(cr,32,155,1000,787,'01','NATURAL HAZARDS',region+' / OFFICIAL SOURCES')
        markers=[dict(lat=r['lat'],lon=r['lon'],color='yellow',label='M%.1f'%r['mag']) for r in quakes[:30]]
        markers += [dict(lat=r['lat'],lon=r['lon'],color='magenta',label=r['name']) for r in storms]
        markers += [dict(lat=r['lat'],lon=r['lon'],color='red' if r['severity']=='Red' else 'accent',label=r['kind']) for r in disasters[:15]]
        if loc:
            markers.append(dict(lat=loc['lat'],lon=loc['lon'],color='green',label='LOCAL FOCUS'))
            basin=bool(storms) and int(t/14)%2==1
            storm=storms[int(t/28)%len(storms)] if basin else None
            center_lon=(loc['lon']+storm['lon'])/2 if storm else loc['lon'];center_lat=(loc['lat']+storm['lat'])/2 if storm else loc['lat']
            self.tiles.draw(self,cr,48,211,968,365,center_lon,center_lat,3 if basin else 5,markers)
            self.text(cr,65,234,'BASIN CONTEXT / ACTIVE CYCLONE POSITIONS' if basin else 'REGIONAL CONTEXT / REPORTED EVENTS',9,'bright_foreground')
        else:self.globe.draw(self,cr,530,397,181,-110+t*.4,18,t,markers)
        key='quakes' if sections['earthquakes'] else 'disasters'
        self.text(cr,58,602,data[key+'_status']+' / '+self.source_age(data,key),9,'dark_foreground')
        weather=data['weather'];current=weather.get('current',{});units=weather.get('current_units',{});daily=weather.get('daily',{})
        if current and sections['weather']:
            self.text(cr,58,640,'MODELED CONDITIONS / '+current.get('time','')+' LOCAL',9,'dark_foreground')
            for i,(label,value,unit) in enumerate([('TEMPERATURE',current.get('temperature_2m','—'),units.get('temperature_2m','')),('WIND',current.get('wind_speed_10m','—'),units.get('wind_speed_10m','')),('PRECIPITATION',current.get('precipitation','—'),units.get('precipitation',''))]):
                x=58+i*315;self.text(cr,x,664,label,9,'dark_foreground');self.text(cr,x,698,str(value)+' '+unit,24,'cyan')
            for i,date in enumerate(daily.get('time',[])[:3]):
                value=lambda field:daily.get(field,['—']*3)[i]
                self.text(cr,58+i*315,727,date[5:]+f" / {value('temperature_2m_min')}–{value('temperature_2m_max')}° / rain {value('precipitation_probability_max')}%",10,'foreground')
            self.text(cr,58,751,'Open-Meteo / '+self.source_age(data,'weather'),9,'dark_foreground')
        else:
            self.text(cr,58,658,'WEATHER / GLOBAL DISASTER OBSERVATION' if not loc else data['weather_status'],11,'cyan')
            for i,r in enumerate(disasters[:3]):self.wrapped(cr,58,689+i*24,r['title'],width=120,lines=1,size=10)
        self.line(cr,58,771,1006,771)
        if sections['weather']:
            self.text(cr,58,795,data['outlook_status']+' / '+self.source_age(data,'outlook'),10,'magenta')
            if data['outlook']:
                item=data['outlook'][0];self.wrapped(cr,58,820,item['detail'],width=125,lines=4,size=10,spacing=21)
                self.text(cr,58,922,'Issued '+item['published'][:48]+' / BASIN OUTLOOK, NOT A LOCAL WARNING',8,'dark_foreground')
        self.panel(cr,1048,155,520,787,'02','REGIONAL BULLETIN','ALERTS / SEISMIC / CYCLONES')
        self.text(cr,1068,222,'WEATHER ALERTS',10,'accent')
        alerts=data['alerts']
        if sections['weather']:
            if alerts:
                alert=alerts[int(t/12)%len(alerts)];self.wrapped(cr,1068,248,alert['title']+' / '+alert['severity'],width=53,lines=2,size=12,color='yellow');self.wrapped(cr,1068,292,alert['detail'],width=66,lines=3,size=10)
                self.text(cr,1068,358,alert['published'][:25],9,'dark_foreground')
            else:self.wrapped(cr,1068,249,'Last NWS response: no active alerts at this point.' if data['alerts_updated'] else data['alerts_status'],width=63,lines=3,size=10)
            self.text(cr,1068,383,'NWS / '+self.source_age(data,'alerts'),9,'dark_foreground')
        self.line(cr,1068,405,1548,405)
        self.text(cr,1068,435,'EARTHQUAKES / M2.5+ / PAST 7 DAYS',10,'accent')
        self.text(cr,1068,459,(str(self.settings['quake_radius_km'])+' KM RADIUS / ' if loc else 'WORLD / ')+self.source_age(data,'quakes'),9,'dark_foreground')
        if quakes:
            offset=(int(t/10)*5)%len(quakes)
            from datetime import datetime,timezone
            for i,r in enumerate((quakes+quakes)[offset:offset+min(5,len(quakes))]):
                y=488+i*39;self.text(cr,1068,y,'M%.1f'%r['mag'],12,'yellow');self.text(cr,1120,y,r['title'][:58],9,'foreground')
                stamp=datetime.fromtimestamp(r['time'],timezone.utc).strftime('%m-%d %H:%M UTC')
                self.text(cr,1120,y+16,stamp+((' / %.0f km away'%r['distance']) if r['distance'] is not None else ''),8,'dark_foreground')
        else:self.text(cr,1068,492,'No events in this feed / radius' if data['quakes_updated'] else data['quakes_status'],9,'dark_foreground')
        self.line(cr,1068,699,1548,699);self.text(cr,1068,725,'TROPICAL CYCLONES / NHC',10,'magenta')
        for i,r in enumerate(storms[:3]):
            y=754+i*48;self.text(cr,1068,y,r['name']+' / '+r['classification']+' / '+str(r['wind'])+' kt',11,'bright_foreground')
            self.text(cr,1068,y+19,r['basin'].upper()+' / '+r['updated'][:19]+' UTC',9,'dark_foreground')
        if not storms:self.wrapped(cr,1068,755,'No relevant active systems returned.' if data['cyclones_updated'] else data['cyclones_status'],width=64,lines=2,size=10)
        self.text(cr,1068,910,'NHC / '+self.source_age(data,'cyclones'),9,'dark_foreground')

    def media_events(self,cr,t):
        data=self.regional_data;news=data['news'];loc=self.settings.get('location') if self.local else None
        self.panel(cr,32,155,1000,787,'01','MEDIA EVENTS','PUBLISHER REPORTS / NOT OFFICIAL ALERTS')
        self.text(cr,58,228,'L O C A L   S I G N A L' if loc else 'W O R L D   S I G N A L',11,'accent')
        if news:
            index=int(t/12)%len(news);item=news[index]
            self.wrapped(cr,58,282,item['title'],width=65,lines=4,size=21,color='bright_foreground',spacing=33)
            self.text(cr,58,430,item['source'][:80],12,'cyan');self.text(cr,58,459,item['published'][:70],10,'dark_foreground')
            self.line(cr,58,490,1006,490)
            for i,item in enumerate((news+news)[index+1:index+6]):
                y=540+i*75;self.wrapped(cr,58,y,item['title'],width=110,lines=2,size=11,spacing=20);self.text(cr,58,y+44,item['source'][:65]+' / '+item['published'][:31],8,'dark_foreground')
        else:self.text(cr,58,290,data['news_status'],12,'dark_foreground')
        self.panel(cr,1048,155,520,787,'02','OBSERVATION FOCUS',self.settings['mode'].upper())
        markers=[dict(lon=loc['lon'],lat=loc['lat'],active=True,label=loc['name'])] if loc else []
        self.globe.draw(self,cr,1308,438,181,loc['lon'] if loc else -110+t*.5,loc['lat'] if loc else 20,t,markers)
        self.wrapped(cr,1072,683,loc['name']+', '+loc['region'] if loc else 'Worldwide headline selection',width=43,lines=2,size=15,color='bright_foreground',spacing=24)
        self.wrapped(cr,1072,750,data['news_status'],width=62,lines=3,size=10,color='accent')
        self.text(cr,1072,824,self.source_age(data,'news')+' / 15 MIN REFRESH',9,'dark_foreground')
        self.text(cr,1072,852,str(len(news))+' HEADLINES / RSS SOURCE ORDER',9,'dark_foreground')
        self.wrapped(cr,1072,891,'News relevance comes from the selected query. Headlines do not confirm hazards or measure social-media popularity.',width=62,lines=3,size=9,color='dark_foreground',spacing=16)

    def orbital(self,cr,t):
        data=self.orbits
        self.panel(cr,32,155,1100,787,'05','ORBITAL OBSERVATION','SGP4 / ISS + STARLINK / ESTIMATED POSITIONS')
        self.panel(cr,1148,155,420,787,'06','ORBIT ELEMENTS','PUBLIC / CELESTRAK')
        target=data['objects'][data.get('selected',0)] if data['objects'] else None
        lon,lat=(target['point'][:2] if target else (0,20))
        markers=[dict(lon=o['point'][0],lat=o['point'][1],active=i==data.get('selected',0),label=o['name']) for i,o in enumerate(data['objects'])]
        self.orbit_globe.draw(self,cr,580,510,295,lon,lat*.8,t,markers,data['track'])
        self.text(cr,60,235,'E A R T H   O R B I T   O B S E R V A T I O N',12,'bright_foreground')
        self.text(cr,60,262,'ISS / STARLINK / PROPAGATED GROUND POSITIONS',10,'dark_foreground')
        self.text(cr,58,865,data['status'][:135],10,'accent')
        self.text(cr,58,892,'Ground track: 45 minutes behind / 90 minutes ahead. Selection changes every 30 seconds.',9,'dark_foreground')
        self.text(cr,58,915,'Positions are calculated from orbital elements; this is not direct satellite telemetry.',10,'dark_foreground')
        if data['objects']:
            obj=data['objects'][data.get('selected',0)];lon,lat,alt=obj['point']
            rows=[('OBJECT',obj['name']),('NORAD ID',str(obj['id'])),('LATITUDE',f'{lat:.3f}°'),('LONGITUDE',f'{lon:.3f}°'),('ALTITUDE',f'{alt:.1f} km'),('ELEMENT EPOCH',obj['epoch']),('DISPLAYED SUBSET',str(len(data['objects']))+' objects')]
            for i,(label,value) in enumerate(rows):
                yy=225+i*78;self.text(cr,1170,yy,label,9,'dark_foreground');self.text(cr,1170,yy+25,value[:42],12,'bright_foreground')
        if data['updated']:
            self.text(cr,1170,840,'CACHE AGE',9,'dark_foreground');self.text(cr,1170,865,f"{(time.time()-data['updated'])/3600:.1f} hours",12,'accent')
            self.text(cr,1170,900,'Elements refresh every six hours',9,'dark_foreground')

    def overview(self,cr,t):
        y=155;h=438;left=950;right=570;rx=998
        self.panel(cr,32,y,left,h,'01','GLOBAL SENSOR MESH','EQUIRECTANGULAR / WGS84')
        self.world(cr,52,y+55,left-40,h-90,t)
        self.panel(cr,rx,y,right,h,'02','OVERWATCH','PROCEDURAL / CAM-07')
        self.camera(cr,rx+16,y+55,right-32,h-80,t)
        by=609;bh=333
        self.panel(cr,32,by,left,bh,'03','PACKET INSPECTOR','REPLAY / ETH0 / ENCRYPTED FLOWS')
        self.packets(cr,50,by+65,left-36,bh-72,t)
        self.panel(cr,rx,by,right,bh,'04','CORRELATION STREAM','RULE ENGINE / REPLAY')
        self.events(cr,rx+18,by+67,right-36,bh-75,t)

    def traffic(self,cr,t,phase):
        self.panel(cr,32,155,950,787,'01','FLOW EXPLORER','AUTOMATIC SESSION FOLLOW')
        self.packets(cr,50,224,914,680,t)
        self.panel(cr,998,155,570,355,'02','PROTOCOL DISTRIBUTION','ROLLING WINDOW / 60s')
        protocols=[('TLS 1.3',.54,'accent'),('QUIC / HTTP3',.23,'magenta'),('DNS',.12,'cyan'),('SSH',.07,'green'),('OTHER',.04,'dark_foreground')]
        for i,(label,weight,color) in enumerate(protocols):
            yy=227+i*49;value=weight*(1+.07*math.sin(t*.3+i))
            self.text(cr,1020,yy,label,10);self.text(cr,1495,yy,f'{value*100:.1f}%',10,color)
            self.line(cr,1020,yy+14,1540,yy+14,alpha=.3,width=3)
            self.line(cr,1020,yy+14,1020+520*value,yy+14,color,.75,3)
        self.panel(cr,998,526,570,416,'03','SESSION ACTIVITY','BEHAVIOR / CORRELATION')
        self.events(cr,1016,594,534,318,t)

    def camera_array(self,cr,t):
        for i in range(4):
            x=32+(i%2)*776;y=155+(i//2)*402
            self.panel(cr,x,y,760,385,f'{i+1:02}','OPTICAL OBSERVATION',f'CAM-{7+i:02} / VECTOR REPLAY')
            self.camera(cr,x+16,y+52,728,310,t+i*19,sector=i)

    def investigation(self,cr,t,phase):
        stage=min(3,int(phase/6))
        stages=['SIGNAL ACQUIRED','SESSION CORRELATED','POLICY VERIFIED','CASE RESOLVED']
        self.panel(cr,32,155,950,438,'01','INVESTIGATION GRAPH','AUTONOMOUS CORRELATION')
        points=[(190,350),(390,270),(390,440),(610,350),(790,270),(790,440)]
        names=['EDGE-07','DNS RELAY','TLS SESSION','POLICY ENGINE','CERT STORE','BASELINE']
        links=[(0,1),(0,2),(1,3),(2,3),(3,4),(3,5)]
        for j,(a,b) in enumerate(links):
            pa,pb=points[a],points[b]
            self.line(cr,*pa,*pb,'cyan',.35,1)
            f=(t*.23+j*.19)%1;self.dot(cr,pa[0]+(pb[0]-pa[0])*f,pa[1]+(pb[1]-pa[1])*f,2,'cyan')
        for i,((x,y),name) in enumerate(zip(points,names)):
            active=i<=stage+2
            self.color(cr,'background');cr.arc(x,y,24,0,math.tau);cr.fill()
            self.color(cr,'accent' if active else 'muted',.8);cr.set_line_width(.8);cr.arc(x,y,24,0,math.tau);cr.stroke()
            self.text(cr,x-8,y+4,f'{i+1:02}',11,'accent' if active else 'dark_foreground')
            self.text(cr,x-len(name)*3,y+48,name,9,'foreground')
        self.text(cr,60,548,'CASE '+str(1000+int(t/96))+'  /  '+stages[stage],10,'green' if stage==3 else 'accent')
        self.panel(cr,998,155,570,438,'02','CASE TIMELINE','SIMULATED WORKFLOW')
        for i,label in enumerate(stages):
            yy=235+i*80;color='green' if i<stage else ('accent' if i==stage else 'dark_foreground')
            self.dot(cr,1028,yy,3,color)
            self.text(cr,1050,yy+4,label,10,color)
            self.text(cr,1050,yy+27,['Sensor reports a replayed traffic burst','Linking flow, resolver and certificate','Checking expected egress and trust chain','Baseline matched; investigation archived'][i],9,'dark_foreground')
            if i<3:self.line(cr,1028,yy+10,1028,yy+67,alpha=.4)
        self.panel(cr,32,609,1536,333,'03','ANALYST CONSOLE','READ-ONLY / SYNTHETIC ENVIRONMENT')
        commands=[('sentinel correlate --sensor EDGE-07','Loading sensor metadata and replay window…'),('session inspect --flow 0248 --summary','TLS 1.3 / certificate chain valid / payload encrypted'),('policy verify --scope egress','Expected route · resolver verified · no deviation'),('case archive --reason baseline-match','Complete. Returning to observation workspace.')]
        for i,(command,response) in enumerate(commands):
            if phase<i*6:break
            yy=685+i*61;typed=command[:int(max(0,phase-i*6)*24)]
            self.text(cr,58,yy,'observer@sentinel  ~  $',10,'cyan');self.text(cr,232,yy,typed,11,'bright_foreground')
            if phase-i*6>1.8:self.text(cr,232,yy+22,response,10,'green' if i==3 else 'dark_foreground')
            elif int(t*2)%2==0:self.text(cr,232+len(typed)*6.6,yy,'▏',11,'accent')

    def inspector(self,cr,t,phase,scene):
        visibility=min(1,(phase-8)/.7,(18-phase)/.7)
        visibility=visibility*visibility*(3-2*visibility)
        cr.save();cr.translate(28*(1-visibility),0);cr.push_group()
        x,y,w,h=1120,338,420,418
        self.color(cr,'background',.98);cr.rectangle(x-6,y-6,w+12,h+12);cr.fill()
        self.panel(cr,x,y,w,h,'↗','SESSION INSPECTOR','AUTO FOLLOW')
        self.text(cr,x+20,y+77,'FLOW / 0248',15,'bright_foreground')
        for i,(label,value) in enumerate([('SOURCE','10.24.6.109'),('DESTINATION','192.0.2.92:443'),('TRANSPORT','TLS 1.3 / encrypted'),('CERTIFICATE','CHAIN VERIFIED'),('POLICY','EGRESS ALLOWED')]):
            yy=y+115+i*47
            self.text(cr,x+20,yy,label,8,'dark_foreground');self.text(cr,x+20,yy+20,value,10,'green' if i>2 else 'foreground')
        self.text(cr,x+20,y+h-25,'CORRELATING SESSION CONTEXT…' if phase<13 else 'COMPLETE / RETURNING TO WORKSPACE',8,'accent')
        image=cr.pop_group();cr.set_source(image);cr.paint_with_alpha(visibility);cr.restore()

    def world(self,cr,x,y,w,h,t):
        scale=min(w/360,(h-22)/145);ox=x+(w-360*scale)/2;oy=y+(h-145*scale)/2
        def project(p):return ox+(p[0]+180)*scale,oy+(85-p[1])*scale
        cr.save();cr.rectangle(x,y,w,h);cr.clip()
        for lon in range(-180,181,30):
            ax,ay=project((lon,85));bx,by=project((lon,-60));self.line(cr,ax,ay,bx,by,alpha=.16,width=.5)
        for lat in range(-60,86,30):
            ax,ay=project((-180,lat));bx,by=project((180,lat));self.line(cr,ax,ay,bx,by,alpha=.16,width=.5)
        self.color(cr,'foreground',.43);cr.set_line_width(.65)
        for shape in LAND:
            previous=None
            for p in shape:
                if p[1]<-60 or p[1]>85:previous=None;continue
                px,py=project(p)
                if previous is None or abs(previous[0]-p[0])>180:cr.move_to(px,py)
                else:cr.line_to(px,py)
                previous=p
        cr.stroke()
        selected=int(t/6)%7
        for i in range(7):
            if i==selected:continue
            a=project(NODES[selected]);b=project(NODES[i]);peak=min(a[1],b[1])-30-abs(a[0]-b[0])*.10
            self.color(cr,'cyan',.26);cr.set_line_width(.7);cr.move_to(*a);cr.curve_to(a[0],peak,b[0],peak,*b);cr.stroke()
            f=(t*.15+i*.14)%1;g=1-f
            px=g**3*a[0]+3*g*g*f*a[0]+3*g*f*f*b[0]+f**3*b[0]
            py=g**3*a[1]+3*g*g*f*peak+3*g*f*f*peak+f**3*b[1]
            self.dot(cr,px,py,1.8,'cyan',.9)
        names=['SFO','NYC','LON','BER','SIN','TYO','SYD']
        for i,p in enumerate(NODES):
            px,py=project(p);self.dot(cr,px,py,2.3,'accent')
            self.color(cr,'accent',.6);cr.set_line_width(.6);cr.arc(px,py,6+(math.sin(t*1.5+i)+1)*2,0,math.tau);cr.stroke()
            self.text(cr,px+10,py-8,names[i],8,'bright_foreground',.8)
        cr.restore()
        self.text(cr,x+4,y+h+12,'07 NODES    06 ROUTES    24 ms MEDIAN LATENCY',9,'dark_foreground')
    def camera(self,cr,x,y,w,h,t,sector=0):
        cr.save();cr.rectangle(x,y,w,h);cr.clip()
        zoom=min(w/540,h/350)
        drift=math.sin(t/18)*.12
        def iso(a,b,z=0):
            a,b=(a-6)*math.cos(drift)-(b-6)*math.sin(drift)+6,(a-6)*math.sin(drift)+(b-6)*math.cos(drift)+6
            return x+w*.50+(a-b)*26*zoom,y+h*.16+(a+b)*12*zoom-z*17*zoom
        for i in range(13):
            self.line(cr,*iso(i,0),*iso(i,12),alpha=.25,width=.45)
            self.line(cr,*iso(0,i),*iso(12,i),alpha=.25,width=.45)
        for a in (1,4,7,10):
            for b in (1,4,7,10):
                z=1+((a*7+b*3+sector*5)%7)*.55
                corners=[(a,b),(a+1.7,b),(a+1.7,b+1.7),(a,b+1.7)]
                for inds in ((0,1),(1,2),(2,3)):
                    pa,pb=[corners[k] for k in inds]
                    verts=[iso(*pa),iso(*pb),iso(*pb,z),iso(*pa,z)]
                    cr.move_to(*verts[0])
                    for v in verts[1:]:cr.line_to(*v)
                    cr.close_path();self.color(cr,'accent',.035 if inds==(0,1) else .065);cr.fill_preserve();self.color(cr,'foreground',.25);cr.set_line_width(.6);cr.stroke()
                roof=[iso(*p,z) for p in corners];cr.move_to(*roof[0])
                for v in roof[1:]:cr.line_to(*v)
                cr.close_path();self.color(cr,'cyan',.10);cr.fill_preserve();self.color(cr,'cyan',.4);cr.stroke()
                for level in range(1,int(z)+1):self.line(cr,*iso(a+1.7,b,level),*iso(a+1.7,b+1.7,level),'foreground',.14,.4)
        # Restrained moving scan plane and two independently tracked vehicles.
        sweep=(t*.23)%12
        self.line(cr,*iso(sweep,0),*iso(sweep,12),'cyan',.45,.8)
        for i in range(2):
            px,py=iso(3+i*3,(t*.35+i*4)%12)
            self.color(cr,'accent',.8);cr.set_line_width(.8)
            for dx,dy in ((-7,-6),(3,-6),(-7,3),(3,3)):
                cr.move_to(px+dx,py+dy);cr.line_to(px+dx+(4 if dx<0 else -4),py+dy);cr.stroke()
            self.dot(cr,px,py,1.7,'green');self.text(cr,px+12,py-7,f'TRK-{i+1:02}',8,'accent')
        self.text(cr,x+6,y+13,['DOCKLAND / SECTOR 07','RELAY CAMPUS / SECTOR 08','TRANSIT GRID / SECTOR 09','EDGE FACILITY / SECTOR 10'][sector],9,'foreground')
        self.text(cr,x+6,y+30,'AZ 042°   ALT 240m   TRACKING 02',8,'dark_foreground')
        self.text(cr,x+6,y+h-10,'VECTOR SCENE · SIMULATED OPTICAL SENSOR',8,'dark_foreground')
        cr.restore()
    def packets(self,cr,x,y,w,h,t):
        cols=[(0,'SEQ'),(72,'TIME'),(184,'SOURCE'),(350,'DESTINATION'),(520,'PROTO'),(600,'LEN'),(660,'DETAIL')]
        for dx,label in cols:self.text(cr,x+dx,y,label,9,'dark_foreground')
        for i,p in enumerate(list(self.model.packets)[:max(1,int((h-26)/23))]):
            yy=y+26+i*23;seq,src,dst,proto,size,info=p
            if i%2==0:self.color(cr,'foreground',.018);cr.rectangle(x-8,yy-15,w+16,22);cr.fill()
            col='cyan' if proto=='DNS' else ('magenta' if proto=='QUIC' else 'foreground')
            vals=[str(seq),f'{max(0,t-i*.5):09.3f}',src,dst,proto,str(size),info.split(' · ')[0]]
            for (dx,_),value in zip(cols,vals):self.text(cr,x+dx,yy,value,10,col if dx==520 else 'foreground',.9 if i<4 else .7)
    def events(self,cr,x,y,w,h,t):
        for i,(kind,msg) in enumerate(list(self.model.events)[:7]):
            yy=y+i*34
            self.dot(cr,x+3,yy-4,2,'yellow' if kind=='NOTE' else 'cyan',.7)
            if i<6:self.line(cr,x+3,yy+2,x+3,yy+26,alpha=.4)
            self.text(cr,x+17,yy,kind,9,'yellow' if kind=='NOTE' else 'accent')
            self.text(cr,x+73,yy,msg,10,'foreground',.85)
            self.text(cr,x+w-30,yy,f'{i*2:02}s',8,'dark_foreground')
        self.line(cr,x,y+h-28,x+w,y+h-28,alpha=.3)
        self.text(cr,x,y+h-7,'RULESET 2.4 / 2,048 INDICATORS / NO ACTION REQUIRED',8,'dark_foreground')

def manager_parent():
    pid=os.getppid()
    try:args=Path(f'/proc/{pid}/cmdline').read_bytes().split(b'\0')
    except OSError:return None,False
    owned=any(a.endswith(b'/screensaver-manager') for a in args) and b'run' in args
    return (pid if owned else None), b'--preview' in args

class Window(Gtk.Window):
    def __init__(self,preview=False,demo=False,settings=None):
        self.parent_pid,self.managed_preview=manager_parent();self.preview=preview or self.managed_preview
        GLib.set_prgname(('io.github.whiskeytangofussell.screensavermanager.preview' if self.managed_preview else 'org.omarchy.sentinel.preview') if self.preview else 'org.omarchy.screensaver')
        super().__init__(title='Sentinel · Security Observation Network')
        self.set_default_size(1440,900);self.set_decorated(self.preview)
        self.view=Gtk.DrawingArea();self.overlay=Gtk.Overlay();self.overlay.add(self.view);self.add(self.overlay);self.display=Display(live=not demo,settings=settings)
        self.camera_viewer=None
        self.regional_cameras=None
        if not demo and self.display.settings['sections']['cameras']:
            import json
            from preferences import local_cameras
            from regional_cameras import RegionalCameras
            from camera_viewer import CameraViewer
            cameras=local_cameras(json.loads((Path(__file__).parent/'cameras.json').read_text()),self.display.settings)
            self.camera_viewer=CameraViewer(self.overlay,self.view,self.key_press,cameras=cameras);self.display.camera_viewer=self.camera_viewer
            self.regional_cameras=RegionalCameras(self.display.settings)
        self.view.connect('draw',self.draw)
        self.add_events(Gdk.EventMask.POINTER_MOTION_MASK|Gdk.EventMask.BUTTON_PRESS_MASK|Gdk.EventMask.KEY_PRESS_MASK|Gdk.EventMask.FOCUS_CHANGE_MASK)
        self.connect('key-press-event',self.key_press);self.connect('button-press-event',self.dismiss)
        self.connect('motion-notify-event',self.motion);self.connect('delete-event',self.dismiss)
        self.connect('focus-out-event',self.blur)
        self.pointer=None;self.started=time.monotonic()
        self.show_all()
        if not self.preview:self.fullscreen()
        GLib.timeout_add(83,self.update)
        for sig in (signal.SIGTERM,signal.SIGINT,signal.SIGHUP):GLib.unix_signal_add(GLib.PRIORITY_DEFAULT,sig,self.shutdown)
    def draw(self,widget,cr):self.display.paint(cr,widget.get_allocated_width(),widget.get_allocated_height(),time.monotonic()-self.display.start)
    def update(self):
        if self.camera_viewer:
            cameras=self.regional_cameras.take() if self.regional_cameras else None
            if cameras:self.camera_viewer.set_cameras(cameras)
            t=time.monotonic()-self.display.start;scene,phase,duration=scene_at(t,self.display.enabled)
            self.camera_viewer.update(scene in (0,2) and phase<duration-1.4)
            if self.camera_viewer.played:
                cam=self.camera_viewer.cameras[self.camera_viewer.index]
                self.display.signals.set_region(cam['city'],cam['lat'],cam['lon'])
        self.view.queue_draw();return True
    def key_press(self,widget,event):
        if getattr(event,'keyval',None) in (Gdk.KEY_s,Gdk.KEY_S):
            import subprocess
            subprocess.Popen([sys.executable,str(Path(__file__).resolve()),'--settings'],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
        return self.dismiss()

    def dismiss(self,*args):
        if self.parent_pid and not self.managed_preview:
            try:os.kill(self.parent_pid,signal.SIGTERM)
            except ProcessLookupError:pass
        self.shutdown();return True
    def motion(self,widget,event):
        pos=(event.x_root,event.y_root)
        if time.monotonic()-self.started>2 and self.pointer and math.dist(pos,self.pointer)>4 and not self.preview:self.dismiss()
        if time.monotonic()-self.started<=2 or self.pointer is None:self.pointer=pos
        return False
    def blur(self,*args):
        if not self.preview and time.monotonic()-self.started>2:self.dismiss()
        return False
    def shutdown(self):
        if self.camera_viewer:self.camera_viewer.close();self.camera_viewer=None
        if self.display.live:self.display.tiles.close();self.display.signals.close();self.display.regional.close()
        Gtk.main_quit();return False

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--preview',action='store_true');p.add_argument('--snapshot',type=Path);p.add_argument('--demo',action='store_true',help='Offline simulated display');p.add_argument('--settings',action='store_true',help='Configure location and sources');args=p.parse_args()
    if args.settings:
        from settings_ui import main
        main()
    elif args.snapshot:
        surface=cairo.ImageSurface(cairo.FORMAT_ARGB32,3240,2160);Display().paint(cairo.Context(surface),3240,2160,17);surface.write_to_png(str(args.snapshot))
    else:
        from preferences import runtime_settings
        Window(args.preview,args.demo,settings=None if args.demo else runtime_settings());Gtk.main()
