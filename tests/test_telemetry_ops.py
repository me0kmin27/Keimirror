"""Deployment adapters stay confined to the discovered mirror telemetry scope."""
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
REPOSITORIES = {'archlinux', 'gentoo', 'gentoo-portage', 'kali', 'kali-images',
                'cachyos', 'artix', 'artix-iso', 'artix-weekly-iso', 'opnsense',
                'endeavouros', 'fyralabs', 'manjaro', 'proxmox'}


class TelemetryOperationsTests(unittest.TestCase):
    def test_fixed_configurations_cover_only_the_verified_repositories(self):
        for number, directory in [(1, '/srv/mirrors'), (2, '/srv/data')]:
            path = ROOT / f'ops/telemetry/mirror{number}.json'
            with self.subTest(mirror=number):
                self.assertTrue(path.is_file(), 'Verified fixed configuration is missing')
                value = json.loads(path.read_text())
                self.assertEqual(value['mirror_id'], f'mirror{number}')
                self.assertEqual(set(value['repositories']), REPOSITORIES)
                grouped = [repo for ids in value['projects'].values() for repo in ids]
                self.assertEqual(len(value['projects']), 10)
                self.assertEqual(len(grouped), len(set(grouped)))
                self.assertEqual(set(grouped), REPOSITORIES)
                self.assertEqual(value['http_log'], '/var/log/nginx/keimirror-telemetry.log')
                self.assertEqual(value['rsync_log'], '/var/log/rsyncd/transfer.raw.log')
                for key, config in value['repositories'].items():
                    self.assertEqual(config['path'], f'{directory}/{key}')
                    self.assertEqual(config['user'], 'archvsync' if key in ('kali', 'kali-images') else 'me0kmin27')
                    self.assertTrue(config['script'].startswith('/home/'))
                    self.assertIn(config['kind'], ('generic', 'arch', 'kali', 'kali-images', 'opnsense'))

    def test_collectors_are_bounded_and_disk_scans_are_not_on_fast_poll(self):
        base = ROOT / 'ops/telemetry'
        for name in ('collector', 'sizes', 'aggregate'):
            service = base / f'keimirror-status-{name}.service'
            timer = base / f'keimirror-status-{name}.timer'
            self.assertTrue(service.is_file(), f'Missing service {name}')
            self.assertTrue(timer.is_file(), f'Missing timer {name}')
            text = service.read_text()
            self.assertIn('Type=oneshot', text)
            self.assertIn('NoNewPrivileges=yes', text)
            self.assertIn('ProtectSystem=strict', text)
            self.assertIn('TimeoutStartSec=', text)
            self.assertIn('CPUQuota=', text)
            self.assertIn('/var/lib/keimirror-telemetry', text)
        sizes = (base / 'keimirror-status-sizes.service').read_text()
        self.assertIn('Nice=19', sizes)
        self.assertIn('IOSchedulingClass=idle', sizes)
        self.assertIn('OnUnitInactiveSec=1h', (base / 'keimirror-status-sizes.timer').read_text())
        collector = (base / 'keimirror-status-collector.service').read_text()
        self.assertNotIn('sizes.py', collector)
        self.assertIn('RestrictAddressFamilies=AF_UNIX AF_NETLINK', collector)
        self.assertIn('OnCalendar=*-*-* *:*:*', (base / 'keimirror-status-collector.timer').read_text())
        aggregate = (base / 'keimirror-status-aggregate.service').read_text()
        self.assertIn('User=keimirror-status', aggregate)
        self.assertNotIn('DynamicUser=yes', aggregate)  # private StateDirectory would block nginx read access
        self.assertIn('OnCalendar=*-*-* *:*:*', (base / 'keimirror-status-aggregate.timer').read_text())

    def test_one_second_timers_have_precise_scheduling_and_bounded_cpu_headroom(self):
        base = ROOT / 'ops/telemetry'
        for name, quota in (('collector', '100%'), ('aggregate', '50%')):
            with self.subTest(name=name):
                timer = (base / f'keimirror-status-{name}.timer').read_text()
                self.assertIn('OnCalendar=*-*-* *:*:*\n', timer)
                self.assertIn('AccuracySec=10ms\n', timer)
                self.assertNotIn('OnUnitInactiveSec=', timer)
                self.assertNotIn('OnUnitActiveSec=', timer)
                self.assertNotIn('OnBootSec=', timer)
                service = (base / f'keimirror-status-{name}.service').read_text()
                self.assertIn(f'CPUQuota={quota}\n', service)

    def test_backend_and_ops_are_not_uploaded_as_public_static_files(self):
        workflow = (ROOT / '.github/workflows/deploy-on-pr-merge.yml').read_text()
        for directory in ('telemetry', 'ops'):
            self.assertIn(f'--exclude "{directory}"', workflow)

    def test_rsync_shims_delegate_exact_arguments_without_changing_path(self):
        for name in ('rsync', 'rsync-ssl'):
            shim = ROOT / 'ops/telemetry/bin' / name
            self.assertTrue(shim.is_file(), 'Observer shim is missing')
            source = shim.read_text()
            self.assertIn('exec /usr/bin/python3 -B /opt/keimirror-telemetry/observe.py', source)
            self.assertIn(f'--program /usr/bin/{name} "$@"', source)
            self.assertIn('--inherited-sigign', source)
            self.assertIn(')" -- "$@"', source)
            self.assertNotIn('PATH=', source)
            self.assertNotIn('eval', source)


if __name__ == '__main__':
    unittest.main()
