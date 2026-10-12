"""Bounded Caltrans frame checks reject the provider's white/blue offline card."""
import queue,threading,time,urllib.request
import gi
gi.require_version('GdkPixbuf','2.0')
from gi.repository import GdkPixbuf


def offline_placeholder(raw):
    loader=GdkPixbuf.PixbufLoader();loader.write(raw);loader.close()
    image=loader.get_pixbuf().scale_simple(64,36,GdkPixbuf.InterpType.BILINEAR)
    return offline_pixels(image.get_pixels(),image.get_n_channels(),image.get_rowstride())

def offline_pixels(data,channels,stride):
    white=blue=total=0
    # The bottom timestamp changes even when the unavailable card is unchanged.
    for y in range(2,31):
        for x in range(2,62):
            offset=y*stride+x*channels;r,g,b=data[offset:offset+3];total+=1
            white+=min(r,g,b)>240
            blue+=r<65 and g<65 and b>75
    return white/total>.80 and blue/total>.008

def probe_frame(url):
    try:
        request=urllib.request.Request(url,headers={'User-Agent':'Sentinel/1.0 (+https://github.com/samcapshaw/sentinel)'})
        with urllib.request.urlopen(request,timeout=4) as response:raw=response.read(750001)
        return len(raw)<=750000 and not offline_placeholder(raw)
    except Exception:return False


def healthy_catalog(cameras,timeout=20):
    """Check bounded JPEG previews with four daemon workers; never download video."""
    jobs=queue.Queue();results={};lock=threading.Lock();stop=threading.Event()
    for index,camera in enumerate(cameras):jobs.put((index,camera))
    def worker():
        while not stop.is_set():
            try:index,camera=jobs.get_nowait()
            except queue.Empty:return
            url=camera.get('fallback_url') or camera.get('media_url','')
            healthy=probe_frame(url)
            with lock:results[index]=healthy
    workers=[threading.Thread(target=worker,daemon=True) for _ in range(4)]
    deadline=time.monotonic()+timeout
    for thread in workers:thread.start()
    for thread in workers:thread.join(max(0,deadline-time.monotonic()))
    stop.set()
    with lock:return [camera for index,camera in enumerate(cameras) if results.get(index)]


class FrameHealth:
    def __init__(self):
        self.lock=threading.Lock();self.results={};self.pending=set();self.jobs=queue.Queue();self.closed=False
        for _ in range(2):threading.Thread(target=self.worker,daemon=True).start()
    def check(self,camera):
        url=camera.get('fallback_url') or (camera.get('media_url') if camera.get('kind')=='image' else '')
        if 'Caltrans' not in camera.get('provider','') or not url:return True
        with self.lock:
            result=self.results.get(url)
            if result and time.monotonic()<result[1]:return result[0]
            if url not in self.pending and len(self.pending)<4:
                self.pending.add(url);self.jobs.put(url)
        return None
    def worker(self):
        while True:
            url=self.jobs.get()
            if url is None:return
            if self.closed:continue
            healthy=probe_frame(url)
            with self.lock:
                self.results[url]=(healthy,time.monotonic()+(120 if not healthy else 90));self.pending.discard(url)
    def close(self):
        self.closed=True
        for _ in range(2):self.jobs.put(None)
