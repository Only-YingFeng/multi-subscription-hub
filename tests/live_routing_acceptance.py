"""Opt-in checks on dedicated hub listeners; never writes to main Clash."""
from pathlib import Path
import json
import socket
import ssl
import struct
import sys
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend import Backend
from clash_api import api
import yaml


def probe(entry, controller, domain, expected_chain, reject=False):
    result = {'port': entry['port'], 'reject': reject, 'passed': False}
    stream = None
    negotiated = False
    try:
        stream = socket.create_connection(('127.0.0.1', entry['port']), 12)
        stream.settimeout(15)
        def read(count):
            data = b''
            while len(data) < count:
                block = stream.recv(count-len(data))
                if not block:
                    raise ConnectionError()
                data += block
            return data
        stream.sendall(b'\x05\x01\x00')
        assert read(2) == b'\x05\x00'
        negotiated = True
        host = domain.encode('idna')
        stream.sendall(b'\x05\x01\x00\x03'+bytes([len(host)])+host+struct.pack('!H',443))
        header = read(4)
        if header[1] != 0:
            result['passed'] = reject
            return result
        if header[3] == 1: read(6)
        elif header[3] == 4: read(18)
        elif header[3] == 3: read(read(1)[0]+2)
        if reject:
            # Mihomo acknowledges SOCKS before the outbound route finishes.
            # Reject must close the stream before a valid TLS connection exists.
            try:
                with ssl.create_default_context().wrap_socket(stream, server_hostname=domain):
                    pass
            except (ssl.SSLError, ConnectionResetError, ConnectionAbortedError):
                result['passed'] = True
                result['closedBeforeTLS'] = True
            return result
        with ssl.create_default_context().wrap_socket(stream, server_hostname=domain) as tls:
            connections = api('GET','/connections',config=controller)[1]['connections']
            match = next((c for c in reversed(connections) if
                str(c.get('metadata',{}).get('sourcePort')) == str(tls.getsockname()[1]) and
                c.get('metadata',{}).get('host') == domain), None)
            assert match
            result['rule'] = match.get('rule')
            result['chainVerified'] = expected_chain in match.get('chains', [])
            tls.sendall(('GET / HTTP/1.1\r\nHost: '+domain+'\r\nConnection: close\r\n\r\n').encode())
            first = tls.recv(2048).split(b'\r\n',1)[0].split()
            result['httpStatus'] = int(first[1]) if len(first)>1 else 0
            result['passed'] = result['chainVerified'] and 200 <= result['httpStatus'] < 400
    except (OSError, AssertionError, KeyError, ValueError):
        # A rejected SOCKS request is expected to close before TLS negotiation.
        result['passed'] = bool(reject and negotiated)
    finally:
        if stream: stream.close()
    return result


def main():
    backend = Backend(Path(sys.argv[1]) / 'private')
    state = backend._state()
    config = yaml.safe_load(backend._path('config.yaml').read_text(encoding='utf8'))
    snapshot = json.loads(backend._path('logs/connectivity.private.json').read_text(encoding='utf8'))
    available = {r['nodeId'] for r in snapshot['rows'] if r['status']=='available'}
    chosen = {}
    for entry in state['entries']:
        if entry['id'] in available:
            chosen.setdefault(entry['sourceId'],entry)
    assert len(chosen)==2
    entries=list(chosen.values())
    with ThreadPoolExecutor(max_workers=2) as executor:
        proxy=list(executor.map(lambda e: probe(e,config,'example.com',e['internalName']),entries))
    direct=probe(entries[0],config,'www.baidu.com','DIRECT')
    result={'simultaneousSources':proxy,'direct':direct,
            'reject':'Current snapshot has no unconditional REJECT domain; tested separately with generated routing.'}
    backend._path('logs/routing-acceptance.private.json').write_text(json.dumps(result,indent=2),encoding='utf8')
    print(json.dumps(result),flush=True)
    return 0 if all(r['passed'] for r in proxy+[direct]) else 1

if __name__=='__main__':
    raise SystemExit(main())
