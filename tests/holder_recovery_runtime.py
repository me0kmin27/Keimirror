"""Run only in a disposable user/PID/net/mount namespace (test harness)."""
import fcntl
import json
import os
import pwd
import re
import socket
import struct
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from telemetry import rates
from telemetry.private_reasons import Recorder

# Never change the host interface: require the isolated PID namespace.
assert os.getpid() == 1
with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as control:
    fcntl.ioctl(control, 0x8914, struct.pack('16sH14s', b'lo', 0x49, b''))
config = {'repositories': {'a': dict(user=pwd.getpwuid(os.getuid()).pw_name,
                                    script='/not-a-job', path='/not-a-project')}}
with socket.socket() as listener, socket.socket() as client:
    listener.bind(('127.0.0.1', 0))
    listener.listen()
    client.connect(listener.getsockname())
    peer, _ = listener.accept()
    with peer:
        client.sendall(b'bounded-loopback')
        assert peer.recv(16) == b'bounded-loopback'
        # Fixture strips only ownership from real ss records. Proc traversal,
        # FDs, start ticks, credentials, netns, diagnostics and clocks stay real.
        def ownerless_ss():
            text = rates.read_ss()
            assert 'pid=1,' in text, text
            text = re.sub(r'users:\(\(.*?\)\)', '', text)
            assert 'pid=' not in text
            return text
        recorder = Recorder(config['repositories'])
        def take(old=None):
            return rates.sample(config, old, now=None, ss_reader=ownerless_ss,
                                diag_reader=lambda: rates.read_diag(endpoints=True),
                                diagnostic=recorder.sink('tcp'))
        rows, old = take()
        assert old['repositories'] == {'a': {}}, (rows, old)
        time.sleep(0.02)
        rows, state = take(old)
        assert rows['a']['sync_tx_bps'] == rows['a']['sync_rx_bps'] == 0, rows
        assert recorder.sources['tcp']['repositories']['a']['37'] == 4
        # Closing a socket after the first dump cannot be positive live proof.
        calls = [0]
        def closing_diag():
            result = rates.read_diag(endpoints=True)
            calls[0] += 1
            if calls[0] == 1:
                client.shutdown(socket.SHUT_WR)
            return result
        rows, state = rates.sample(config, old, now=None, ss_reader=lambda: '', diag_reader=closing_diag)
        assert rows['a']['sync_tx_bps'] is None, (rows, state)
        print(json.dumps({'real_loopback': True, 'uid': os.getuid(),
                          'adjacent_empty_zero': True, 'recovered_socket_observations': 4,
                          'state_transition_unknown': True}))
