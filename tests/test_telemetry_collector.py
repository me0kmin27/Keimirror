"""Synthetic fixtures only: no production client addresses or raw logs."""
from datetime import datetime
import io
import json
import os
from pathlib import Path
import pwd
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

from telemetry import collector

COLLECTOR_PATH = Path(__file__).resolve().parents[1] / "telemetry/collector.py"

GROUPS = {
    "archlinux": ["archlinux"], "gentoo": ["gentoo", "gentoo-portage"],
    "kali": ["kali", "kali-images"], "cachyos": ["cachyos"],
    "artix": ["artix", "artix-iso", "artix-weekly-iso"],
    "opnsense": ["opnsense"], "endeavouros": ["endeavouros"],
    "fyralabs": ["fyralabs"], "manjaro": ["manjaro"], "proxmox": ["proxmox"],
}
METRICS = {
    "state", "last_attempt_at", "last_finished_at", "last_success_at", "exit_code",
    "warning", "size_bytes", "size_measured_at", "sync_rx_bps", "sync_tx_bps",
    "rates_observed_at", "http_bytes_60s", "rsync_bytes_60s", "traffic_observed_since",
}


class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR"))
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.state = self.root / "state"
        self.proc = self.root / "proc"
        self.proc.mkdir()
        self.now = 1793170800
        self.config = {
            "mirror_id": "mirror1", "public_host": "mirror.example.invalid",
            "projects": GROUPS,
            "repositories": {
                rid: {"path": str(self.root / "data" / rid),
                      "script": str(self.root / "scripts" / (rid + ".sh")),
                      "user": pwd.getpwuid(os.getuid()).pw_name,
                      "kind": {"archlinux": "arch", "kali": "kali", "kali-images": "kali-images", "opnsense": "opnsense"}.get(rid, "generic"),
                      "log": str(self.root / "logs" / (rid + ".log")),
                      "trace": None, "lock": str(self.root / "locks" / rid)}
                for ids in GROUPS.values() for rid in ids
            },
            "http_log": str(self.root / "http.log"),
            "rsync_log": str(self.root / "rsync.log"),
        }

    def tick(self, now=None, **kwargs):
        return collector.collect(self.config, self.state, now=self.now if now is None else now,
                                 proc_root=self.proc, **kwargs)

    def repo(self, snapshot, rid):
        return next(r for p in snapshot["projects"].values() for r in p["repositories"] if r["id"] == rid)

    def put_json(self, path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")

    def observer(self, rid, **updates):
        record = {"schema_version": 1, "repository": rid, "phase": "finished",
                  "pid": 123, "start_ticks": 500, "boot_id": "00000000-0000-4000-8000-000000000001",
                  "started_at": self.now - 30, "finished_at": self.now - 10,
                  "exit_code": 0, "last_success_at": self.now - 10}
        record.update(updates)
        if record["exit_code"] != 0 and "last_success_at" not in updates:
            record["last_success_at"] = None
        self.put_json(self.state / "runs" / rid / "state.json", record)

    def test_tcp_rates_integrate_and_idle_sibling_does_not_block_group(self):
        stamp = self.now + 1
        known_zero = {rid: dict(sync_rx_bps=0, sync_tx_bps=0, rates_observed_at=stamp)
                      for rid in self.config['repositories']}
        tcp = {rid: dict(value) for rid, value in known_zero.items()}
        tcp['gentoo']['sync_rx_bps'] = 300
        with mock.patch('telemetry.rates.sample', return_value=(tcp, {'at': stamp})), \
             mock.patch('telemetry.http_rates.sample', return_value=(known_zero, {})):
            second = self.tick(now=stamp)
        self.assertEqual(self.repo(second, 'gentoo')['sync_rx_bps'], 300)
        self.assertEqual(second['projects']['gentoo']['sync_rx_bps'], 300)
        self.assertEqual(self.repo(second, 'gentoo-portage')['sync_rx_bps'], 0)
        self.assertEqual(second['projects']['gentoo']['rates_observed_at'], stamp)
        private = json.loads((self.state/'collector.json').read_text())
        self.assertEqual(private['rates']['at'], stamp)
        self.assertIn('http_rates', private)

    def test_runtime_collection_uses_socket_sample_clock_not_tick_start(self):
        rows = {rid: dict(sync_rx_bps=0, sync_tx_bps=0, rates_observed_at=self.now+.5)
                for rid in self.config['repositories']}
        with mock.patch.object(collector.time, 'time', return_value=self.now), \
             mock.patch('telemetry.rates.sample', return_value=(rows, {'at': self.now+.5})) as tcp, \
             mock.patch('telemetry.http_rates.sample', return_value=(rows, {})) as http:
            snapshot = collector.collect(self.config, self.state, proc_root=self.proc)
        self.assertIsNone(tcp.call_args.kwargs['now'])
        self.assertIsNone(http.call_args.kwargs['now'])
        private = json.loads((self.state / 'collector.json').read_text())
        self.assertEqual(snapshot['collected_at'], self.now)
        self.assertEqual(private['rates']['at'], self.now + .5)
        self.assertEqual(snapshot['projects']['gentoo']['rates_observed_at'], self.now + .5)

    def test_group_rates_include_downloads_when_sync_is_finished(self):
        download = dict(collector.empty_metrics(), state='completed', sync_rx_bps=3,
                        sync_tx_bps=500, rates_observed_at=self.now)
        idle = dict(collector.empty_metrics(), sync_rx_bps=0, sync_tx_bps=0,
                    rates_observed_at=self.now - .1)
        result = collector.group_metrics([download, idle])
        self.assertEqual(result['sync_rx_bps'], 3)
        self.assertEqual(result['sync_tx_bps'], 500)
        self.assertEqual(result['rates_observed_at'], self.now - .1)
        self.assertIsNone(collector.group_metrics([download, collector.empty_metrics()])['sync_tx_bps'])
        self.assertEqual(collector.group_metrics([idle])['sync_tx_bps'], 0)

    def test_combine_http_and_tcp_requires_both_sources(self):
        combine = getattr(collector, 'combine_transfer_rates', None)
        self.assertTrue(callable(combine), 'missing combined live transfer accounting')
        tcp = dict(sync_rx_bps=10, sync_tx_bps=20, rates_observed_at=self.now)
        http = dict(sync_rx_bps=3, sync_tx_bps=500, rates_observed_at=self.now+.1)
        self.assertEqual(combine(tcp, http), dict(sync_rx_bps=13, sync_tx_bps=520,
                                                rates_observed_at=self.now))
        for missing in (None, {}, dict(http, sync_rx_bps=None), dict(http, sync_tx_bps=float('inf'))):
            with self.subTest(missing=missing):
                self.assertIsNone(combine(tcp, missing)['sync_tx_bps'])
        self.assertIsNone(combine(tcp, dict(http, rates_observed_at=self.now+3))['sync_tx_bps'])
        self.assertIsNone(combine(dict(tcp, sync_tx_bps=1e308), dict(http, sync_tx_bps=1e308))['sync_tx_bps'])

    def test_completed_project_still_publishes_combined_download_rates(self):
        self.observer('archlinux')
        tcp = {rid: dict(sync_rx_bps=10, sync_tx_bps=20, rates_observed_at=self.now)
               for rid in self.config['repositories']}
        http = {rid: dict(sync_rx_bps=3, sync_tx_bps=500, rates_observed_at=self.now+.1)
                for rid in self.config['repositories']}
        with mock.patch('telemetry.rates.sample', return_value=(tcp, {})), \
             mock.patch('telemetry.http_rates.sample', return_value=(http, {})):
            snapshot = self.tick()
        project = snapshot['projects']['archlinux']
        self.assertEqual(project['state'], 'completed')
        self.assertEqual(project['sync_tx_bps'], 520)
        self.assertEqual(project['sync_rx_bps'], 13)
        self.assertEqual(project['rates_observed_at'], self.now)
        http['archlinux']['sync_rx_bps'] = None
        with mock.patch('telemetry.rates.sample', return_value=(tcp, {})), \
             mock.patch('telemetry.http_rates.sample', return_value=(http, {})):
            self.assertIsNone(self.tick()['projects']['archlinux']['sync_tx_bps'])

    def test_observer_result_and_partial_group(self):
        self.observer("gentoo")
        row = self.tick()["projects"]["gentoo"]
        self.assertEqual(row["state"], "unknown")
        self.assertIsNone(row["last_success_at"])
        self.assertIsNone(row["exit_code"])
        self.assertEqual(row["repositories"][0]["state"], "completed")
        self.observer("gentoo-portage", exit_code=5, last_success_at=self.now - 90)
        row = self.tick()["projects"]["gentoo"]
        self.assertEqual(row["state"], "failed")
        self.assertEqual(row["exit_code"], 5)
        self.assertEqual(row["last_success_at"], self.now - 90)
        self.assertEqual(row["last_attempt_at"], self.now - 30)
        self.assertEqual(row["last_finished_at"], self.now - 10)
        self.observer("gentoo-portage", started_at=self.now - 20, finished_at=self.now - 5,
                      last_success_at=self.now - 5)
        row = self.tick()["projects"]["gentoo"]
        self.assertEqual(row["state"], "completed")
        self.assertEqual(row["exit_code"], 0)
        self.assertEqual(row["last_success_at"], self.now - 10)
        self.assertEqual(row["last_attempt_at"], self.now - 20)
        self.assertEqual(row["last_finished_at"], self.now - 5)

    def test_corrupt_or_invalid_observer_never_fabricates_success(self):
        for change in ({"schema_version": 9}, {"repository": "private-name"},
                       {"exit_code": False}, {"exit_code": "0"}, {"exit_code": 999},
                       {"finished_at": float("nan")}, {"started_at": self.now + 90},
                       {"finished_at": self.now - 60}, {"last_success_at": "private-path"}):
            with self.subTest(change=change):
                self.observer("archlinux", **change)
                row = self.repo(self.tick(), "archlinux")
                self.assertEqual(row["state"], "unknown")
                self.assertIsNone(row["exit_code"])
                self.assertIsNone(row["last_success_at"])
        path = self.state / "runs" / "archlinux" / "state.json"
        path.write_text('{"phase":', encoding="utf-8")
        self.assertEqual(self.repo(self.tick(), "archlinux")["state"], "unknown")

    def test_verified_success_survives_later_corruption_or_failure(self):
        self.observer("archlinux")
        self.tick()
        (self.state / "runs" / "archlinux" / "state.json").write_text("broken")
        row = self.repo(self.tick(), "archlinux")
        self.assertEqual(row["state"], "unknown")
        self.assertEqual(row["last_success_at"], self.now - 10)
        self.observer("archlinux", exit_code=23, last_success_at=None, finished_at=self.now)
        row = self.repo(self.tick(), "archlinux")
        self.assertEqual(row["state"], "failed")
        self.assertEqual(row["last_success_at"], self.now - 10)

    def test_size_samples_keep_prior_time_and_require_complete_group(self):
        self.put_json(self.state / "sizes.json", {"schema_version": 1, "repositories": {
            "gentoo": {"bytes": 0, "measured_at": self.now - 100, "error": False},
            "gentoo-portage": {"bytes": 4096, "measured_at": self.now - 200, "error": True},
            "kali": {"bytes": 123, "measured_at": self.now, "error": False},
            "archlinux": {"bytes": -1, "measured_at": self.now, "error": False},
            "manjaro": {"bytes": True, "measured_at": self.now, "error": False},
            "proxmox": {"bytes": 2**53, "measured_at": self.now, "error": False},
        }})
        snapshot = self.tick()
        row = snapshot["projects"]["gentoo"]
        self.assertEqual(row["size_bytes"], 4096)
        self.assertEqual(row["size_measured_at"], self.now - 200)
        self.assertEqual(row["repositories"][0]["size_bytes"], 0)
        self.assertIsNone(snapshot["projects"]["kali"]["size_bytes"])
        for rid in ("archlinux", "manjaro", "proxmox"):
            self.assertIsNone(self.repo(snapshot, rid)["size_bytes"])
        self.assertEqual(row["state"], "unknown")

    def process(self, pid, args, ticks=500, process_state="S"):
        directory = self.proc / str(pid)
        directory.mkdir(exist_ok=True)
        fields = [process_state, "1"] + ["0"] * 17 + [str(ticks)] + ["0"] * 5
        (directory / "stat").write_text(str(pid) + " (fixture ) worker) " + " ".join(fields))
        (directory / "cmdline").write_bytes(b"\0".join(a.encode() for a in args) + b"\0")
        (self.proc / "stat").write_text("btime " + str(self.now - 100) + "\n")
        boot = self.proc / "sys" / "kernel" / "random" / "boot_id"
        boot.parent.mkdir(parents=True, exist_ok=True)
        boot.write_text("00000000-0000-4000-8000-000000000001\n")

    def test_live_observer_requires_same_boot_pid_and_start_ticks(self):
        self.observer("archlinux", phase="running", finished_at=None, exit_code=None,
                      last_success_at=self.now - 100)
        self.process(123, ["/opt/observer"], ticks=500)
        self.assertEqual(self.repo(self.tick(), "archlinux")["state"], "syncing")
        self.process(123, ["/opt/observer"], ticks=501)
        row = self.repo(self.tick(), "archlinux")
        self.assertEqual(row["state"], "unknown")
        self.assertEqual(row["last_success_at"], self.now - 100)
        self.process(123, ["/opt/observer"], ticks=500, process_state="Z")
        self.assertEqual(self.repo(self.tick(), "archlinux")["state"], "unknown")
        self.process(123, ["/opt/observer"], ticks=500)
        (self.proc / "sys/kernel/random/boot_id").write_text("different-boot")
        self.assertEqual(self.repo(self.tick(), "archlinux")["state"], "unknown")
        (self.proc / "123" / "stat").unlink()
        self.assertEqual(self.repo(self.tick(), "archlinux")["state"], "unknown")

    def test_matching_activity_overrides_old_result_then_dies_unknown(self):
        rid = "gentoo"
        job = self.config["repositories"][rid]
        self.observer(rid, started_at=self.now - 95, finished_at=self.now - 90,
                      last_success_at=self.now - 90)
        self.observer("gentoo-portage", exit_code=5)
        self.process(200, ["/bin/bash", job["script"]], ticks=70 * os.sysconf("SC_CLK_TCK"))
        snapshot = self.tick()
        row = self.repo(snapshot, rid)
        self.assertEqual(row["state"], "syncing")
        self.assertEqual(row["last_attempt_at"], self.now - 30)
        self.assertIsNone(row["exit_code"])
        self.assertEqual(snapshot["projects"][rid]["state"], "syncing")
        self.assertEqual(snapshot["projects"][rid]["exit_code"], 5)
        (self.proc / "200/stat").unlink()
        row = self.repo(self.tick(now=self.now + 15), rid)
        self.assertEqual(row["state"], "unknown")
        self.assertEqual(row["last_success_at"], self.now - 90)
        self.observer(rid, started_at=self.now - 30, finished_at=self.now + 10,
                      last_success_at=self.now + 10)
        self.assertEqual(self.repo(self.tick(now=self.now + 15), rid)["state"], "completed")

    def test_process_matching_excludes_daemons_and_argument_substrings(self):
        rid = "archlinux"
        job = self.config["repositories"][rid]
        cases = [
            (["/usr/bin/rsync", "--server", "--sender", ".", job["path"]], False),
            (["/usr/bin/rsync", "--daemon", job["path"]], False),
            (["/bin/echo", job["script"]], False),
            (["/bin/bash", "-c", job["script"]], False),
            (["/bin/bash", job["script"] + ".bak"], False),
            (["/usr/bin/rsync", "upstream::archive", job["path"] + "-other"], False),
            (["/usr/bin/rsync", "upstream::archive", "relative/archlinux"], False),
            (["/usr/bin/rsync", "upstream::archive", job["path"] + "/"], True),
            (["/usr/bin/rsync", "upstream::archive", job["path"] + "/pool"], True),
            (["/bin/bash", job["script"]], True),
        ]
        for args, active in cases:
            with self.subTest(args=args):
                self.process(200, args)
                row = self.repo(self.tick(), rid)
                self.assertEqual(row["state"], "syncing" if active else "unknown")
                self.assertIsNone(row["sync_rx_bps"])
                self.assertIsNone(row["sync_tx_bps"])
        lock = Path(job["lock"])
        lock.parent.mkdir(parents=True)
        lock.write_text("200")
        (self.proc / "200/stat").unlink()
        self.assertEqual(self.repo(self.tick(), rid)["state"], "unknown")

    def append(self, path, text):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as stream:
            stream.write(text)

    def http(self, project, count, ts, **extra):
        return json.dumps(dict(ts=ts, project=project, bytes=count, request_bytes=41, status=200, **extra)) + "\n"

    def xfer(self, project, count, op="send"):
        # Prefix timestamps were discarded by the source audit: observed-time only.
        return f"XFER|pid=17|op={op}|size=99999|wire={count}|module={project}|ip=REDACTED|file=OMITTED\n"

    def test_traffic_warms_up_then_counts_completed_delivery_not_old_rows(self):
        self.append(self.config["http_log"], self.http("gentoo", 9999, self.now - 1))
        self.append(self.config["rsync_log"], self.xfer("gentoo", 8888))
        first = self.tick()["projects"]["gentoo"]
        self.assertIsNone(first["http_bytes_60s"])
        self.assertIsNone(first["rsync_bytes_60s"])
        self.assertEqual(first["traffic_observed_since"], self.now)
        self.append(self.config["http_log"], self.http("gentoo", 23, self.now + 15)
                    + self.http("gentoo-portage", 7, self.now + 15)
                    + self.http("not-allowed", 900, self.now + 15))
        self.append(self.config["rsync_log"], self.xfer("gentoo", 11)
                    + self.xfer("gentoo-portage", 4) + self.xfer("gentoo", 800, "recv")
                    + self.xfer("not-allowed", 300))
        for elapsed in (15, 30, 45):
            row = self.tick(now=self.now + elapsed)["projects"]["gentoo"]
            self.assertIsNone(row["http_bytes_60s"])
            self.assertIsNone(row["rsync_bytes_60s"])
        snapshot = self.tick(now=self.now + 60)
        row = snapshot["projects"]["gentoo"]
        self.assertEqual(row["http_bytes_60s"], 30)
        self.assertEqual(row["rsync_bytes_60s"], 15)
        self.assertEqual(row["repositories"][0]["http_bytes_60s"], 23)
        self.assertEqual(snapshot["projects"]["archlinux"]["http_bytes_60s"], 0)
        row = self.tick(now=self.now + 75)["projects"]["gentoo"]
        self.assertEqual(row["http_bytes_60s"], 0)
        self.assertEqual(row["rsync_bytes_60s"], 0)
        private = (self.state / "collector.json").read_text()
        self.assertNotIn("REDACTED", private)
        self.assertNotIn("OMITTED", private)
        self.assertNotIn("not-allowed", private)

    def warm(self):
        for key in ("http_log", "rsync_log"):
            Path(self.config[key]).touch()
        for elapsed in (0, 15, 30, 45, 60):
            snapshot = self.tick(now=self.now + elapsed)
        return snapshot

    def test_partial_delivery_lines_count_once_and_initial_fragment_is_discarded(self):
        raw = Path(self.config["rsync_log"])
        raw.write_text("old prefix ")
        Path(self.config["http_log"]).touch()
        self.tick()
        self.append(raw, self.xfer("archlinux", 99) + self.xfer("archlinux", 8)[:-1])
        self.tick(now=self.now + 15)
        self.append(raw, "\n")
        self.tick(now=self.now + 30)
        self.tick(now=self.now + 45)
        row = self.tick(now=self.now + 60)["projects"]["archlinux"]
        self.assertEqual(row["rsync_bytes_60s"], 8)
        self.assertEqual(row["traffic_observed_since"], self.now)
        row = self.tick(now=self.now + 75)["projects"]["archlinux"]
        self.assertEqual(row["rsync_bytes_60s"], 8)
        row = self.tick(now=self.now + 90)["projects"]["archlinux"]
        self.assertEqual(row["rsync_bytes_60s"], 0)

    def test_rotation_truncation_and_regrowth_restart_coverage(self):
        self.warm()
        path = Path(self.config["rsync_log"])
        self.append(path, self.xfer("archlinux", 8))
        self.tick(now=self.now + 75)
        path.rename(path.with_suffix(".1"))
        path.write_text(self.xfer("archlinux", 99))
        row = self.tick(now=self.now + 90)["projects"]["archlinux"]
        self.assertIsNone(row["rsync_bytes_60s"])
        self.assertEqual(row["traffic_observed_since"], self.now + 90)
        for elapsed in (105, 120, 135, 150):
            snapshot = self.tick(now=self.now + elapsed)
        self.assertEqual(snapshot["projects"]["archlinux"]["rsync_bytes_60s"], 0)
        path.write_text("")
        row = self.tick(now=self.now + 165)["projects"]["archlinux"]
        self.assertIsNone(row["rsync_bytes_60s"])
        self.assertEqual(row["traffic_observed_since"], self.now + 165)
        self.append(path, self.xfer("archlinux", 11))
        self.tick(now=self.now + 180)
        # Same inode, regrown beyond the old offset between ticks.
        path.write_text(self.xfer("archlinux", 9999999) * 2)
        row = self.tick(now=self.now + 195)["projects"]["archlinux"]
        self.assertIsNone(row["rsync_bytes_60s"])
        self.assertEqual(row["traffic_observed_since"], self.now + 195)

    def test_poll_gap_and_read_budget_drop_backlog_not_replay_it_live(self):
        self.warm()
        self.append(self.config["rsync_log"], self.xfer("archlinux", 9))
        row = self.tick(now=self.now + 100)["projects"]["archlinux"]
        self.assertIsNone(row["rsync_bytes_60s"])
        self.assertEqual(row["traffic_observed_since"], self.now + 100)
        self.append(self.config["rsync_log"], self.xfer("archlinux", 9) * 30)
        row = self.tick(now=self.now + 115, max_read=256)["projects"]["archlinux"]
        self.assertIsNone(row["rsync_bytes_60s"])
        self.assertEqual(row["traffic_observed_since"], self.now + 115)
        for elapsed in (130, 145, 160, 175):
            snapshot = self.tick(now=self.now + elapsed, max_read=256)
        self.assertEqual(snapshot["projects"]["archlinux"]["rsync_bytes_60s"], 0)

    def test_bad_delivery_rows_and_counter_overflow_restart_not_publish_invalid(self):
        self.warm()
        self.append(self.config["http_log"], self.http("archlinux", 2**53 - 1, self.now + 75)
                    + self.http("archlinux", 1, self.now + 75))
        row = self.tick(now=self.now + 75)["projects"]["archlinux"]
        self.assertIsNone(row["http_bytes_60s"])
        self.assertEqual(row["traffic_observed_since"], self.now + 75)
        for elapsed, line in ((90, self.http("archlinux", -1, self.now + 90)),
                              (105, self.http("archlinux", True, self.now + 105)),
                              (120, self.http("archlinux", 1, float("nan"))),
                              (135, '{"project":"archlinux"}\n')):
            self.append(self.config["http_log"], line)
            row = self.tick(now=self.now + elapsed)["projects"]["archlinux"]
            self.assertIsNone(row["http_bytes_60s"])
            self.assertEqual(row["traffic_observed_since"], self.now + elapsed)
            self.assertEqual(row["rsync_bytes_60s"], 0)

    def test_corrupted_cursor_resets_coverage_without_exceptions(self):
        self.warm()
        private_path = self.state / "collector.json"
        state = json.loads(private_path.read_text())
        for change in ({"events": [[self.now, "private-repo", 1]]},
                       {"events": None}, {"offset": "secret"}, {"started": False},
                       {"checked": self.now + 999}, {"events": [[self.now, "archlinux", -9]]}):
            bad = json.loads(json.dumps(state))
            bad["delivery"]["rsync"].update(change)
            self.put_json(private_path, bad)
            row = self.tick(now=self.now + 75)["projects"]["archlinux"]
            self.assertIsNone(row["rsync_bytes_60s"])
            self.assertEqual(row["traffic_observed_since"], self.now + 75)

    def test_generic_bootstrap_explicit_failure_survives_unconditional_end(self):
        path = self.config["repositories"]["fyralabs"]["log"]
        self.append(path, "Started update at Wed Oct 28 03:46:01 PM KST 2026\n"
                    "123 progress\r456 progress\r"
                    "rsync error: transfer interrupted (code 5) at main.c(1) [Receiver=3.5.0]\n"
                    "End: Wed Oct 28 03:50:31 PM KST 2026\n")
        row = self.repo(self.tick(), "fyralabs")
        self.assertEqual(row["state"], "failed")
        self.assertEqual(row["exit_code"], 5)
        self.assertEqual(row["last_attempt_at"], 1793169961)
        self.assertEqual(row["last_finished_at"], 1793170231)
        self.assertIsNone(row["last_success_at"])
        self.append(path, "Started update at Wed Oct 28 03:56:01 PM KST 2026\n"
                    "End: Wed Oct 28 03:56:31 PM KST 2026\n")
        row = self.repo(self.tick(), "fyralabs")
        self.assertEqual(row["state"], "unknown")
        self.assertIsNone(row["exit_code"])
        self.assertIsNone(row["last_success_at"])
        # No observer result means a generic End is never a successful run.
        self.assertEqual(row["last_finished_at"], 1793170591)

    def test_old_failure_log_cannot_override_new_observer_or_active_job(self):
        rid = "fyralabs"
        job = self.config["repositories"][rid]
        self.append(job["log"], "Started update at Wed Oct 28 03:46:01 PM KST 2026\n"
                    "rsync error: interrupted (code 5) at main.c(1)\n"
                    "End: Wed Oct 28 03:50:31 PM KST 2026\n")
        self.observer(rid)
        self.assertEqual(self.repo(self.tick(), rid)["state"], "completed")
        self.append(job["log"], "Started update at Wed Oct 28 04:00:01 PM KST 2026\n")
        row = self.repo(self.tick(now=self.now + 15), rid)
        self.assertEqual(row["state"], "unknown")
        self.assertEqual(row["last_success_at"], self.now - 10)
        self.process(201, ["/bin/bash", job["script"]])
        row = self.repo(self.tick(now=self.now + 15), rid)
        self.assertEqual(row["state"], "syncing")
        self.assertIsNone(row["exit_code"])

    def kali_log(self, first_code=0, ending="Mirrorsync done"):
        return ("Oct 28 09:33:48 mirror ftpsync-kali[17]: Mirrorsync start\n"
                "Oct 28 09:33:48 mirror ftpsync-kali[17]: Running stage1: rsync OMITTED\n"
                f"Oct 28 09:34:13 mirror ftpsync-kali[17]: Back from rsync with returncode {first_code}\n"
                "Oct 28 09:34:13 mirror ftpsync-kali[17]: Running stage2: rsync OMITTED\n"
                "Oct 28 09:35:32 mirror ftpsync-kali[17]: Back from rsync with returncode 0\n"
                f"Oct 28 09:35:33 mirror ftpsync-kali[17]: {ending}\n")

    def test_kali_completion_requires_stages_and_reports_ftpsync_24_warning(self):
        job = self.config["repositories"]["kali"]
        path = Path(job["log"] + ".0")
        self.append(path, self.kali_log(24))
        os.utime(path, (self.now, self.now))
        row = self.repo(self.tick(), "kali")
        self.assertEqual(row["state"], "completed")
        self.assertEqual(row["exit_code"], 24)
        self.assertIs(row["warning"], True)
        self.assertEqual(row["last_success_at"], 1793147733)
        self.assertEqual(row["last_attempt_at"], 1793147628)
        path.write_text(self.kali_log(23, "Mirrorsync done with errors"))
        os.utime(path, (self.now, self.now))
        row = self.repo(self.tick(), "kali")
        self.assertEqual(row["state"], "failed")
        self.assertEqual(row["exit_code"], 23)
        self.assertEqual(row["last_success_at"], 1793147733)

    def test_kali_leap_day_failure_replaces_previous_completion(self):
        self.now = datetime.fromisoformat("2028-02-29T10:00:00+09:00").timestamp()
        previous_success = datetime.fromisoformat("2028-02-28T09:35:33+09:00").timestamp()
        attempt = datetime.fromisoformat("2028-02-29T09:33:48+09:00").timestamp()
        finished = datetime.fromisoformat("2028-02-29T09:35:33+09:00").timestamp()
        path = Path(self.config["repositories"]["kali"]["log"])
        self.append(path, self.kali_log().replace("Oct 28", "Feb 28"))
        os.utime(path, (previous_success, previous_success))
        previous = self.repo(self.tick(now=previous_success), "kali")
        self.assertEqual(previous["state"], "completed")
        self.assertEqual(previous["exit_code"], 0)
        self.assertEqual(previous["last_success_at"], previous_success)

        self.append(path, self.kali_log(23, "Mirrorsync done with errors")
                    .replace("Oct 28", "Feb 29"))
        os.utime(path, (self.now, self.now))
        expected = {"state": "failed", "exit_code": 23,
                    "last_attempt_at": attempt, "last_finished_at": finished,
                    "last_success_at": previous_success}
        for state in (self.state, self.root / "fresh-state"):
            with self.subTest(cached=state == self.state):
                snapshot = collector.collect(self.config, state, now=self.now, proc_root=self.proc)
                row = self.repo(snapshot, "kali")
                self.assertEqual({key: row[key] for key in expected}, expected)

    def test_kali_intermediate_observer_success_does_not_complete_whole_run(self):
        self.observer("kali")
        self.assertEqual(self.repo(self.tick(), "kali")["state"], "unknown")
        self.assertIsNone(self.repo(self.tick(), "kali")["last_success_at"])
        job = self.config["repositories"]["kali"]
        path = Path(job["log"] + ".0")
        self.append(path, self.kali_log())
        os.utime(path, (self.now, self.now))
        self.append(job["log"], "Oct 28 16:00:01 mirror ftpsync-kali[18]: Mirrorsync start\n")
        os.utime(job["log"], (self.now + 1, self.now + 1))
        row = self.repo(self.tick(now=self.now + 15), "kali")
        self.assertEqual(row["state"], "unknown")
        self.assertEqual(row["last_attempt_at"], self.now + 1)
        self.assertEqual(row["last_success_at"], 1793147733)
        self.process(18, ["/bin/bash", job["script"]])
        self.assertEqual(self.repo(self.tick(now=self.now + 15), "kali")["state"], "syncing")

    def test_opnsense_canonical_completion_does_not_hide_newer_tmp_failure(self):
        job = self.config["repositories"]["opnsense"]
        self.append(job["log"], "Wed 28 Oct 2026 02:58:01 PM KST Started update: src=OMITTED dst=OMITTED\n"
                    "Wed 28 Oct 2026 02:58:29 PM KST Finished update.\n")
        row = self.repo(self.tick(), "opnsense")
        self.assertEqual(row["state"], "completed")
        self.assertEqual(row["exit_code"], 0)
        self.assertEqual(row["last_success_at"], 1793167109)
        self.append(job["log"] + ".tmp.20261028155801.21",
                    "Wed 28 Oct 2026 03:58:01 PM KST Started update: src=OMITTED dst=OMITTED\n"
                    "rsync error: interrupted (code 23) at main.c(1)\n")
        row = self.repo(self.tick(), "opnsense")
        self.assertEqual(row["state"], "failed")
        self.assertEqual(row["exit_code"], 23)
        self.assertEqual(row["last_attempt_at"], 1793170681)
        self.assertEqual(row["last_success_at"], 1793167109)
        # An unpromoted temp file cannot be canonical success evidence.
        self.append(job["log"] + ".tmp.20261028155901.22",
                    "Wed 28 Oct 2026 03:59:01 PM KST Started update: src=OMITTED dst=OMITTED\n"
                    "Wed 28 Oct 2026 03:59:29 PM KST Finished update.\n")
        self.assertEqual(self.repo(self.tick(), "opnsense")["state"], "unknown")

    def test_cli_writes_small_atomic_snapshot_with_fixed_schema(self):
        config_path, output = self.root / "config.json", self.root / "public/snapshot.json"
        self.put_json(config_path, self.config)
        self.put_json(output, {"old": True})
        with output.open("rb") as old_reader:
            before = os.fstat(old_reader.fileno()).st_ino
            run = subprocess.run([sys.executable, str(COLLECTOR_PATH), "--config", str(config_path),
                                  "--state-dir", str(self.state), "--output", str(output)],
                                 capture_output=True, timeout=10)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertEqual(run.stderr, b"")
            self.assertNotEqual(before, output.stat().st_ino)
            self.assertEqual(json.load(old_reader), {"old": True})
        snapshot = json.loads(output.read_text())
        self.assertEqual(snapshot["schema_version"], 1)
        self.assertEqual(set(snapshot["projects"]), set(GROUPS))
        self.assertLess(output.stat().st_size, 20000)
        self.assertEqual(output.stat().st_mode & 0o777, 0o644)
        self.assertEqual((self.state / "collector.json").stat().st_mode & 0o777, 0o600)
        self.assertNotIn(str(self.root), output.read_text())
        self.assertEqual(list(output.parent.glob(".snapshot.json.*")), [])

    def test_cli_rejects_invalid_allowlists_without_replacing_public_snapshot(self):
        config_path, output = self.root / "config.json", self.root / "public.json"
        self.config["mirror_id"] = "secret-hostname"
        self.put_json(config_path, self.config)
        self.put_json(output, {"old": True})
        run = subprocess.run([sys.executable, str(COLLECTOR_PATH), "--config", str(config_path),
                              "--state-dir", str(self.state), "--output", str(output)],
                             capture_output=True, timeout=10)
        self.assertNotEqual(run.returncode, 0)
        self.assertEqual(json.loads(output.read_text()), {"old": True})
        self.assertNotIn(b"secret-hostname", run.stderr)
        self.assertNotIn(str(self.root).encode(), run.stderr)

    def test_oversized_json_is_rejected_before_parsing(self):
        path = self.root / "huge.json"
        self.put_json(path, {"payload": "x" * (3 * 1024 * 1024)})
        self.assertTrue(collector.read_json(path) is None, "oversized JSON was accepted")

    def test_fifo_sources_cannot_block_a_tick(self):
        config_path, output = self.root / "config.json", self.root / "snapshot.json"
        self.put_json(config_path, self.config)
        os.mkfifo(self.config["http_log"])
        bad_state = self.state / "runs/archlinux/state.json"
        bad_state.parent.mkdir(parents=True)
        os.mkfifo(bad_state)
        try:
            run = subprocess.run([sys.executable, str(COLLECTOR_PATH), "--config", str(config_path),
                                  "--state-dir", str(self.state), "--output", str(output)],
                                 capture_output=True, timeout=3)
        except subprocess.TimeoutExpired:
            self.fail("collector blocked on a FIFO instead of rejecting non-regular evidence")
        self.assertEqual(run.returncode, 0, run.stderr)
        row = json.loads(output.read_text())["projects"]["archlinux"]
        self.assertEqual(row["state"], "unknown")
        self.assertIsNone(row["http_bytes_60s"])

    def test_cli_deadline_keeps_previous_snapshot_on_slow_evidence(self):
        config_path, output = self.root / "config.json", self.root / "snapshot.json"
        self.put_json(config_path, self.config)
        self.put_json(output, {"old": True})
        def slow(*args, **kwargs):
            time.sleep(0.2)
            return {"should_not_publish": True}
        with mock.patch.object(collector, "collect", side_effect=slow), \
             mock.patch.object(collector, "TICK_TIMEOUT", 0.02, create=True), \
             mock.patch.object(sys, "stderr", io.StringIO()):
            status = collector.main(["--config", str(config_path), "--state-dir", str(self.state),
                                     "--output", str(output)])
        self.assertNotEqual(status, 0)
        self.assertEqual(json.loads(output.read_text()), {"old": True})

    def test_observer_metadata_and_numeric_ranges_are_strict(self):
        for change in ({"pid": True}, {"start_ticks": "500"}, {"boot_id": "not-uuid"},
                       {"schema_version": True}, {"exit_code": -9}, {"last_success_at": None},
                       {"started_at": 10**400}, {"exit_code": 5, "last_success_at": self.now - 10}):
            with self.subTest(change=change):
                self.observer("archlinux", **change)
                row = self.repo(self.tick(), "archlinux")
                self.assertEqual(row["state"], "unknown")
                self.assertIsNone(row["last_success_at"])

    def test_duplicate_json_keys_do_not_turn_an_exit_failure_into_success(self):
        self.observer("archlinux")
        path = self.state / "runs/archlinux/state.json"
        text = path.read_text().replace('"exit_code": 0', '"exit_code": 5, "exit_code": 0')
        path.write_text(text)
        row = self.repo(self.tick(), "archlinux")
        self.assertEqual(row["state"], "unknown")
        self.assertIsNone(row["last_success_at"])

    def test_live_observer_always_overrides_even_newer_failed_log(self):
        rid = "fyralabs"
        self.observer(rid, phase="running", finished_at=None, exit_code=None,
                      started_at=self.now - 120, last_success_at=self.now - 200)
        self.process(123, ["/opt/observer"])
        self.append(self.config["repositories"][rid]["log"],
                    "Started update at Wed Oct 28 03:59:01 PM KST 2026\n"
                    "rsync error: interrupted (code 23) at main.c(1)\n"
                    "End: Wed Oct 28 03:59:59 PM KST 2026\n")
        row = self.repo(self.tick(), rid)
        self.assertEqual(row["state"], "syncing")
        self.assertIsNone(row["exit_code"])
        self.assertEqual(row["last_success_at"], self.now - 200)

    def test_group_reports_known_failure_code_even_if_first_failed_code_unknown(self):
        path = Path(self.config["repositories"]["kali"]["log"])
        self.append(path, self.kali_log(0, "Mirrorsync done with errors"))
        os.utime(path, (self.now, self.now))
        self.observer("kali-images", exit_code=5)
        row = self.tick()["projects"]["kali"]
        self.assertEqual(row["state"], "failed")
        self.assertIsNone(row["repositories"][0]["exit_code"])
        self.assertEqual(row["exit_code"], 5)

    def test_explicit_failure_after_active_process_exits_is_not_lost_without_end(self):
        rid = "fyralabs"
        job = self.config["repositories"][rid]
        self.append(job["log"], "Started update at Wed Oct 28 03:59:30 PM KST 2026\n")
        self.process(201, ["/bin/bash", job["script"]], ticks=70 * os.sysconf("SC_CLK_TCK"))
        self.assertEqual(self.repo(self.tick(), rid)["state"], "syncing")
        (self.proc / "201/stat").unlink()
        self.append(job["log"], "rsync error: interrupted (code 23) at main.c(1)\n")
        row = self.repo(self.tick(now=self.now + 15), rid)
        self.assertEqual(row["state"], "failed")
        self.assertEqual(row["exit_code"], 23)
        self.assertIsNone(row["last_finished_at"])

    def test_private_extra_sources_cannot_enter_snapshot_or_crash_tick(self):
        self.warm()
        path = self.state / "collector.json"
        private = json.loads(path.read_text())
        private["delivery"]["unknown-source"] = "private-string"
        self.put_json(path, private)
        snapshot = self.tick(now=self.now + 75)
        self.assertEqual(snapshot["projects"]["archlinux"]["http_bytes_60s"], 0)
        self.assertNotIn("private-string", json.dumps(snapshot))
        self.assertNotIn("unknown-source", path.read_text())

    def test_busy_rsync_delivery_is_coalesced_without_losing_numeric_counts(self):
        Path(self.config["http_log"]).touch()
        Path(self.config["rsync_log"]).touch()
        self.tick()
        for elapsed in (15, 30, 45, 60):
            self.append(self.config["rsync_log"], self.xfer("archlinux", 1) * 2100)
            snapshot = self.tick(now=self.now + elapsed)
        self.assertEqual(snapshot["projects"]["archlinux"]["rsync_bytes_60s"], 8400)
        private = json.loads((self.state / "collector.json").read_text())
        self.assertLessEqual(len(private["delivery"]["rsync"]["events"]), 4)

    def test_corrupt_log_cache_cannot_promote_generic_end_to_success(self):
        rid = "fyralabs"
        self.append(self.config["repositories"][rid]["log"],
                    "Started update at Wed Oct 28 03:59:01 PM KST 2026\n"
                    "End: Wed Oct 28 03:59:59 PM KST 2026\n")
        self.tick()
        path = self.state / "collector.json"
        private = json.loads(path.read_text())
        cached = private["logs"][rid]["current"]["result"]
        cached.update(state="completed", exit_code=0, last_success_at=self.now - 1)
        self.put_json(path, private)
        row = self.repo(self.tick(), rid)
        self.assertEqual(row["state"], "unknown")
        self.assertIsNone(row["last_success_at"])

    def test_missing_sources_produce_exact_null_schema(self):
        snapshot = self.tick()
        self.assertEqual(set(snapshot), {"schema_version", "mirror_id", "collected_at", "interval_seconds", "projects"})
        self.assertEqual(snapshot["mirror_id"], "mirror1")
        self.assertEqual(snapshot["collected_at"], self.now)
        self.assertEqual(snapshot["schema_version"], 1)
        self.assertEqual(snapshot["interval_seconds"], 1)
        self.assertEqual(set(snapshot["projects"]), set(GROUPS))
        rows = []
        for project, ids in GROUPS.items():
            row = snapshot["projects"][project]
            self.assertEqual(set(row), METRICS | {"repositories"})
            self.assertEqual([r["id"] for r in row["repositories"]], ids)
            rows.append(row)
            rows.extend(row["repositories"])
        for row in rows:
            self.assertEqual(row["state"], "unknown")
            self.assertIs(row["warning"], False)
            for field in METRICS - {"state", "warning"}:
                self.assertIsNone(row[field], field)
        self.assertEqual(sum(len(p["repositories"]) for p in snapshot["projects"].values()), 14)
        self.assertNotIn(str(self.root), json.dumps(snapshot))


if __name__ == "__main__":
    unittest.main()
