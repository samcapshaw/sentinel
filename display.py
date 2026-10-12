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

SCENES = (("OVERVIEW",24),("TRAFFIC ANALYSIS",22),("CAMERA ARRAY",24),("THREAT INTELLIGENCE",26),("ORBITAL TRACKING",30))

def scene_at(t):
    moment=max(0,t)%sum(duration for _,duration in SCENES)
    for index,(_,duration) in enumerate(SCENES):
        if moment<duration:return index,moment,duration
        moment-=duration
    return 0,0,SCENES[0][1]

class Display:
    def __init__(self,live=False):
        self.live=live
        if live:
            from live import Telemetry
            from orbits import Tracker
            self.telemetry=Telemetry();self.tracker=Tracker()
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
        scene,phase,duration=scene_at(t)
        self.text(cr,266,39,'/ '+SCENES[scene][0],10,'dark_foreground')
        self.dot(cr,1312,35,2,'green');self.text(cr,1324,39,'LIVE' if self.live else 'DEMO',9,'green')
        from datetime import datetime,timezone
        self.text(cr,1430,39,datetime.now(timezone.utc).strftime('%H:%M:%S UTC'),10,'accent')
        self.line(cr,32,61,1568,61)
        metrics=[('SENSOR MESH','07','EDGES ONLINE','accent'),('ACTIVE FLOWS',str(248+int(17*math.sin(t/4))),'CORRELATED SESSIONS','foreground'),('INGEST RATE',f'{1.28+.08*math.sin(t/6):.2f}','Gb/s  /  SYNTHETIC','cyan'),('EXPOSURE SCORE','18','LOW  /  BASELINE NORMAL','green')]
        if self.live:
            self.current=self.telemetry.snapshot();self.orbits=self.tracker.snapshot()
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
            cr.push_group();self.workspace(cr,(scene+1)%len(SCENES),t,0);incoming=cr.pop_group()
            cr.set_source(incoming);cr.paint_with_alpha(blend)
        for i,(label,_) in enumerate(SCENES):
            x=350+i*240
            self.text(cr,x,959,label,8,'accent' if i==scene else 'dark_foreground')
            self.line(cr,x,965,x+160,965,alpha=.35)
            if i==scene:self.line(cr,x,965,x+160*(phase/duration),965,'accent',.8,1)
        self.text(cr,32,975,('LIVE METADATA / PUBLIC FEEDS / NO PACKET PAYLOAD CAPTURE' if self.live else 'DEMO TELEMETRY / CAMERA SIMULATION'),9,'dark_foreground')
        self.text(cr,1280,975,'OMARCHY  ·  MOVE MOUSE TO DISMISS',9,'dark_foreground')
        cr.restore()
    def workspace(self,cr,scene,t,phase):
        if self.live:
            self.live_workspace(cr,scene,t,phase);return
        if scene==4:
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
        if scene==2:
            import json
            cameras=json.loads((Path(__file__).parent/'cameras.json').read_text())
            self.panel(cr,32,155,1536,787,'03','PUBLIC CITY CAMERA DIRECTORY','PROVIDER PAGES / VIDEO NOT EMBEDDED')
            for i,cam in enumerate(cameras):
                x=60+(i%2)*760;y=235+(i//2)*130
                self.text(cr,x,y,cam['city'].upper()+' / '+cam['view'],12,'bright_foreground')
                self.text(cr,x,y+27,cam['provider'],10,'accent')
                self.text(cr,x,y+51,cam['url'][:100],9,'dark_foreground')
            self.text(cr,60,905,'Public viewing pages. Availability varies; hosted players may contain ads. No camera device is accessed.',10,'dark_foreground')
            return
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

    def orbital(self,cr,t):
        data=self.orbits
        self.panel(cr,32,155,1100,787,'05','ORBITAL OBSERVATION','SGP4 / ISS + STARLINK / ESTIMATED POSITIONS')
        self.panel(cr,1148,155,420,787,'06','ORBIT ELEMENTS','PUBLIC / CELESTRAK')
        x,y,w,h=55,235,1052,570
        scale=min(w/360,h/180);ox=x+(w-360*scale)/2;oy=y+(h-180*scale)/2
        def project(lon,lat):return ox+(lon+180)*scale,oy+(90-lat)*scale
        for lon in range(-180,181,30):self.line(cr,*project(lon,90),*project(lon,-90),alpha=.2)
        for lat in range(-90,91,30):self.line(cr,*project(-180,lat),*project(180,lat),alpha=.2)
        for shape in LAND:
            previous=None
            for lon,lat in shape:
                point=project(lon,lat)
                if previous and abs(point[0]-previous[0])<w/2:self.line(cr,*previous,*point,'foreground',.4,.65)
                previous=point
        previous=None
        for point in data['track']:
            if point:
                pixel=project(*point[:2])
                if previous and abs(pixel[0]-previous[0])<w/2:self.line(cr,*previous,*pixel,'cyan',.8,1)
                previous=pixel
            else:previous=None
        for i,item in enumerate(data['objects']):
            px,py=project(*item['point'][:2]);selected=i==data.get('selected',0)
            self.dot(cr,px,py,4 if selected else 1.6,'green' if selected else 'accent',1 if selected else .7)
            if selected:self.text(cr,px+9,py-9,item['name'],10,'green')
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
    def __init__(self,preview=False,demo=False):
        self.parent_pid,self.managed_preview=manager_parent();self.preview=preview or self.managed_preview
        GLib.set_prgname(('io.github.whiskeytangofussell.screensavermanager.preview' if self.managed_preview else 'org.omarchy.sentinel.preview') if self.preview else 'org.omarchy.screensaver')
        super().__init__(title='Sentinel · Security Observation Network')
        self.set_default_size(1440,900);self.set_decorated(self.preview)
        self.view=Gtk.DrawingArea();self.add(self.view);self.display=Display(live=not demo)
        self.view.connect('draw',self.draw)
        self.add_events(Gdk.EventMask.POINTER_MOTION_MASK|Gdk.EventMask.BUTTON_PRESS_MASK|Gdk.EventMask.KEY_PRESS_MASK|Gdk.EventMask.FOCUS_CHANGE_MASK)
        self.connect('key-press-event',self.dismiss);self.connect('button-press-event',self.dismiss)
        self.connect('motion-notify-event',self.motion);self.connect('delete-event',self.dismiss)
        self.connect('focus-out-event',self.blur)
        self.pointer=None;self.started=time.monotonic()
        self.show_all()
        if not self.preview:self.fullscreen()
        GLib.timeout_add(83,self.update)
        for sig in (signal.SIGTERM,signal.SIGINT,signal.SIGHUP):GLib.unix_signal_add(GLib.PRIORITY_DEFAULT,sig,self.shutdown)
    def draw(self,widget,cr):self.display.paint(cr,widget.get_allocated_width(),widget.get_allocated_height(),time.monotonic()-self.display.start)
    def update(self):self.view.queue_draw();return True
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
    def shutdown(self):Gtk.main_quit();return False

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--preview',action='store_true');p.add_argument('--snapshot',type=Path);p.add_argument('--demo',action='store_true',help='Offline simulated display');args=p.parse_args()
    if args.snapshot:
        surface=cairo.ImageSurface(cairo.FORMAT_ARGB32,3240,2160);Display().paint(cairo.Context(surface),3240,2160,17);surface.write_to_png(str(args.snapshot))
    else:Window(args.preview,args.demo);Gtk.main()
