"""Retirement integration tests: disposable filesystem, stub system boundary only."""
import hashlib
import contextlib
import io
import importlib.util
import json
import os
from pathlib import Path
import subprocess
from unittest.mock import patch
import stat
import tempfile
import unittest


MODULE = Path(__file__).resolve().parents[1] / "ops/retirement/retire_runtime.py"
PREFIX = "/opt/keimirror-telemetry/bin/"
SITE = "/etc/nginx/sites-available/theme"
INCLUDE = b"    include /etc/nginx/snippets/keimirror-telemetry-location.conf;\n"
SCRIPTS = [f"/home/me0kmin27/script/{n}.sh" for n in (
    "arch-ssl", "artix", "artix-iso", "artix-weekly-iso", "cachyos", "endeavouros",
    "fyralabs", "gentoo", "gentoo-portage", "manjaro", "opnsense", "proxmox")]
SCRIPTS += ["/home/archvsync/bin/mirror-kali-images"]
FRAGMENTS = ["/etc/nginx/conf.d/keimirror-live.conf", "/etc/nginx/conf.d/keimirror-telemetry.conf",
             "/etc/nginx/modules-enabled/90-keimirror-live.conf",
             "/etc/nginx/snippets/keimirror-telemetry-location.conf",
             "/usr/lib/systemd/system/nginx.service.d/keimirror-live.conf"]
UNITS = [f"keimirror-status-{name}.{kind}" for name in ("collector", "sizes")
         for kind in ("service", "timer")] + ["keimirror-rpc-helper.service"]


class FakeSystem:
    def __init__(self):
        self.events = []
        self.states = {u: {"active": "active" if u.endswith("timer") or "rpc" in u else "inactive",
                           "enabled": "enabled" if u.endswith("timer") or "rpc" in u else "static"}
                       for u in UNITS}
        self.pid = 1234
        self.observers = 1
        self.failures = []

    def identity(self):
        return {"hostname": "mirror", "boot_id": "fixture-boot"}

    def owner(self):
        return os.getuid()

    def unit_state(self, unit):
        return dict(self.states[unit])

    def action(self, verb, unit=None):
        self.events.append((verb, unit))
        if (verb, unit) in self.failures:
            self.failures.remove((verb, unit))
            raise RuntimeError("injected boundary failure: confidential stderr")
        if verb == "stop":
            self.states[unit]["active"] = "inactive"
        elif verb == "start":
            self.states[unit]["active"] = "active"
        elif verb in ("enable", "disable"):
            self.states[unit]["enabled"] = "enabled" if verb == "enable" else "disabled"

    def nginx_pid(self):
        return self.pid

    def rsync_pid(self):
        return 4567

    def observer_count(self):
        return self.observers

    def check_shell(self, content):
        self.events.append(("shell-check", None))


class RetirementTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.system = FakeSystem()
        self.originals = {}
        self.changes = []
        self.native_scripts = {}
        for path in SCRIPTS:
            if path.endswith("arch-ssl.sh"):
                native = b'cmd=(rsync-ssl --type=openssl)\ncmd=(rsync)\n'
                wrapped = native.replace(b"cmd=(", b"cmd=(" + PREFIX.encode())
            elif path.endswith("opnsense.sh"):
                native = b'RSYNC_BIN="$(command -v rsync || true)"\n[[ -z "${RSYNC_BIN}" ]] && RSYNC_BIN="rsync"\n'
                wrapped = native.replace(b'$(command -v rsync || true)', (PREFIX + "rsync").encode())
            elif path.endswith("mirror-kali-images"):
                native = b'\trsync $RSYNC_OPTIONS $RSYNC_URL $TO >"$LOG_FILE" 2>"$ERROR_FILE"\n'
                wrapped = native.replace(b'\trsync ', b'\t' + PREFIX.encode() + b'rsync ')
            else:
                native = b'RSYNC="/usr/bin/rsync-ssl"\n'
                wrapped = native.replace(b'/usr/bin/', PREFIX.encode())
            prefix = b'#!/bin/bash\n# preserve bytes \xff\n'
            suffix = b'# keep argv, upstream, locks, and whitespace exactly\n'
            self.native_scripts[path] = prefix + native + suffix
            self.add(path, prefix + wrapped + suffix, "script", 0o750)
        for binary in ("rsync", "rsync-ssl"):
            self.add(PREFIX + binary, b"#!/bin/sh\nexec python3 /opt/keimirror-telemetry/observe.py\n", "native-link", 0o755)
        self.add(SITE, b"server {\n    root /srv/mirrors;\n" + INCLUDE +
                 b"    include /etc/nginx/conf.d/ipban.conf;\n    location / { autoindex on; }\n}\n", "include")
        for path in FRAGMENTS:
            self.add(path, b"# dedicated telemetry fixture\n", "remove")
        self.protected = []
        for path in ("/usr/bin/rsync", "/usr/bin/rsync-ssl", "/usr/sbin/nginx",
                     "/home/archvsync/bin/ftpsync", "/etc/ssh/ssh_host_ed25519_key",
                     "/home/me0kmin27/.ssh/authorized_keys", "/etc/crontab",
                     "/var/spool/cron/crontabs/me0kmin27", "/etc/nginx/nginx.conf",
                     "/etc/rsyncd.conf", "/opt/keimirror-telemetry/observe.py",
                     "/usr/share/keimirror-live/http/project-live.conf"):
            self.put(path, b"protected fixture\n", 0o755 if path.startswith("/usr/bin/") else 0o600)
            self.protected.append(self.record(path))
        link = "/etc/nginx/sites-enabled/theme"
        self.local(link).parent.mkdir(parents=True, exist_ok=True)
        self.local(link).symlink_to("../sites-available/theme")
        self.protected.append(self.record(link))
        self.local("/var/backups/keimirror-retirement").mkdir(parents=True, mode=0o700)
        self.plan = {"version": 1, "role": "mirror1", **self.system.identity(),
                     "backup_dir": "/var/backups/keimirror-retirement/fixture",
                     "changes": self.changes, "protected": self.protected,
                     "units": dict(self.system.states)}
        self.plan_path = self.root / "plan.json"
        self.save_plan()

    def local(self, path):
        return self.root / path.lstrip("/")

    def put(self, path, content, mode=0o644):
        p = self.local(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content)
        p.chmod(mode)
        self.originals[path] = content

    def record(self, path):
        p = self.local(path)
        s = p.lstat()
        data = os.fsencode(os.readlink(p)) if p.is_symlink() else p.read_bytes()
        out = {"path": path, "kind": "symlink" if p.is_symlink() else "file",
               "sha256": hashlib.sha256(data).hexdigest(), "uid": s.st_uid,
               "gid": s.st_gid, "mode": stat.S_IMODE(s.st_mode)}
        if p.is_symlink():
            out["target"] = os.readlink(p)
        return out

    def add(self, path, content, operation, mode=0o644):
        self.put(path, content, mode)
        self.changes.append({**self.record(path), "operation": operation})

    def save_plan(self):
        self.plan_path.write_text(json.dumps(self.plan))
        self.plan_path.chmod(0o600)

    def module(self):
        if hasattr(self, "_module"):
            return self._module
        self.assertTrue(MODULE.exists(), "retirement orchestrator is not implemented")
        spec = importlib.util.spec_from_file_location("retire_runtime", MODULE)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        self._module = mod
        return mod

    def run_mode(self, mode):
        return self.module().run(self.plan_path, mode, system=self.system, root=self.root)

    def test_unsupported_planned_unit_states_refuse_before_backup(self):
        cases = (("keimirror-status-collector.timer", {"active": "active", "enabled": "enabled-runtime"}),
                 ("keimirror-status-collector.service", {"active": "activating", "enabled": "static"}))
        for unit, state in cases:
            for mode in ("check", "apply"):
                with self.subTest(unit=unit, state=state, mode=mode):
                    self.setUp()
                    self.system.states[unit] = dict(state)
                    self.plan["units"][unit] = dict(state)
                    self.save_plan()
                    with self.assertRaisesRegex(self.module().Refusal, "unsupported unit state"):
                        self.run_mode(mode)
                    self.assertFalse(self.local(self.plan["backup_dir"]).exists())
                    self.assertEqual(self.system.events, [])
                    for record in self.changes + self.protected:
                        self.assertEqual(self.record(record["path"]),
                                         {k: v for k, v in record.items() if k != "operation"})

    def test_malformed_planned_unit_states_refuse_before_backup(self):
        for unit in UNITS:
            for state in (None, [], {"active": "inactive"},
                          {"active": "inactive", "enabled": "static", "extra": True},
                          {"active": [], "enabled": "static"}):
                with self.subTest(unit=unit, state=state):
                    original = self.plan["units"][unit]
                    self.plan["units"][unit] = state
                    self.save_plan()
                    try:
                        with self.assertRaisesRegex(self.module().Refusal, "unsupported unit state"):
                            self.run_mode("apply")
                        self.assertFalse(self.local(self.plan["backup_dir"]).exists())
                        self.assertEqual(self.system.events, [])
                    finally:
                        self.plan["units"][unit] = original

    def test_unit_state_drift_during_preparation_refuses_before_backup(self):
        for state in ({"active": "activating", "enabled": "static"},
                      {"active": "inactive", "enabled": "enabled-runtime"}):
            with self.subTest(state=state):
                self.setUp()
                original = self.system.check_shell
                def change_state(content):
                    original(content)
                    self.system.states[UNITS[0]] = dict(state)
                self.system.check_shell = change_state
                with self.assertRaisesRegex(self.module().Refusal, "unit state drift"):
                    self.run_mode("apply")
                self.assertFalse(self.local(self.plan["backup_dir"]).exists())
                self.assertTrue(all(event[0] == "shell-check" for event in self.system.events))
                for path, data in self.originals.items():
                    self.assertEqual(self.local(path).read_bytes(), data)

    def test_verify_refuses_unsupported_unit_enablement(self):
        self.run_mode("apply")
        self.system.states["keimirror-status-collector.timer"]["enabled"] = "enabled-runtime"
        self.system.events.clear()
        with self.assertRaisesRegex(self.module().Refusal, "telemetry unit"):
            self.run_mode("verify")
        self.assertEqual(self.system.events, [])

    def test_rollback_requires_unit_readback_not_only_command_success(self):
        self.run_mode("apply")
        original = self.system.action
        def no_start(verb, unit=None):
            if verb == "start":
                self.system.events.append((verb, unit))
            else:
                original(verb, unit)
        self.system.action = no_start
        with self.assertRaisesRegex(self.module().Refusal, "recovery incomplete"):
            self.run_mode("rollback")
        self.assertFalse((self.local(self.plan["backup_dir"]) / "receipt.json").exists())

    def test_rollback_refuses_concurrent_edit_without_overwriting_it(self):
        self.run_mode("apply")
        self.local(SCRIPTS[0]).write_bytes(b"operator changed this script\n")
        self.system.events.clear()
        with self.assertRaisesRegex(self.module().Refusal, "drift"):
            self.run_mode("rollback")
        self.assertEqual(self.local(SCRIPTS[0]).read_bytes(), b"operator changed this script\n")
        self.assertFalse(self.local(FRAGMENTS[-1]).exists())
        self.assertEqual(self.system.events, [])

    def test_cli_defaults_to_check_and_suppresses_error_contents(self):
        mod = self.module()
        self.assertTrue(hasattr(mod, "main"), "CLI entrypoint missing")
        output, errors = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            rc = mod.main(["--plan", str(self.plan_path)], system=self.system, root=self.root)
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(output.getvalue())["status"], "ready")
        self.assertFalse(self.local(self.plan["backup_dir"]).exists())
        self.plan_path.write_text("private malformed JSON contents")
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            rc = mod.main(["--plan", str(self.plan_path)], system=self.system, root=self.root)
        self.assertEqual(rc, 1)
        self.assertNotIn("private malformed", errors.getvalue())

    def test_local_system_boundary_has_only_guarded_service_commands(self):
        mod = self.module()
        self.assertTrue(hasattr(mod, "System"), "local system boundary missing")
        calls = []

        def command(argv, **kwargs):
            calls.append((argv, kwargs))
            output = b"ActiveState=active\nUnitFileState=enabled\nMainPID=1234\n"
            return subprocess.CompletedProcess(argv, 0, stdout=output)

        proc = self.root / "proc"
        (proc / "sys/kernel/random").mkdir(parents=True)
        (proc / "sys/kernel/random/boot_id").write_text("fixture-boot\n")
        for pid, args in (("11", b"python3\0-B\0/opt/keimirror-telemetry/observe.py\0"),
                          ("12", b"/usr/bin/rsync\0--server\0")):
            (proc / pid).mkdir()
            (proc / pid / "cmdline").write_bytes(args)
        system = mod.System(command=command, proc=proc)
        self.assertEqual(system.identity()["boot_id"], "fixture-boot")
        self.assertEqual(system.observer_count(), 1)
        self.assertEqual(system.unit_state(UNITS[0]), {"active": "active", "enabled": "enabled"})
        self.assertEqual(system.nginx_pid(), 1234)
        self.assertEqual(system.rsync_pid(), 1234)
        for verb, unit in (("stop", UNITS[0]), ("disable", UNITS[1]), ("enable", UNITS[1]),
                           ("start", UNITS[1]), ("daemon-reload", None), ("nginx-test", None),
                           ("nginx-reload", None)):
            system.action(verb, unit)
        system.check_shell(b"#!/bin/bash\ntrue\n")
        self.assertIn(["/usr/sbin/nginx", "-t"], [a for a,k in calls])
        self.assertIn(["/usr/bin/systemctl", "reload", "nginx.service"], [a for a,k in calls])
        self.assertIn(["/bin/bash", "-n"], [a for a,k in calls])
        for argv, kwargs in calls:
            self.assertFalse(kwargs.get("shell", False))
            self.assertLessEqual(kwargs["timeout"], 60)
            self.assertNotIn("restart", argv)
        for verb, unit in (("restart", "nginx.service"), ("stop", "rsync.service"),
                           ("stop", "keimirror-agent.service")):
            with self.assertRaises(mod.Refusal): system.action(verb, unit)
        with patch.object(mod.os, "geteuid", return_value=1000):
            with self.assertRaises(mod.Refusal): system.owner()

    def test_journal_is_bound_to_private_plan_before_rollback(self):
        self.run_mode("apply")
        journal_path = self.local(self.plan["backup_dir"]) / "journal.json"
        journal = json.loads(journal_path.read_text())
        journal["plan_sha256"] = "0" * 64
        journal_path.write_text(json.dumps(journal))
        self.system.events.clear()
        with self.assertRaisesRegex(self.module().Refusal, "journal"):
            self.run_mode("rollback")
        self.assertEqual(self.system.events, [])
        self.assertEqual(self.local(SCRIPTS[0]).read_bytes(), self.native_scripts[SCRIPTS[0]])

    def test_homepage_only_retires_actual_aggregate_units(self):
        site = "/etc/nginx/sites-available/keiverse"
        self.put(site, self.originals[SITE])
        alias = "/etc/nginx/sites-enabled/keiverse"
        self.local(alias).symlink_to("../sites-available/keiverse")
        self.plan.update(role="homepage", hostname="keiminem")
        self.system.identity = lambda: {"hostname": "keiminem", "boot_id": "fixture-boot"}
        self.plan["changes"] = [{**self.record(site), "operation": "include"},
                                next(r for r in self.changes if r["path"] == FRAGMENTS[3])]
        self.plan["protected"] = [r for r in self.protected if r["path"] != "/etc/nginx/sites-enabled/theme"]
        self.plan["protected"].append(self.record(alias))
        self.system.states = {"keimirror-status-aggregate.service": {"active": "inactive", "enabled": "static"},
                              "keimirror-status-aggregate.timer": {"active": "active", "enabled": "enabled"}}
        self.plan["units"] = json.loads(json.dumps(self.system.states))
        self.save_plan()
        self.assertEqual(self.run_mode("apply")["status"], "retired")
        self.assertEqual(self.local(site).read_bytes(), self.originals[SITE].replace(INCLUDE, b""))
        for path in SCRIPTS + [PREFIX + "rsync", SITE]:
            self.assertEqual(self.local(path).read_bytes(), self.originals[path])
        self.assertNotIn(("stop", "keimirror-rpc-helper.service"), self.system.events)

    def test_check_mode_returns_preview_without_writing(self):
        result = self.run_mode("check")
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["changed_files"], len(self.changes))
        self.assertFalse(self.local(self.plan["backup_dir"]).exists())
        self.assertFalse(any(e[0] != "shell-check" for e in self.system.events))
        for path, content in self.originals.items():
            self.assertEqual(self.local(path).read_bytes(), content)

    def test_readback_refuses_native_rsync_daemon_pid_change(self):
        self.run_mode("apply")
        self.system.rsync_pid = lambda: 9876
        with self.assertRaisesRegex(self.module().Refusal, "rsync"):
            self.run_mode("verify")

    def test_pre_reload_failure_recovers_files_and_units_independently(self):
        self.system.states[UNITS[0]]["active"] = "failed"
        self.plan["units"][UNITS[0]]["active"] = "failed"
        self.save_plan()
        self.system.failures = [("nginx-test", None), ("start", "keimirror-rpc-helper.service")]
        with self.assertRaises(self.module().Refusal) as error:
            self.run_mode("apply")
        self.assertIn("recovery incomplete", str(error.exception))
        self.assertNotIn("confidential", str(error.exception))
        for rec in self.changes + self.protected:
            self.assertEqual(self.record(rec["path"]), {k:v for k,v in rec.items() if k != "operation"})
        self.assertNotIn(("nginx-reload", None), self.system.events)
        for unit in UNITS:
            if unit.endswith("timer"):
                self.assertIn(("start", unit), self.system.events)
                self.assertEqual(self.system.states[unit], {"active": "active", "enabled": "enabled"})
        self.assertNotIn(("start", UNITS[0]), self.system.events)
        self.assertNotIn(("start", "keimirror-status-sizes.service"), self.system.events)
        self.assertFalse((self.local(self.plan["backup_dir"]) / "receipt.json").exists())

    def test_rollback_validates_every_backup_before_any_restore(self):
        self.run_mode("apply")
        backup = self.local(self.plan["backup_dir"])
        (backup / "0").write_bytes(b"damaged backup")
        self.system.events.clear()
        retired_script = self.local(SCRIPTS[0]).read_bytes()
        with self.assertRaisesRegex(self.module().Refusal, "drift"):
            self.run_mode("rollback")
        self.assertEqual(self.local(SCRIPTS[0]).read_bytes(), retired_script)
        self.assertFalse(self.local(FRAGMENTS[-1]).exists())
        self.assertEqual(self.system.events, [])

    def test_rollback_restores_backups_and_only_previously_active_units(self):
        expected_states = json.loads(json.dumps(self.plan["units"]))
        self.run_mode("apply")
        self.system.events.clear()
        result = self.run_mode("rollback")
        self.assertEqual(result["status"], "rolled-back")
        for rec in self.changes + self.protected:
            self.assertEqual(self.record(rec["path"]), {k:v for k,v in rec.items() if k != "operation"})
        self.assertEqual(self.system.states, expected_states)
        started = {u for verb,u in self.system.events if verb == "start"}
        self.assertEqual(started, {u for u,s in expected_states.items() if s["active"] == "active"})
        self.assertFalse((self.local(self.plan["backup_dir"]) / "receipt.json").exists())

    def test_unsafe_preflight_inputs_refuse_without_mutations(self):
        cases = ("host", "boot", "role", "plan-mode", "backup-exists", "backup-webroot",
                 "unknown-operation", "duplicate-path", "missing-native", "missing-ssh",
                 "unit-drift", "legacy-unit", "symlink-parent", "hardlink",
                 "wrong-include", "missing-wrapper", "metadata-drift")
        for case in cases:
            with self.subTest(case=case):
                self.setUp()
                if case == "host": self.plan["hostname"] = "other-host"
                elif case == "boot": self.plan["boot_id"] = "other-boot"
                elif case == "role": self.plan["role"] = "unknown"
                elif case == "backup-exists": self.local(self.plan["backup_dir"]).mkdir()
                elif case == "backup-webroot": self.plan["backup_dir"] = "/srv/mirrors/backup"
                elif case == "unknown-operation": self.plan["changes"][0]["operation"] = "execute"
                elif case == "duplicate-path": self.plan["changes"].append(dict(self.plan["changes"][0]))
                elif case == "missing-native": self.plan["protected"] = [r for r in self.protected if r["path"] != "/usr/bin/rsync"]
                elif case == "missing-ssh": self.plan["protected"] = [r for r in self.protected if "/.ssh/" not in r["path"] and not r["path"].startswith("/etc/ssh/")]
                elif case == "unit-drift": self.system.states[UNITS[0]] = {"active": "active", "enabled": "static"}
                elif case == "legacy-unit": self.plan["units"]["keimirror-agent.service"] = {"active": "inactive", "enabled": "disabled"}
                elif case == "symlink-parent":
                    parent = self.local(SCRIPTS[0]).parent
                    parent.rename(parent.with_name("elsewhere"))
                    parent.symlink_to("elsewhere")
                elif case == "hardlink": os.link(self.local(SCRIPTS[0]), self.root / "hardlink")
                elif case == "fifo":
                    self.local(SCRIPTS[0]).unlink()
                    os.mkfifo(self.local(SCRIPTS[0]))
                elif case == "wrong-include":
                    self.local(SITE).write_bytes(self.originals[SITE].replace(INCLUDE, b""))
                    for r in self.changes:
                        if r["path"] == SITE: r.update(self.record(SITE))
                elif case == "missing-wrapper":
                    self.local(SCRIPTS[0]).write_bytes(b"#!/bin/bash\n/usr/bin/rsync --help\n")
                    self.changes[0].update(self.record(SCRIPTS[0]))
                elif case == "metadata-drift": self.local(SCRIPTS[0]).chmod(0o777)
                self.save_plan()
                if case == "plan-mode": self.plan_path.chmod(0o644)
                with self.assertRaises(self.module().Refusal):
                    self.run_mode("apply")
                self.assertFalse(any(e[0] != "shell-check" for e in self.system.events))
                self.assertFalse((self.local(self.plan["backup_dir"]) / "journal.json").exists())

    def test_drift_refuses_before_backups_or_system_mutation(self):
        self.plan["changes"][0]["sha256"] = "0" * 64
        self.save_plan()
        with self.assertRaisesRegex(self.module().Refusal, "drift"):
            self.run_mode("apply")
        self.assertEqual(self.system.events, [])
        self.assertFalse(self.local(self.plan["backup_dir"]).exists())
        for path, content in self.originals.items():
            self.assertEqual(self.local(path).read_bytes(), content)

    def test_apply_preserves_mirror_bytes_and_drains_existing_observer(self):
        result = self.run_mode("apply")
        for path in SCRIPTS:
            self.assertEqual(self.local(path).read_bytes(), self.native_scripts[path])
            self.assertEqual(stat.S_IMODE(self.local(path).stat().st_mode), 0o750)
        self.assertEqual(self.local(SITE).read_bytes(), self.originals[SITE].replace(INCLUDE, b""))
        for name in ("rsync", "rsync-ssl"):
            self.assertEqual(os.readlink(self.local(PREFIX + name)), "/usr/bin/" + name)
        for path in FRAGMENTS:
            self.assertFalse(self.local(path).exists())
        for rec in self.protected:
            self.assertEqual(self.record(rec["path"]), rec)
        self.assertEqual(result["status"], "retired")
        self.assertEqual(result["code_cleanup"], "pending-observer-drain")
        self.assertEqual(result["observer_count"], 1)
        self.assertEqual(result["nginx_master_pid"], 1234)
        events = self.system.events
        self.assertLess(max(i for i,e in enumerate(events) if e[0]=="stop" and e[1].endswith("timer")),
                        min(i for i,e in enumerate(events) if e[0]=="stop" and e[1].endswith("service")))
        self.assertLess(events.index(("nginx-test", None)), events.index(("nginx-reload", None)))
        self.assertEqual([e for e in events if e[0] in ("kill", "restart", "sync")], [])
        backup = self.local(self.plan["backup_dir"])
        self.assertEqual(stat.S_IMODE(backup.stat().st_mode), 0o700)
        self.assertTrue((backup / "receipt.json").exists())
        self.assertEqual(self.run_mode("verify")["status"], "retired")


if __name__ == "__main__":
    unittest.main()
