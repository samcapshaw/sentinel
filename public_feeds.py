"""Small timestamped public-feed cache with honest stale fallback."""
import hashlib,json,time,urllib.request
from pathlib import Path

def fetch(url,ttl=300,xml=False):
    path=Path.home()/'.cache/sentinel/feeds'/(hashlib.sha256(url.encode()).hexdigest()+'.json')
    cached=None
    try:
        cached=json.loads(path.read_text())
        if time.time()-cached['fetched']<ttl:return cached['data'],cached['fetched'],False
    except (OSError,ValueError,KeyError,TypeError):cached=None
    try:
        request=urllib.request.Request(url,headers={'User-Agent':'Sentinel/1.0 (+https://github.com/samcapshaw/sentinel)','Accept':'application/geo+json,application/json,application/xml,text/xml,*/*'})
        with urllib.request.urlopen(request,timeout=15) as response:raw=response.read(8_000_001)
        if len(raw)>8_000_000:raise ValueError('Public feed exceeds size limit')
        data=raw.decode('utf-8-sig') if xml else json.loads(raw)
        if xml:
            import xml.etree.ElementTree as ET
            ET.fromstring(data) # Never replace a usable XML cache with an error page.
        stamp=time.time();path.parent.mkdir(parents=True,exist_ok=True)
        temp=path.with_suffix('.tmp');temp.write_text(json.dumps({'data':data,'fetched':stamp}));temp.replace(path)
        return data,stamp,False
    except Exception:
        if cached:return cached['data'],cached['fetched'],True
        raise
