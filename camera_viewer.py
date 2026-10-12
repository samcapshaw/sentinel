"""Muted, preloaded public embeds in an ephemeral WebKit context."""
import json
import os
# Avoid Mesa/GStreamer GL teardown crashes in ambient video windows.
# This affects Sentinel video surfaces only, and an explicit user override wins.
os.environ.setdefault("WEBKIT_GST_DISABLE_GL_SINK", "1")
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading
import time
from urllib.parse import parse_qs, urlparse
import gi
gi.require_version('Gtk','3.0')
gi.require_version('WebKit2','4.1')
from gi.repository import Gtk, WebKit2

DWELL = 45
START_TIMEOUT = 25
SETTLE_TIME = 3
PRELOAD_LEAD = 10


def media_id(camera):
    return camera.get('video_id') or camera.get('media_url','')


def media_html(camera, generation):
    url=json.dumps(camera['media_url']).replace('<','\\u003c')
    identity=json.dumps(media_id(camera)).replace('<','\\u003c')
    head='<!doctype html><html><head><style>html,body{margin:0;width:100%;height:100%;background:#1a1b26;overflow:hidden}video,img{width:100%;height:100%;object-fit:contain}</style></head><body>'
    report="function report(state){window.webkit.messageHandlers.camera.postMessage(JSON.stringify({video:"+identity+",generation:"+str(generation)+",state:state}))}"
    if camera.get('kind')=='image':
        return head+'<img id="frame"><script>'+report+"const frame=document.getElementById('frame');function refresh(){const incoming=new Image();incoming.onload=()=>{frame.src=incoming.src;report('playing')};incoming.onerror=()=>report('error image');const url=new URL("+url+");url.searchParams.set('_sentinel',Date.now());incoming.src=url.href}refresh();setInterval(refresh,15000);</script></body></html>"
    # Native WebKit/GStreamer playback supports the providers' HLS manifests.
    # A TfL loop is a recently published short clip, explicitly labeled as such.
    loop=' loop' if camera.get('kind')=='clip' else ''
    setup="video.src="+url+";video.play().catch(()=>{});"
    script=''
    if camera.get('kind')=='hls':
        script='<script src="/hls.js"></script>'
        setup="if(Hls.isSupported()){const hls=new Hls({maxBufferLength:12,maxMaxBufferLength:20});hls.loadSource("+url+");hls.attachMedia(video);hls.on(Hls.Events.MANIFEST_PARSED,()=>video.play().catch(()=>{}));hls.on(Hls.Events.ERROR,(event,data)=>{if(data.fatal)report('error hls')})}else{"+setup+"}"
    return head+'<video id="video" autoplay muted playsinline'+loop+'></video>'+script+'<script>'+report+"const video=document.getElementById('video');video.muted=true;video.onplaying=()=>report('playing');video.onwaiting=()=>report('buffering');video.onerror=()=>report('error video');video.onended=()=>report('ended');"+setup+"</script></body></html>"


def camera_html(video_id, generation=0):
    if isinstance(video_id,dict):
        if video_id.get('media_url'):return media_html(video_id,generation)
        video_id=video_id['video_id']
    return '''<!doctype html><html><head><meta name="referrer" content="strict-origin-when-cross-origin"><style>
html,body,#player{margin:0;width:100%;height:100%;background:#1a1b26;overflow:hidden}
</style></head><body><div id="player"></div><script>
function report(state){window.webkit.messageHandlers.camera.postMessage(JSON.stringify({video:VIDEO,generation:GENERATION,state:state}))}
let player;function onYouTubeIframeAPIReady(){player=new YT.Player('player',{videoId:VIDEO,
playerVars:{autoplay:1,mute:1,playsinline:1,rel:0,origin:location.origin},events:{
onReady:e=>{e.target.mute();e.target.playVideo()},onStateChange:e=>{if(e.data===1)report('playing');else if(e.data===3)report('buffering');else if(e.data===0)report('ended')},
onError:e=>report('error '+e.data)}})}
</script><script src="https://www.youtube.com/iframe_api"></script></body></html>'''.replace('VIDEO',json.dumps(video_id)).replace('GENERATION',str(generation))


class CameraViewer:
    def __init__(self, overlay, canvas, dismiss, cameras=None):
        self.cameras=[c for c in (cameras if cameras is not None else json.loads((Path(__file__).parent/'cameras.json').read_text())) if c.get('video_id') or c.get('media_url')]
        self.index=-1;self.cursor=-1;self.active=False;self.started=0;self.status='CONNECTING';self.failures={};self.played=False;self.last_error=''
        self.current=None;self.pending=None;self.generation=0;self.canvas=canvas
        self.fixed=Gtk.Fixed();self.fixed.set_hexpand(True);self.fixed.set_vexpand(True)
        overlay.add_overlay(self.fixed);overlay.set_overlay_pass_through(self.fixed,True)
        context=WebKit2.WebContext.new_ephemeral()
        self.slots=[]
        for number in range(2):
            manager=WebKit2.UserContentManager();manager.register_script_message_handler('camera')
            manager.connect('script-message-received::camera',self.message,number)
            web=WebKit2.WebView(web_context=context,user_content_manager=manager)
            web.set_can_focus(False);web.set_is_muted(True);web.set_opacity(0)
            settings=web.get_settings();settings.set_media_playback_requires_user_gesture(False)
            settings.set_enable_write_console_messages_to_stdout(False)
            web.connect('permission-request',lambda view,request:(request.deny(),True)[1])
            web.connect('create',lambda *args:None)
            web.connect('load-failed',self.load_failed,number)
            web.connect('web-process-terminated',lambda *args,n=number:self.failed(n))
            web.connect('key-press-event',dismiss);web.connect('button-press-event',dismiss)
            self.fixed.put(web,0,0);web.set_no_show_all(True)
            self.slots.append({'web':web,'index':-1,'generation':-1,'started':0,'playing_at':None})
        viewer=self
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                parsed=urlparse(self.path);query=parse_qs(parsed.query)
                if parsed.path=='/hls.js':
                    content=(Path(__file__).parent/'vendor/hls.min.js').read_bytes()
                    self.send_response(200);self.send_header('Content-Type','application/javascript');self.send_header('Content-Length',str(len(content)));self.end_headers()
                    try:self.wfile.write(content)
                    except (BrokenPipeError,ConnectionResetError):pass
                    return
                if parsed.path!='/player':self.send_error(404);return
                try:
                    index=int(query['camera'][0]);generation=int(query['generation'][0])
                    if not 0<=index<len(viewer.cameras):raise ValueError()
                except (ValueError,KeyError):self.send_error(400);return
                content=camera_html(viewer.cameras[index],generation).encode()
                self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8');self.send_header('Cache-Control','no-store');self.send_header('Content-Length',str(len(content)));self.end_headers()
                try:self.wfile.write(content)
                except (BrokenPipeError,ConnectionResetError):pass
            def log_message(self,*args):pass
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler);self.server.daemon_threads=True
        threading.Thread(target=self.server.serve_forever,daemon=True).start()

    @property
    def web(self):
        return self.slots[self.current if self.current is not None else self.pending if self.pending is not None else 0]['web']

    def message(self,manager,result,number):
        if not self.active:return
        slot=self.slots[number]
        try:
            message=json.loads(result.get_js_value().to_string())
            if message['generation']!=slot['generation'] or message['video']!=media_id(self.cameras[slot['index']]):return
            value=message['state']
        except (ValueError,KeyError,IndexError):return
        if value=='playing':
            if slot['playing_at'] is None:slot['playing_at']=time.monotonic()
            if number==self.current:self.status=self.live_label()
        elif value=='buffering':
            slot['playing_at']=None
            if number==self.current:self.status='BUFFERING'
        else:self.failed(number,value)

    def live_label(self):
        kind=self.cameras[self.index].get('kind')
        if kind=='image':return 'PUBLIC SNAPSHOT / REFRESH 15s'
        if kind=='clip':return 'RECENT TRAFFIC CLIP / LOOP'
        return 'LIVE VIDEO'

    def load_failed(self,view,event,uri,error,number):
        if uri.startswith('http://127.0.0.1:') and error.code!=302:self.failed(number,error.message)
        return True

    def failed(self,number,reason="player failure"):
        if not self.active:return
        self.last_error=reason
        slot=self.slots[number]
        camera=self.cameras[slot['index']] if 0<=slot['index']<len(self.cameras) else {}
        if camera.get('kind')=='hls' and camera.get('fallback_url'):
            self.cameras[slot['index']]={**camera,'kind':'image','media_url':camera['fallback_url'],'fallback_url':''}
            self.cursor=slot['index']-1
        else:self.failures[slot['index']]=time.monotonic()+600
        slot['web'].set_opacity(0);slot['web'].load_uri('about:blank');slot['playing_at']=None
        if number==self.pending:self.pending=None
        if number==self.current:self.current=None;self.played=False
        self.status='UNAVAILABLE / SWITCHING' if self.current is None else self.live_label()

    def next(self):
        if self.pending is not None:return
        now=time.monotonic()
        for offset in range(1,len(self.cameras)+1):
            candidate=(self.cursor+offset)%len(self.cameras)
            if self.failures.get(candidate,0)>now:continue
            number=0 if self.current!=0 else 1;slot=self.slots[number]
            self.cursor=candidate;self.pending=number;self.generation+=1
            slot.update(index=candidate,generation=self.generation,started=now,playing_at=None)
            if self.current is None:self.index=candidate;self.status='CONNECTING';self.started=now
            slot['web'].set_opacity(0);slot['web'].show()
            slot['web'].load_uri('http://127.0.0.1:%d/player?camera=%d&generation=%d'%(self.server.server_port,candidate,self.generation))
            return
        if self.current is None:self.status='FEEDS UNAVAILABLE / RETRYING LATER'

    def update(self,visible):
        if visible and not self.active:self.active=True
        elif not visible and self.active:
            self.active=False;self.current=None;self.pending=None;self.played=False
            for slot in self.slots:slot['web'].set_opacity(0);slot['web'].load_uri('about:blank');slot['web'].hide()
        if not self.active:return
        now=time.monotonic()
        width=self.canvas.get_allocated_width();height=self.canvas.get_allocated_height();scale=min(width/1600,height/1000)
        x=(width-1600*scale)/2+690*scale;y=(height-1000*scale)/2+288*scale
        for slot in self.slots:
            self.fixed.move(slot['web'],round(x),round(y));slot['web'].set_size_request(max(1,round(830*scale)),max(1,round(467*scale)))
        if self.pending is None and (self.current is None or now-self.started>=DWELL-PRELOAD_LEAD):self.next()
        if self.pending is not None:
            number=self.pending;slot=self.slots[number]
            if slot['playing_at'] is None and now-slot['started']>START_TIMEOUT:self.failed(number)
            elif slot['playing_at'] is not None and now-slot['playing_at']>=SETTLE_TIME and (self.current is None or now-self.started>=DWELL):
                outgoing=self.current
                self.current=number;self.pending=None;self.index=slot['index'];self.started=now;self.played=True;self.status=self.live_label()
                slot['web'].set_opacity(1)
                if outgoing is not None:
                    old=self.slots[outgoing]['web'];old.set_opacity(0);old.load_uri('about:blank');old.hide()

    def set_cameras(self,cameras):
        self.update(False);self.cameras=list(cameras);self.index=-1;self.cursor=-1;self.failures={};self.generation+=1
        for slot in self.slots:slot['generation']=-1

    def close(self):
        self.active=False
        for slot in self.slots:slot['web'].stop_loading();slot['web'].load_uri('about:blank')
        self.server.shutdown();self.server.server_close()
