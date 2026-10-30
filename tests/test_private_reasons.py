"""Private same-invocation diagnostics; synthetic local fixtures only."""
import unittest
from unittest import mock
import test_telemetry_rates as fixtures
from telemetry import rates, http_rates
import json
import copy
import test_telemetry_collector as collector_fixtures
from telemetry import collector


class CollectorReasons(unittest.TestCase):
    setUp = collector_fixtures.CollectorTests.setUp
    tick = collector_fixtures.CollectorTests.tick

    def test_sidecar_replacement_does_not_follow_symlinks_or_fifo(self):
        import os
        self.tick()
        path = self.state/'diagnostics.json'
        victim = self.root/'private-target'
        victim.write_text('unchanged')
        path.unlink()
        path.symlink_to(victim)
        self.tick()
        self.assertFalse(path.is_symlink())
        self.assertEqual(victim.read_text(), 'unchanged')
        path.unlink()
        os.mkfifo(path)
        self.tick()
        self.assertTrue(path.is_file())
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        before = path.read_bytes()
        replace = collector.os.replace
        def fail_replace(src, dst):
            if dst.name == 'diagnostics.json':
                raise OSError('private diagnostic write failure')
            return replace(src, dst)
        with mock.patch.object(collector.os, 'replace', side_effect=fail_replace):
            self.tick()
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(list(self.state.glob('.diagnostics.json.*')), [])

    def test_observer_cannot_swallow_existing_whole_tick_deadline(self):
        from telemetry.private_reasons import _callback, emit, Reason
        def expire(*args):
            raise collector.CollectionDeadline()
        token = _callback.set(expire)
        try:
            with self.assertRaises(collector.CollectionDeadline):
                emit(Reason.TCP_ATTEMPT)
        finally:
            _callback.reset(token)

    def test_recorder_is_numeric_allowlisted_and_bounded(self):
        from telemetry.private_reasons import Reason, Recorder
        record = Recorder(collector.REPOSITORIES)
        for source in record.sources:
            for rid in [None, *collector.REPOSITORIES]:
                for code in Reason:
                    record.add(source, code, rid, 2**100)
        before = json.dumps(record.snapshot(100, {'monotonic': 99, 'at': 100}, {'monotonic': 99}))
        for code in ['secret path', 99999, True]:
            record.add('tcp', code)
        record.add('tcp', Reason.TCP_ATTEMPT, 'not-allowlisted-private-name')
        after = json.dumps(record.snapshot(100, {'monotonic': 99, 'at': 100}, {'monotonic': 99}))
        self.assertEqual(before, after)
        self.assertLess(len(after), 65536)
        self.assertTrue(all(v == 2**31-1 for s in record.sources.values() for v in s['counts'].values()))
        self.assertEqual(record.sources['tcp']['monotonic'], 99)
        self.assertEqual(record.sources['tcp']['observed_at'], 100)

    def test_actual_collect_writes_private_reasons_not_public_or_cursor(self):
        snapshot = self.tick()
        path = self.state/'diagnostics.json'
        self.assertTrue(path.exists(), 'actual collector must write diagnostic sidecar')
        diagnostic = json.loads(path.read_text())
        from telemetry.private_reasons import Reason
        self.assertEqual(diagnostic['collected_at'], snapshot['collected_at'])
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(diagnostic['sources']['tcp']['counts'][str(Reason.TCP_RETRY_EXHAUSTED.value)], 1)
        self.assertIn(str(Reason.COMBINED_TCP_NULL.value), diagnostic['sources']['combined']['repositories']['archlinux'])
        self.assertIn(str(Reason.COMBINED_HTTP_NULL.value), diagnostic['sources']['combined']['repositories']['archlinux'])
        self.assertNotIn('diagnostics', json.loads((self.state/'collector.json').read_text()))
        for raw in (json.dumps(snapshot), path.read_text()):
            for secret in (str(self.root), 'cmdline', 'socket:[', '127.0.0.1', 'nginx:'):
                self.assertNotIn(secret, raw)
        path.write_text('{malformed old diagnostics')
        again = self.tick()
        self.assertEqual(snapshot, again)
        self.assertEqual(json.loads(path.read_text())['schema_version'], 1)
        real_write = collector.atomic_json
        def fail_diagnostic(path, data, **kwargs):
            if path.name == 'diagnostics.json':
                raise ValueError('private serialization failure')
            return real_write(path, data, **kwargs)
        with mock.patch.object(collector, 'atomic_json', side_effect=fail_diagnostic):
            self.assertEqual(snapshot, self.tick())



class HTTPReasons(unittest.TestCase):
    def test_http_source_failures_are_distinct_and_callbacks_cannot_raise(self):
        from telemetry.private_reasons import Reason
        workers = dict(master=[1, 1], workers=[[2, 1]], boot=1)
        cases = [('workers', 'HTTP_WORKERS_FAILED'), ('reader', 'HTTP_READ_FAILED'), ('decode', 'HTTP_DECODE_FAILED')]
        for stage, name in cases:
            events = []
            def fail(*args):
                raise OSError('must not serialize secret')
            kwargs = dict(now=100, workers_reader=fail if stage == 'workers' else lambda _: workers,
                          reader=fail if stage == 'reader' else lambda: b'{}')
            plain = http_rates.sample({'repositories': {'a': {}}}, None, **kwargs)
            observed = http_rates.sample({'repositories': {'a': {}}}, None, diagnostic=lambda *e: events.append(e), **kwargs)
            self.assertEqual(plain, observed)
            self.assertEqual(observed[1], {})
            self.assertIn((getattr(Reason, name), None, 1), events)
            self.assertEqual(plain, http_rates.sample({'repositories': {'a': {}}}, None, diagnostic=fail, **kwargs))

    def test_http_rejection_branches_do_not_change_values(self):
        from telemetry.private_reasons import Reason
        config = {'repositories': {'a': {}}}
        workers = dict(master=[1, 1], workers=[[2, 1]], boot=1)
        body = dict(schema_version=1, epoch='epoch', workers=[2], repositories={
            'a': dict(rx_bytes=10, tx_bytes=20, uncertain=0, active_uncertain=0)})
        def sample(payload, old, clock=101, callback=None, identity=None):
            return http_rates.sample(config, old, now=clock, monotonic_now=clock,
                reader=lambda: json.dumps(payload).encode(), workers_reader=lambda _: identity or workers,
                diagnostic=callback)
        # Each branch retains exactly the old public result and baseline.
        _, old = http_rates.sample(config, None, now=100, monotonic_now=100,
            reader=lambda: json.dumps(body).encode(), workers_reader=lambda _: workers)
        cases = []
        for key, value, reason in [('uncertain', 1, 'HTTP_UNCERTAIN_CHANGED'),
                                   ('active_uncertain', 1, 'HTTP_ACTIVE_UNCERTAIN'),
                                   ('rx_bytes', 0, 'HTTP_COUNTER_RESET')]:
            payload = copy.deepcopy(body)
            payload['repositories']['a'][key] = value
            cases.append((payload, old, 101, reason, 'a'))
        changed_epoch = dict(body, epoch='new')
        cases.extend([(changed_epoch, old, 101, 'HTTP_EPOCH_CHANGED', None),
                      (body, old, 103, 'HTTP_PAIR_WINDOW', None),
                      (body, None, 101, 'HTTP_BASELINE', None),
                      (dict(body, workers=[99]), old, 101, 'HTTP_WORKER_COVERAGE', None)])
        for payload, prior, clock, name, rid in cases:
            with self.subTest(name=name):
                events = []
                plain = sample(payload, prior, clock)
                observed = sample(payload, prior, clock, lambda *e: events.append(e))
                self.assertEqual(plain, observed)
                self.assertIsNone(observed[0]['a']['sync_tx_bps'])
                self.assertIn((getattr(Reason, name), rid, 1), events)
        events = []
        changed_workers = dict(workers, workers=[[2, 2]])
        result = sample(body, old, callback=lambda *e: events.append(e), identity=changed_workers)
        self.assertIsNone(result[0]['a']['sync_tx_bps'])
        self.assertIn((Reason.HTTP_IDENTITY_CHANGED, None, 1), events)



class TCPReasons(unittest.TestCase):
    setUp = fixtures.RateTests.setUp
    process = fixtures.RateTests.process
    socket = fixtures.RateTests.socket

    def test_proc_enoent_stage_retry_exhaustion_and_callback_isolation(self):
        from telemetry.private_reasons import Reason
        for filename, expected in [('stat', 'TCP_PROC_STAT_ENOENT'), ('cmdline', 'TCP_PROC_CMDLINE_ENOENT')]:
            with self.subTest(filename=filename):
                path = self.proc/'11'/filename
                data = path.read_bytes()
                path.unlink()
                events = []
                kwargs = dict(now=100, proc_root=self.proc, ss_reader=lambda: '', diag_reader=lambda: [])
                with mock.patch.object(rates.time, 'monotonic', return_value=100):
                    plain = rates.sample(self.config, None, **kwargs)
                    observed = rates.sample(self.config, None, diagnostic=lambda *e: events.append(e), **kwargs)
                    broken = rates.sample(self.config, None, diagnostic=lambda *e: (_ for _ in ()).throw(RuntimeError('secret')), **kwargs)
                self.assertEqual(plain, observed)
                self.assertEqual(plain, broken)
                self.assertEqual(observed[1], {})
                self.assertEqual(events.count((getattr(Reason, expected), None, 1)), 3)
                self.assertIn((Reason.TCP_RETRY_EXHAUSTED, None, 1), events)
                path.write_bytes(data)
        with self.assertRaisesRegex(RuntimeError, 'sampler error'):
            rates.sample(self.config, None, now=100, proc_root=self.proc,
                         ss_reader=lambda: (_ for _ in ()).throw(RuntimeError('sampler error')),
                         diagnostic=lambda *e: None)

    def test_source_failure_classification(self):
        from telemetry.private_reasons import Reason
        (self.proc/'self/ns').mkdir(parents=True)
        (self.proc/'self/ns/net').symlink_to('net:[1]')
        cases = [
            ('processes', FileNotFoundError(), 'TCP_PROCESS_TABLE_ENOENT'),
            ('read_ss', FileNotFoundError(), 'TCP_SS_ENOENT'),
            ('read_diag', FileNotFoundError(), 'TCP_DIAG_ENOENT'),
            ('read_diag', ValueError('synthetic private failure'), 'TCP_OBSERVATION_FAILED'),
        ]
        for function, error, name in cases:
            events = []
            with self.subTest(function=function), mock.patch('telemetry.rpc.protect_memory'), mock.patch.object(rates.time, 'monotonic', return_value=100), \
                 mock.patch.object(rates, 'read_ss', return_value=''), \
                 mock.patch.object(rates, function, side_effect=error):
                result = rates.sample(self.config, None, now=100, proc_root=self.proc,
                                      diagnostic=lambda *e: events.append(e))
            self.assertEqual(result[1], {})
            self.assertIn((getattr(Reason, name), None, 1), events)
        events = []
        with mock.patch.object(rates.time, 'monotonic', side_effect=[100, 103]):
            result = rates.sample(self.config, None, now=100, proc_root=self.proc,
                                  diagnostic=lambda *e: events.append(e))
        self.assertEqual(result[1], {})
        self.assertIn((Reason.TCP_DEADLINE, None, 1), events)

    def test_rpc_namespace_and_owner_churn_are_distinguished(self):
        from telemetry.private_reasons import Reason
        (self.proc/'self/ns').mkdir(parents=True)
        namespace = self.proc/'self/ns/net'
        namespace.symlink_to('net:[1]')
        events = []
        def take(ss, diag, rpc=None):
            with mock.patch.object(rates.time, 'monotonic', return_value=100):
                return rates.sample(self.config, None, now=100, proc_root=self.proc, ss_reader=ss,
                    diag_reader=diag, rpc_reader=rpc, diagnostic=lambda *e: events.append(e))
        def absent():
            raise OSError('private RPC source')
        rows, state = take(lambda: '', lambda: [], absent)
        self.assertEqual(state['repositories'], {'a': {}, 'b': {}})
        self.assertIn((Reason.TCP_RPC_UNAVAILABLE, None, 1), events)
        events.clear()
        def change_namespace():
            namespace.unlink()
            namespace.symlink_to('net:[2]')
            return []
        rows, state = take(lambda: '', change_namespace)
        self.assertEqual(state, {})
        self.assertIn((Reason.TCP_NAMESPACE_CHANGED, None, 1), events)
        events.clear()
        self.process(11, ['curl', 'synthetic'], parent=10)
        def change_owner():
            self.process(11, ['curl', 'synthetic'], parent=10, ticks=51)
            return self.socket()
        rows, state = take(change_owner, lambda: [])
        self.assertEqual(state, {})
        self.assertIn((Reason.TCP_OWNERSHIP_CHANGED, None, 1), events)
        events.clear()
        self.process(11, ['rsync', 'unclassified'], parent=0)
        rows, state = take(self.socket, lambda: [])
        self.assertEqual(state['repositories'], {})
        self.assertIn((Reason.TCP_UNCLASSIFIED_TRANSPORT, None, 1), events)

    def test_enoent_acquisition_and_verification_phases(self):
        import os
        from telemetry.private_reasons import Reason
        for pid in ('self', '11'):
            (self.proc/pid/'ns').mkdir(parents=True)
            (self.proc/pid/'ns/net').symlink_to('net:[1]')
        (self.proc/'11/fd').mkdir()
        (self.proc/'11/fd/3').symlink_to('socket:[42]')
        diag = [dict(ino=42, cookie=161, uid=os.getuid(), rx=200, tx=100, state=1)]
        cases = [('self/ns/net', 1, 'TCP_NAMESPACE_ENOENT'),
                 ('11/fd/3', 1, 'TCP_OWNER_FD_ENOENT'),
                 ('11/fd/3', 2, 'TCP_HOLDER_VERIFY_ENOENT')]
        original = rates.os.readlink
        for suffix, occurrence, name in cases:
            events, calls = [], [0]
            def readlink(path):
                if str(path).endswith(suffix):
                    calls[0] += 1
                    if calls[0] % occurrence == 0:
                        raise FileNotFoundError()
                return original(path)
            with mock.patch.object(rates.os, 'readlink', side_effect=readlink), mock.patch.object(rates.time, 'monotonic', return_value=100):
                result = rates.sample(self.config, None, now=100, proc_root=self.proc, ss_reader=self.socket,
                                      diag_reader=lambda: diag, diagnostic=lambda *e: events.append(e))
            self.assertEqual(result[1], {})
            self.assertEqual(events.count((getattr(Reason, name), None, 1)), 3)

    def test_rpc_holder_pass_and_attempt_are_recorded(self):
        from telemetry import rpc
        from telemetry.private_reasons import Reason
        (self.proc/'self/ns').mkdir(parents=True)
        (self.proc/'self/ns/net').symlink_to('net:[1]')
        for pass_number, name in [(1, 'TCP_RPC_FIRST_HOLDERS_ENOENT'), (2, 'TCP_RPC_SECOND_HOLDERS_ENOENT')]:
            events = []
            effects = ([rates.ObservationChurn()] if pass_number == 1 else [set(), rates.ObservationChurn()]) * 3
            with mock.patch.object(rpc, 'holders', side_effect=effects), mock.patch.object(rates.time, 'monotonic', return_value=100):
                result = rates.sample(self.config, None, now=100, proc_root=self.proc, ss_reader=lambda: '',
                                      diag_reader=lambda: [], rpc_reader=lambda: [], diagnostic=lambda *e: events.append(e))
            self.assertEqual(result[1], {})
            self.assertEqual(events.count((getattr(Reason, name), None, 1)), 3)
            self.assertEqual(events.count((Reason.TCP_ATTEMPT, None, 1)), 3)
            self.assertIn((Reason.TCP_RETRY_EXHAUSTED, None, 1), events)

    def test_rpc_holder_enoent_preserves_fail_closed_proof(self):
        from telemetry import rpc
        from telemetry.private_reasons import Reason, _callback
        events = []
        token = _callback.set(lambda *e: events.append(e))
        try:
            with self.assertRaises(rates.ObservationChurn):
                rpc.holders(self.proc)
            self.assertIn((Reason.TCP_HOLDER_DIR_ENOENT, None, 1), events)
            for pid in (10, 11):
                (self.proc/str(pid)/'fd').mkdir()
                (self.proc/str(pid)/'fd/3').symlink_to('socket:[42]')
            events.clear()
            with mock.patch.object(rpc.os, 'readlink', side_effect=FileNotFoundError()), self.assertRaises(rates.ObservationChurn):
                rpc.holders(self.proc)
            self.assertEqual(events, [(Reason.TCP_HOLDER_LINK_ENOENT, None, 1)])
        finally:
            _callback.reset(token)

    def test_pair_transition_reasons_preserve_null(self):
        from telemetry.private_reasons import Reason
        events = []
        def sample(text, previous, clock):
            with mock.patch.object(rates.time, 'monotonic', return_value=clock):
                return rates.sample(self.config, previous, now=clock, proc_root=self.proc,
                                    ss_reader=lambda: text, diagnostic=lambda *e: events.append(e))
        _, prior = sample(self.socket(), None, 100)
        self.assertIn((Reason.TCP_BASELINE, None, 1), events)
        cases = [(self.socket(), prior, 103, 'TCP_PAIR_WINDOW', None),
                 (self.socket(cookie='b2'), prior, 101, 'TCP_SOCKET_SET_CHANGED', 'a'),
                 (self.socket(sent=1), prior, 101, 'TCP_COUNTER_OR_IDENTITY', 'a'),
                 ('ESTAB 0 0 - - ino:42 sk:a1', prior, 101, 'TCP_LOST_ATTRIBUTED_COOKIE', 'a')]
        for text, old, clock, code, rid in cases:
            events.clear()
            rows, _ = sample(text, old, clock)
            self.assertIsNone(rows['a']['sync_rx_bps'])
            self.assertIn((getattr(Reason, code), rid, 1), events)

    def test_ownerless_veto_is_explained_without_changing_result(self):
        (self.proc/'self/ns').mkdir(parents=True)
        (self.proc/'self/ns/net').symlink_to('net:[1]')
        events = []
        kwargs = dict(now=100, proc_root=self.proc, ss_reader=lambda: '',
                      diag_reader=lambda: [dict(ino=42, cookie=1, uid=0, state=1, rx=0, tx=0)])
        with mock.patch.object(rates.time, 'monotonic', return_value=100):
            plain = rates.sample(self.config, None, **kwargs)
            observed = rates.sample(self.config, None, diagnostic=lambda *e: events.append(e), **kwargs)
        self.assertEqual(plain, observed)
        self.assertEqual(observed[1]['repositories'], {})
        self.assertIsNone(observed[0]['a']['sync_rx_bps'])
        from telemetry.private_reasons import Reason
        self.assertIn((Reason.TCP_OWNERLESS, None, 1), events)
        self.assertIn((Reason.TCP_OWNERLESS_ESTABLISHED, None, 1), events)
        self.assertIn((Reason.TCP_OWNERLESS_ROOT, None, 1), events)
        self.assertTrue(all(isinstance(code, int) and rid is None and type(count) is int
                            for code, rid, count in events))
