"""Orthographic globe and cached aerial basemap for the native Cairo display."""
from collections import OrderedDict
import queue
import math
from pathlib import Path
import threading
import time
import urllib.request
import gi
gi.require_version('GdkPixbuf','2.0')
from gi.repository import Gdk, GdkPixbuf
import cairo
from sentinel import LAND


def mercator(lon,lat,zoom):
    lat=max(-85.05112878,min(85.05112878,lat));size=256*2**zoom
    return (lon+180)/360*size,(1-math.asinh(math.tan(math.radians(lat)))/math.pi)/2*size


def orthographic(lon,lat,center_lon,center_lat):
    lon,lat,center_lon,center_lat=map(math.radians,(lon,lat,center_lon,center_lat))
    delta=lon-center_lon
    return math.cos(lat)*math.sin(delta),math.cos(center_lat)*math.sin(lat)-math.sin(center_lat)*math.cos(lat)*math.cos(delta),math.sin(center_lat)*math.sin(lat)+math.cos(center_lat)*math.cos(lat)*math.cos(delta)


class Globe:
    def __init__(self):self.lon=-20;self.lat=20;self.last=None

    def draw(self,display,cr,cx,cy,radius,lon,lat,t,markers=(),track=()):
        elapsed=min(.2,max(0,t-self.last)) if self.last is not None else .08;self.last=t
        alpha=1-math.exp(-elapsed*.65)
        self.lon+=((lon-self.lon+180)%360-180)*alpha;self.lat+=(lat-self.lat)*alpha
        glow=cairo.RadialGradient(cx,cy,radius*.7,cx,cy,radius*1.13)
        glow.add_color_stop_rgba(0,.1,.35,.6,.04);glow.add_color_stop_rgba(.8,.1,.35,.6,.05);glow.add_color_stop_rgba(1,.1,.35,.6,0)
        cr.set_source(glow);cr.arc(cx,cy,radius*1.13,0,math.tau);cr.fill()
        sphere=cairo.RadialGradient(cx-radius*.4,cy-radius*.35,0,cx,cy,radius)
        sphere.add_color_stop_rgb(0,.075,.105,.16);sphere.add_color_stop_rgb(1,.025,.033,.055)
        cr.set_source(sphere);cr.arc(cx,cy,radius,0,math.tau);cr.fill()
        display.color(cr,'accent',.42);cr.set_line_width(.8);cr.arc(cx,cy,radius,0,math.tau);cr.stroke()
        def project(point):
            x,y,z=orthographic(point[0],point[1],self.lon,self.lat)
            return cx+x*radius,cy-y*radius,z
        def path(points,color,opacity,width):
            previous=None
            for point in points:
                px,py,z=project(point)
                if z>=0:
                    if previous:display.line(cr,*previous,px,py,color,opacity*(.35+.65*z),width)
                    previous=(px,py)
                else:previous=None
        for meridian in range(-180,180,30):path([(meridian,lat) for lat in range(-90,91,3)],'muted',.3,.55)
        for parallel in range(-60,61,30):path([(lon,parallel) for lon in range(-180,181,3)],'muted',.3,.55)
        for shape in LAND:path(shape,'foreground',.65,.8)
        path([p for p in track if p],'cyan',.9,1.2)
        for item in markers:
            px,py,z=project((item['lon'],item['lat']))
            if z<0:continue
            active=item.get('active',False)
            marker_color=item.get('color','green' if active else 'accent')
            display.dot(cr,px,py,3.6 if active else 1.7,marker_color,.4+.6*z)
            if active:
                display.color(cr,marker_color,.5);cr.set_line_width(.8);cr.arc(px,py,9+math.sin(t*2)*2,0,math.tau);cr.stroke()
                display.text(cr,px+13,py-10,item.get('label','')[:25],9,marker_color)
        display.line(cr,cx-radius-15,cy,cx-radius-5,cy,'accent',.5)
        display.line(cr,cx+radius+5,cy,cx+radius+15,cy,'accent',.5)


class Tiles:
    def __init__(self):
        self.cache=Path.home()/'.cache/sentinel/maps';self.lock=threading.Lock();self.images=OrderedDict();self.pending=set();self.retry={};self.jobs=queue.Queue();self.closed=False
        for _ in range(2):threading.Thread(target=self.worker,daemon=True).start()

    def image(self,key):
        with self.lock:
            if key in self.images:self.images.move_to_end(key);return self.images[key]
            if self.closed or key in self.pending or self.retry.get(key,0)>time.time():return None
            self.pending.add(key)
        self.jobs.put(key);return None

    def worker(self):
        while True:
            key=self.jobs.get()
            if key is None:return
            if not self.closed:self.load(key)

    def close(self):
        self.closed=True
        for _ in range(2):self.jobs.put(None)

    def load(self,key):
        try:
            zoom,x,y=key;path=self.cache/f'{zoom}-{x}-{y}.jpg'
            if path.exists() and time.time()-path.stat().st_mtime<604800:raw=path.read_bytes()
            else:
                url=f'https://services.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{zoom}/{y}/{x}'
                with urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'Sentinel/1.0 (+https://github.com/samcapshaw/sentinel)'}),timeout=8) as response:raw=response.read(1_000_001)
                if len(raw)>1_000_000 or not raw.startswith(b'\xff\xd8'):raise ValueError('Invalid map tile')
                self.cache.mkdir(parents=True,exist_ok=True);temporary=path.with_suffix('.tmp');temporary.write_bytes(raw);temporary.replace(path)
            loader=GdkPixbuf.PixbufLoader.new_with_type('jpeg');loader.write(raw);loader.close();image=loader.get_pixbuf()
            with self.lock:
                self.images[key]=image
                while len(self.images)>160:self.images.popitem(last=False)
        except Exception:
            with self.lock:self.retry[key]=time.time()+120
        finally:
            with self.lock:self.pending.discard(key)

    def draw(self,display,cr,x,y,w,h,lon,lat,zoom,markers=()):
        center_x,center_y=mercator(lon,lat,zoom);left=center_x-w/2;top=center_y-h/2
        cr.save();cr.rectangle(x,y,w,h);cr.clip();display.color(cr,'background');cr.paint()
        for tx in range(math.floor(left/256),math.ceil((left+w)/256)):
            for ty in range(math.floor(top/256),math.ceil((top+h)/256)):
                if not 0<=ty<2**zoom:continue
                image=self.image((zoom,tx%2**zoom,ty))
                if image:Gdk.cairo_set_source_pixbuf(cr,image,x+tx*256-left,y+ty*256-top);cr.paint_with_alpha(.72)
        for marker in markers:
            px,py=mercator(marker['lon'],marker['lat'],zoom);px=x+px-left;py=y+py-top
            if not x<=px<=x+w or not y<=py<=y+h:continue
            color=marker.get('color','accent')
            trail=marker.get('trail',[])
            previous=None
            for trail_lon,trail_lat in trail:
                tx,ty=mercator(trail_lon,trail_lat,zoom);tx=x+tx-left;ty=y+ty-top
                if previous:display.line(cr,*previous,tx,ty,color,.45,.8)
                previous=(tx,ty)
            display.dot(cr,px,py,3,color)
            heading=marker.get('heading')
            if isinstance(heading,(int,float)):
                cr.save();cr.translate(px,py);cr.rotate(math.radians(heading));display.color(cr,color,.9);cr.move_to(0,-8);cr.line_to(4,5);cr.line_to(0,2);cr.line_to(-4,5);cr.close_path();cr.fill();cr.restore()
            label=marker.get('label','')
            if label:display.text(cr,px+9,py-7,label[:22],9,color)
        display.text(cr,x+8,y+h-8,'Esri, Vantor, Earthstar Geographics, GIS User Community / aerial basemap',8,'bright_foreground')
        cr.restore()
