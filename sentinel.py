#!/usr/bin/env python3
"""Sentinel: a synthetic security operations screensaver. No network access."""
import argparse
from collections import deque
from datetime import datetime, timezone
from html import escape
import math
import json
from functools import lru_cache
import os
from pathlib import Path
import random
import select
import shutil
import signal
import sys
import termios
import time
import tomllib
import tty

DEFAULTS = dict(background='#1a1b26', foreground='#a9b1d6', accent='#7aa2f7', muted='#414868',
                dark_foreground='#565f89', bright_foreground='#c0caf5', cyan='#449dab',
                green='#9ece6a', yellow='#e0af68', red='#f7768e', magenta='#ad8ee6')

def palette():
    result = DEFAULTS.copy()
    try:
        data = tomllib.loads((Path.home()/'.local/state/omarchy/current/theme/colors.toml').read_text())
        for key in result:
            value = data.get(key, '')
            if len(value)==7 and value.startswith('#'):
                int(value[1:], 16); result[key] = value
    except (OSError, ValueError):
        pass
    return result

class Canvas:
    def __init__(self, width, height):
        self.w, self.h = max(1,width), max(1,height)
        self.cells = [[(' ', 'foreground') for _ in range(self.w)] for _ in range(self.h)]
    def text(self,x,y,text,color='foreground'):
        if not 0<=y<self.h:return
        for i,ch in enumerate(str(text)):
            if 0<=x+i<self.w:self.cells[y][x+i]=(ch,color)
    def panel(self,x,y,w,h,title,tag=''):
        if w<4 or h<3:return
        self.text(x,y,'┌'+'─'*(w-2)+'┐','muted')
        self.text(x,y+h-1,'└'+'─'*(w-2)+'┘','muted')
        for row in range(y+1,y+h-1):
            self.text(x,row,'│','muted');self.text(x+w-1,row,'│','muted')
        self.text(x+2,y,' '+title[:w-6]+' ','accent')
        if tag and len(title)+len(tag)+8<w:self.text(x+w-len(tag)-3,y,' '+tag+' ','dark_foreground')
    def ansi(self, colors):
        out=['\x1b[H'];last=None
        for rowno,row in enumerate(self.cells):
            if rowno:out.append('\r\n')
            for ch,key in row:
                if key!=last:
                    h=colors[key].lstrip('#');r,g,b=(int(h[i:i+2],16) for i in (0,2,4))
                    out.append(f'\x1b[38;2;{r};{g};{b}m');last=key
                out.append(ch)
        return ''.join(out)
    def svg(self,colors):
        parts=[f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.w*9+40}" height="{self.h*18+40}">',f'<rect width="100%" height="100%" fill="{colors["background"]}"/>','<g font-family="DejaVu Sans Mono,monospace" font-size="14" xml:space="preserve">']
        for y,row in enumerate(self.cells):
            start=0
            while start<len(row):
                key=row[start][1];end=start+1
                while end<len(row) and row[end][1]==key:end+=1
                text=''.join(ch for ch,_ in row[start:end])
                if text.strip():parts.append(f'<text x="{20+start*9}" y="{34+y*18}" fill="{colors[key]}" textLength="{(end-start)*9}" lengthAdjust="spacingAndGlyphs">{escape(text)}</text>')
                start=end
        return ''.join(parts)+'</g></svg>'

class Braille:
    DOTS=((1,8),(2,16),(4,32),(64,128))
    def __init__(self,w,h):self.w,self.h=w*2,h*4;self.points=set()
    def dot(self,x,y):
        x,y=round(x),round(y)
        if 0<=x<self.w and 0<=y<self.h:self.points.add((x,y))
    def line(self,x1,y1,x2,y2):
        steps=max(1,math.ceil(max(abs(x2-x1),abs(y2-y1))))
        for s in range(steps+1):self.dot(x1+(x2-x1)*s/steps,y1+(y2-y1)*s/steps)
    def draw(self,c,x,y,color='accent'):
        cells={}
        for px,py in self.points:
            pos=(px//2,py//4);cells[pos]=cells.get(pos,0)|self.DOTS[py%4][px%2]
        for (cx,cy),bits in cells.items():c.text(x+cx,y+cy,chr(0x2800+bits),color)

# Natural Earth 1:110m coastlines (public domain); bundled for offline use.
LAND = json.loads(Path(__file__).with_name('coastlines.json').read_text())

def map_projection(width, height):
    # Braille dots are approximately square in a terminal with 1:2 cells.
    # One shared scale preserves geography instead of stretching to the panel.
    scale = min((width-1)/360, (height-1)/145)
    ox, oy = (width-360*scale)/2, (height-145*scale)/2
    return lambda point: (ox+(point[0]+180)*scale, oy+(85-point[1])*scale)

@lru_cache(maxsize=8)
def coastline(width, height):
    raster = Braille(width, height)
    project = map_projection(raster.w, raster.h)
    for shape in LAND:
        for a, z in zip(shape, shape[1:]):
            # Do not draw the long way across the map at the date line.
            if abs(a[0]-z[0]) > 180 or not (-60 <= a[1] <= 85 and -60 <= z[1] <= 85):
                continue
            raster.line(*project(a), *project(z))
    return raster

NODES=[(-122,38),(-74,41),(-.1,51),(13,52),(104,1),(139,36),(151,-34)]

class Dashboard:
    def __init__(self,seed=7):
        self.rng=random.Random(seed);self.packets=deque(maxlen=100);self.events=deque(maxlen=30);self.sequence=18420
        for i in range(40):self.advance(i)
    def advance(self,t):
        self.sequence+=1
        proto=self.rng.choice(['TLS1.3','TLS1.3','QUIC','DNS','TCP','SSH'])
        src=f'10.24.{self.rng.randrange(1,9)}.{self.rng.randrange(2,240)}'
        dst=f'{self.rng.choice(["192.0.2","198.51.100","203.0.113"])}.{self.rng.randrange(1,240)}'
        info={'TLS1.3':'Application Data · encrypted','QUIC':'Protected payload · h3','DNS':'A? relay.example · response','TCP':'ACK · window 65535','SSH':'Encrypted transport · session'}[proto]
        self.packets.appendleft((self.sequence,src,dst,proto,self.rng.randrange(64,1460),info))
        if self.sequence%4==0:
            kind,msg=self.rng.choice([('PASS','Certificate chain verified'),('SYNC','Edge sensor heartbeat received'),('RULE','Egress policy evaluated'),('PASS','DNS response matched allowlist'),('HUNT','Behavioral baseline correlated'),('NOTE','Unusual burst isolated in replay'),('SYNC','Indicator cache synchronized')])
            self.events.appendleft((kind,msg))
    def render(self,w,h,t):
        c=Canvas(w,h)
        if w<74 or h<24:
            c.text(2,2,'S E N T I N E L','accent');c.text(2,4,'SECURITY OPERATIONS / SIMULATION')
            c.text(2,6,datetime.now().strftime('%H:%M:%S'),'bright_foreground')
            c.text(2,8,'Expand terminal for full dashboard','dark_foreground');return c
        shift=round(math.sin(t/80)) # Subtle position drift, no flashing or aggressive motion.
        c.text(2+shift,1,'S E N T I N E L','bright_foreground')
        c.text(24,1,'/  DISTRIBUTED OBSERVATION SYSTEM','dark_foreground')
        clock=datetime.now(timezone.utc).strftime('%H:%M:%S UTC')
        c.text(w-len(clock)-3,1,clock,'accent')
        c.text(2,3,'●  SYSTEM NOMINAL','green')
        stats=f'07 SENSORS   /   {128+int(12*math.sin(t/4)):03d} FLOWS   /   99.98% UPTIME'
        c.text(max(23,w-len(stats)-3),3,stats,'foreground')
        top=5; upper=max(12,int((h-8)*.53));lower=h-top-upper-2
        left=int((w-4)*.42);middle=int((w-4)*.34);right=w-4-left-middle
        x1,x2,x3=1,left+2,left+middle+3
        self.world(c,x1,top,left,upper,t)
        self.camera(c,x2,top,middle,upper,t)
        self.intel(c,x3,top,right,upper,t)
        split=max(40,int((w-3)*.66))
        self.capture(c,1,top+upper+1,split,lower,t)
        self.timeline(c,split+2,top+upper+1,w-split-3,lower,t)
        c.text(2,h-1,'SYNTHETIC TELEMETRY  /  NO LIVE CAPTURE  /  CAMERA SIMULATION','dark_foreground')
        if w>110:c.text(w-32,h-1,'OMARCHY  ·  PRESS ANY KEY TO EXIT','dark_foreground')
        return c
    def world(self,c,x,y,w,h,t):
        c.panel(x,y,w,h,'01 / GLOBAL MESH','7 EDGES')
        bw,bh=w-6,h-5;b=coastline(bw,bh)
        project=map_projection(b.w,b.h)
        b.draw(c,x+3,y+2,'dark_foreground')
        a=NODES[int(t/4)%len(NODES)];z=NODES[(int(t/4)+3)%len(NODES)]
        path=Braille(bw,bh);ax,ay=project(a);zx,zy=project(z)
        previous=(ax,ay)
        for i in range(1,45):
            f=i/44;point=(ax+(zx-ax)*f,ay+(zy-ay)*f-math.sin(f*math.pi)*b.h*.18)
            path.line(*previous,*point);previous=point
        path.draw(c,x+3,y+2,'cyan')
        for n,p in enumerate(NODES):
            px,py=project(p);c.text(x+3+int(px/2),y+2+int(py/4),'◉' if n==int(t/4)%7 else '·','accent')
        c.text(x+3,y+h-2,'ROUTE CORRELATION  /  EDGE → CORE','dark_foreground')
    def camera(self,c,x,y,w,h,t):
        c.panel(x,y,w,h,'02 / OVERWATCH','SIM')
        c.text(x+3,y+2,'CAM-07  /  DOCKLAND SECTOR','foreground')
        bw,bh=w-6,h-7;b=Braille(bw,max(2,bh))
        def iso(a,z,height=0):return (b.w*.49+(a-z)*b.w*.055,b.h*.18+(a+z)*b.h*.045-height*b.h*.065)
        for i in range(0,9,2):
            b.line(*iso(i,0),*iso(i,8));b.line(*iso(0,i),*iso(8,i))
        for a,z,height in [(1,1,2),(1,4,3),(4,1,4),(4,4,2),(6,6,3),(6,2,2)]:
            top=[iso(a,z,height),iso(a+1.3,z,height),iso(a+1.3,z+1.3,height),iso(a,z+1.3,height)]
            for p,q in zip(top,top[1:]+top[:1]):b.line(*p,*q)
            for da,dz in [(0,0),(1.3,0),(1.3,1.3)]:b.line(*iso(a+da,z+dz),*iso(a+da,z+dz,height))
        b.draw(c,x+3,y+4,'dark_foreground')
        sweep=Braille(bw,max(2,bh));sx=(t*.08%1)*sweep.w
        sweep.line(sx,0,sx,sweep.h-1);sweep.draw(c,x+3,y+4,'muted')
        px,py=iso(3,(t*.4)%7)
        cx,cy=x+3+int(px/2),y+4+int(py/4)
        c.text(cx-1,cy,'[·]','cyan')
        if cx+8<x+w-2:c.text(cx+3,cy,'TRK-04','cyan')
        c.text(x+3,y+h-2,'VECTOR REPLAY  /  04 TRACKS','dark_foreground')
    def intel(self,c,x,y,w,h,t):
        c.panel(x,y,w,h,'03 / INTELLIGENCE')
        inner=w-6
        for row,label,value,color in [(2,'RISK INDEX','18 / 100','green'),(5,'SIGNAL QUALITY','98.4%','accent'),(8,'INDICATORS','2,048','foreground')]:
            if row+2>=h:break
            c.text(x+3,y+row,label[:inner],'dark_foreground')
            c.text(x+3,y+row+1,value[:inner],color)
        if h>14:
            c.text(x+3,y+h-5,'FLOW DISTRIBUTION'[:inner],'dark_foreground')
            wave=''.join('▁▂▃▄▅▆▇'[min(6,max(0,int(3+2*math.sin(i*.7+t*.4)+math.sin(i*.3-t*.3))))] for i in range(inner))
            c.text(x+3,y+h-3,wave,'magenta')
    def capture(self,c,x,y,w,h,t):
        c.panel(x,y,w,h,'04 / PACKET INSPECTOR','REPLAY / eth0')
        wide=w>95
        header='SEQ     SOURCE           DESTINATION      PROTO   BYTES' if not wide else 'SEQ     SOURCE           DESTINATION      PROTO   BYTES  SUMMARY'
        c.text(x+3,y+2,header[:w-6],'dark_foreground')
        for row,p in enumerate(list(self.packets)[:max(0,h-5)]):
            seq,src,dst,proto,size,info=p
            text=f'{seq:<7} {src:<16} {dst:<16} {proto:<7} {size:>5}'
            if wide:text+='  '+info
            color='cyan' if proto=='DNS' else ('magenta' if proto=='QUIC' else 'foreground')
            c.text(x+3,y+4+row,text[:w-6],color)
    def timeline(self,c,x,y,w,h,t):
        c.panel(x,y,w,h,'05 / CORRELATION LOG')
        for row,(kind,msg) in enumerate(list(self.events)[:max(0,h-4)]):
            color='yellow' if kind=='NOTE' else ('green' if kind=='PASS' else 'accent')
            c.text(x+3,y+2+row,f'{kind:<4}',color)
            c.text(x+8,y+2+row,msg[:max(0,w-11)],'foreground')

def run(fps):
    if not sys.stdout.isatty():raise SystemExit('Sentinel needs a terminal. Use --snapshot FILE for a static preview.')
    colors=palette();bg=colors['background'][1:];r,g,b=(int(bg[i:i+2],16) for i in (0,2,4))
    original=None
    if sys.stdin.isatty():original=termios.tcgetattr(sys.stdin);tty.setcbreak(sys.stdin)
    def stop(signum,frame):raise KeyboardInterrupt
    for sig in (signal.SIGTERM,signal.SIGINT,signal.SIGHUP):signal.signal(sig,stop)
    sys.stdout.write(f'\x1b[?1049h\x1b[?25l\x1b[?7l\x1b[48;2;{r};{g};{b}m\x1b[2J')
    d=Dashboard();start=time.monotonic();tick=-1
    try:
        while True:
            t=time.monotonic()-start
            if int(t*2)!=tick:tick=int(t*2);d.advance(t)
            size=shutil.get_terminal_size((140,44))
            sys.stdout.write(d.render(size.columns,size.lines,t).ansi(colors));sys.stdout.flush()
            if original and select.select([sys.stdin],[],[],1/fps)[0]:break
            if not original:time.sleep(1/fps)
    except (KeyboardInterrupt,BrokenPipeError):pass
    finally:
        if original:termios.tcsetattr(sys.stdin,termios.TCSADRAIN,original)
        sys.stdout.write('\x1b[0m\x1b[?7h\x1b[?25h\x1b[?1049l');sys.stdout.flush()

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--snapshot',type=Path);p.add_argument('--fps',type=int,default=6);a=p.parse_args()
    if a.snapshot:a.snapshot.write_text(Dashboard().render(164,46,17).svg(palette()))
    else:run(max(1,min(15,a.fps)))
