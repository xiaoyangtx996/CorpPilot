"""Early header/auth rejection sends its JSON before bounded connection teardown."""
import http.client
import socket
import time
from types import SimpleNamespace

import pytest

from test_workbench_api import running, TOKENS
from workbench import server


@pytest.mark.parametrize('rejection',['duplicate-host','unauthorized'])
def test_delayed_body_receives_original_error_without_connection_reset(tmp_path,rejection):
    with running(tmp_path) as port:
        for _ in range(8):
            connection = http.client.HTTPConnection('127.0.0.1',port,timeout=3)
            try:
                connection.putrequest('POST','/api/workbench/agents')
                connection.putheader('Content-Type','application/json')
                connection.putheader('Content-Length','2')
                if rejection == 'duplicate-host':
                    connection.putheader('Authorization','Bearer '+TOKENS[port])
                    connection.putheader('Host',f'127.0.0.1:{port}')
                connection.endheaders()
                # Reproduce the real split header/body send with a scheduling gap.
                time.sleep(0.02)
                connection.send(b'{}')
                response = connection.getresponse()
                assert response.status == (400 if rejection == 'duplicate-host' else 401)
                import json
                error = json.loads(response.read())['error']
                assert error == ('不允许的 Host' if rejection == 'duplicate-host' else '请使用本次服务启动时提供的访问入口或口令')
            finally:
                connection.close()


def handler(read, shutdown=lambda _:None):
    events = []
    return SimpleNamespace(close_connection=False,
        connection=SimpleNamespace(shutdown=shutdown,settimeout=lambda value:events.append(('timeout',value))),
        wfile=SimpleNamespace(flush=lambda:events.append(('flush',None))),
        rfile=SimpleNamespace(read1=read)), events


def test_drain_has_total_byte_bound_and_does_not_parse_content(monkeypatch):
    sizes = []
    monkeypatch.setattr(server.time,'monotonic',lambda:10)
    def read(size):
        sizes.append(size)
        return b'x'*size
    value, events = handler(read)
    server.Handler._close_rejected(value)
    assert sum(sizes) == server.MAX_BODY_BYTES+1
    assert max(sizes) <= 8192 and value.close_connection
    assert events[0][0] == 'flush'
    assert all(0 < timeout <= 0.101 for name,timeout in events if name=='timeout')


def test_drain_uses_total_deadline_not_a_fresh_timeout_per_read(monkeypatch):
    clock = iter([1.0,1.01,1.06,1.101])
    monkeypatch.setattr(server.time,'monotonic',lambda:next(clock))
    reads = []
    value, events = handler(lambda size:reads.append(size) or b'x')
    server.Handler._close_rejected(value)
    assert len(reads)==2
    timeouts = [value for name,value in events if name=='timeout']
    assert timeouts[1] < timeouts[0] <= 0.1


@pytest.mark.parametrize('failure',['timeout','closed','eof'])
def test_rejection_teardown_tolerates_timeout_closed_peer_and_eof(failure):
    def read(size):
        if failure=='timeout':raise socket.timeout()
        return b''
    def shutdown(direction):
        assert direction==socket.SHUT_WR
        if failure=='closed':raise OSError('peer gone')
    value, _ = handler(read,shutdown)
    server.Handler._close_rejected(value)
    assert value.close_connection
