"""Generated routing runtime test on a temporary private listener only."""
import copy
import json
from pathlib import Path
import socket
import subprocess
import sys
import time
import yaml

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from backend import Backend, convert_routing, _port_available
from live_routing_acceptance import probe
import bridge_lifecycle as life


def main():
    backend=Backend(Path(sys.argv[1])/'private')
    state=backend._state()
    config=yaml.safe_load(backend._path('config.yaml').read_text(encoding='utf8'))
    entry=copy.deepcopy(state['entries'][0])
    port=48080
    assert _port_available(port)
    entry['port']=port
    node=next(n for n in config['proxies'] if n['name']==entry['internalName'])
    generated=convert_routing({'proxies':[node], 'rules':['DOMAIN,www.baidu.com,DIRECT','DOMAIN,line.me,REJECT','MATCH,PROXY']},[entry],{'PROXY':'PROXY'})
    # No public controller, TUN, system proxy or DNS port in this temporary core.
    generated.update({'mode':'rule','allow-lan':False,'log-level':'silent','tun':{'enable':False},'dns':config['dns']})
    generated['external-controller-pipe']=config['external-controller-pipe']+'-acceptance'
    generated['secret']=config['secret']
    path=backend._path('logs/deny-acceptance.private.yaml')
    path.write_text(yaml.safe_dump(generated,allow_unicode=True,sort_keys=False),encoding='utf8')
    backend._validate(path)
    process=None
    try:
        with life._child_dll_search():
            process=subprocess.Popen([str(backend._path('bin/mihomo.exe')), '-d', str(backend._path('data')), '-f',str(path)],
                stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW)
        deadline=time.monotonic()+15
        while time.monotonic()<deadline:
            if life.listeners_match(life.listener_rows(process.pid,[port]),process.pid,[port]):break
            assert process.poll() is None
            time.sleep(.2)
        assert life.listeners_match(life.listener_rows(process.pid,[port]),process.pid,[port])
        deny=probe(entry,generated,'line.me','REJECT',reject=True)
        direct=probe(entry,generated,'www.baidu.com','DIRECT')
        proxy=probe(entry,generated,'example.com',entry['internalName'])
        result={'generatedCoreRuntime':True,'reject':deny,'direct':direct,'matchFixedProxy':proxy}
        backend._path('logs/deny-acceptance.private.json').write_text(json.dumps(result,indent=2),encoding='utf8')
        print(json.dumps(result),flush=True)
        return 0 if all(r['passed'] for r in (deny,direct,proxy)) else 1
    finally:
        if process is not None and process.poll() is None:
            # This exact Popen handle owns the temporary process created above.
            process.terminate()
            process.wait(timeout=10)

if __name__=='__main__':raise SystemExit(main())
