"""Read-only local telemetry; no packet payload capture or security verdicts."""
from collections import deque
from pathlib import Path
import os
import subprocess
import threading
import time
from orbits import fetch_json

class Telemetry:
    def __init__(self):
        self.lock = threading.Lock()
        self.data = {'connections': [], 'interfaces': [], 'bps': 0, 'pps': 0, 'load': 0, 'memory': 0, 'events': [], 'kev': [], 'kev_status': 'Loading CISA catalog', 'kev_updated': 0, 'status': 'Starting sampler'}
        threading.Thread(target=self.sample, daemon=True).start()
        threading.Thread(target=self.catalog, daemon=True).start()

    def snapshot(self):
        with self.lock:
            return dict(self.data)

    def sample(self):
        previous = None
        previous_flows = set()
        events = deque(maxlen=24)
        while True:
            try:
                interfaces = []
                received = transmitted = packets = 0
                for line in Path('/proc/net/dev').read_text().splitlines()[2:]:
                    name, values = line.split(':',1)
                    name = name.strip()
                    if name=='lo':continue
                    parts = list(map(int,values.split()))
                    interfaces.append(name)
                    received += parts[0]; transmitted += parts[8]; packets += parts[1]+parts[9]
                now = time.monotonic()
                bps = pps = 0
                if previous:
                    elapsed = now-previous[0]
                    bps = max(0,(received+transmitted-previous[1])*8/elapsed)
                    pps = max(0,(packets-previous[2])/elapsed)
                previous = now, received+transmitted, packets
                result = subprocess.run(['ss','-H','-tun','state','established'],capture_output=True,text=True,timeout=3,check=True)
                connections = []
                for line in result.stdout.splitlines():
                    fields = line.split()
                    if len(fields)>=5:
                        connections.append((fields[0].upper(), fields[3], fields[4]))
                flows = set(connections)
                for flow in sorted(flows-previous_flows)[:5]:events.appendleft((time.strftime('%H:%M:%S'), 'OPEN '+flow[0]+' '+flow[2]))
                for flow in sorted(previous_flows-flows)[:5]:events.appendleft((time.strftime('%H:%M:%S'), 'CLOSED '+flow[0]+' '+flow[2]))
                previous_flows = flows
                memory = {}
                for line in Path('/proc/meminfo').read_text().splitlines():
                    key,value = line.split(':',1);memory[key]=int(value.split()[0])
                usage = 100*(1-memory['MemAvailable']/memory['MemTotal'])
                with self.lock:
                    self.data.update(connections=connections, interfaces=interfaces, bps=bps, pps=pps, load=os.getloadavg()[0], memory=usage, events=list(events), status='Live local metadata')
            except Exception as exc:
                with self.lock:self.data['status']='Sampler unavailable: '+type(exc).__name__
            time.sleep(2)

    def catalog(self):
        while True:
            try:
                value, updated = fetch_json('https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json','cisa-kev.json')
                vulnerabilities = sorted(value['vulnerabilities'],key=lambda v:v['dateAdded'],reverse=True)
                with self.lock:self.data.update(kev=vulnerabilities,kev_status='CISA KEV / catalog '+value.get('dateReleased','')[:10],kev_updated=updated)
            except Exception as exc:
                with self.lock:self.data['kev_status']='CISA source unavailable: '+type(exc).__name__
            time.sleep(21600)
