"""Read/write only an explicitly supplied loopback or private pipe controller."""
import ctypes, json, msvcrt, time, urllib.request

def api(method, path, payload=None, config=None, timeout=None):
    """Use the existing controller; never create a new public control port."""
    if timeout is not None and (isinstance(timeout, bool) or not isinstance(timeout, (int, float))
                                or not 0 < timeout <= 300):
        raise ValueError('Invalid local API timeout')
    explicit_deadline = time.monotonic() + timeout if timeout is not None else None
    cfg = config or {}
    secret = str(cfg.get('secret', ''))
    body = b'' if payload is None else json.dumps(payload).encode('utf8')
    endpoint = cfg.get('external-controller', '')
    if endpoint:
        host = endpoint.rsplit(':', 1)[0].strip('[]')
        if host not in ('127.0.0.1', 'localhost', '::1'):
            raise RuntimeError('Controller is not loopback; stopped')
        req = urllib.request.Request('http://' + endpoint + path, data=body if payload is not None else None,
                                     method=method, headers={'Authorization': 'Bearer ' + secret,
                                     'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=8 if timeout is None else timeout) as response:
            response_body = response.read()
            return response.status, json.loads(response_body) if response_body else None
    pipe = cfg.get('external-controller-pipe')
    if not pipe or not pipe.startswith('\\\\.\\pipe\\'):
        raise RuntimeError('No supported local controller')
    pipe_wait_ms = 5000 if timeout is None else min(5000, max(1, int(timeout * 1000)))
    if not ctypes.windll.kernel32.WaitNamedPipeW(pipe, pipe_wait_ms):
        raise RuntimeError('Local controller pipe unavailable')
    with open(pipe, 'r+b', buffering=0) as stream:
        deadline = explicit_deadline if explicit_deadline is not None else time.monotonic() + (25 if method == 'PUT' else 8)
        handle = ctypes.c_void_p(msvcrt.get_osfhandle(stream.fileno()))
        peek = ctypes.windll.kernel32.PeekNamedPipe
        peek.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong,
                         ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong), ctypes.c_void_p]
        peek.restype = ctypes.c_int
        request = (method + ' ' + path + ' HTTP/1.1\r\nHost: localhost\r\nAuthorization: Bearer ' + secret +
                   '\r\nConnection: close\r\nContent-Type: application/json\r\nContent-Length: ' + str(len(body)) + '\r\n\r\n').encode() + body
        stream.write(request)

        def exact(count):
            data = b''
            while len(data) < count:
                available = ctypes.c_ulong()
                if not peek(handle, None, 0, None, ctypes.byref(available), None):
                    raise RuntimeError('Local API pipe closed before complete response')
                if available.value == 0:
                    if time.monotonic() >= deadline:
                        if timeout is not None:
                            raise TimeoutError('Local API response timed out')
                        raise RuntimeError('Local API response timed out; application state requires recovery verification')
                    time.sleep(0.005)
                    continue
                part = stream.read(min(count - len(data), available.value))
                if not part:
                    raise RuntimeError('Incomplete local API response')
                data += part
            return data

        def line():
            data = b''
            while not data.endswith(b'\r\n'):
                data += exact(1)
                if len(data) > 65536:
                    raise RuntimeError('Oversize API header')
            return data

        status = int(line().split()[1])
        headers = {}
        while True:
            row = line()
            if row == b'\r\n':
                break
            key, value = row.decode().split(':', 1)
            headers[key.lower()] = value.strip()
        if 'content-length' in headers:
            response_body = exact(int(headers['content-length']))
        elif headers.get('transfer-encoding', '').lower() == 'chunked':
            response_body = b''
            while True:
                size = int(line().split(b';')[0].strip(), 16)
                if size == 0:
                    line()
                    break
                response_body += exact(size)
                exact(2)
                if len(response_body) > 32 * 1024 * 1024:
                    raise RuntimeError('Oversize API body')
        else:
            response_body = b''
        if not 200 <= status < 300:
            raise RuntimeError('Local API failed: HTTP ' + str(status))
        return status, json.loads(response_body) if response_body else None

