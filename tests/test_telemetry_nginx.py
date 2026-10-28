"""Nginx snippets are additive, scoped, and never emit request identity."""
import importlib
import json
from pathlib import Path
import re
import shutil
import socket
import time
import urllib.request
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
OPS = ROOT / "ops/telemetry"
REPOSITORIES = {"archlinux", "gentoo", "gentoo-portage", "kali", "kali-images",
                "cachyos", "artix", "artix-iso", "artix-weekly-iso", "opnsense",
                "endeavouros", "fyralabs", "manjaro", "proxmox"}


class NginxSnippetTests(unittest.TestCase):
    def snippet(self, name):
        path = OPS / name
        self.assertTrue(path.is_file(), "missing nginx telemetry snippet")
        return path.read_text()

    def test_http_log_has_only_five_safe_fields_and_fixed_host_repository_allowlist(self):
        text = self.snippet("nginx-http.conf")
        self.assertIn('map "$server_name:$uri" $keimirror_project', text)
        self.assertRegex(text, r'default\s+"";')
        match = re.search(r'(?m)^\s*~(?P<regex>\S+)\s+\$(?P<capture>\w+);', text)
        if match is None:
            self.fail("missing named-capture allowlist map")
        capture = match["capture"]
        allowed = re.search(r'\(\?<' + capture + r'>([a-z|0-9-]+)\)', match["regex"])
        if allowed is None:
            self.fail("map must capture a fixed repository alternation")
        self.assertEqual(set(allowed[1].split("|")), REPOSITORIES)
        pattern = re.compile(match["regex"].replace(f"(?<{capture}>", f"(?P<{capture}>"))
        for host in ("mirror.keiminem.com", "mirror2.keiminem.com"):
            for repo in REPOSITORIES:
                for suffix in ("", "/", "/private-file.iso"):
                    with self.subTest(host=host, repo=repo, suffix=suffix):
                        matched = pattern.search(f"{host}:/{repo}{suffix}")
                        if matched is None:
                            self.fail("missing allowlisted repository")
                        self.assertEqual(matched[capture], repo)
            for uri in ("/", "/other/", "/archlinux-bad/", "/Archlinux/", "/.well-known/keimirror-status.json"):
                self.assertIsNone(pattern.search(f"{host}:{uri}"))
        for host in ("mirror.keiminem.com.evil", "evil.mirror.keiminem.com", "keiminem.com", "mirror3.keiminem.com"):
            self.assertIsNone(pattern.search(f"{host}:/archlinux/"))
        self.assertNotRegex(text, r'\$[1-9]\b')
        format_match = re.search(r'log_format\s+keimirror_telemetry\s+escape=json\s+(.+?);', text, re.S)
        if format_match is None:
            self.fail("missing JSON log format")
        template = ''.join(re.findall(r"'([^']*)'", format_match[1]))
        values = {"keimirror_project": "archlinux", "msec": "123.456", "body_bytes_sent": "42",
                  "request_length": "128", "status": "200"}
        self.assertEqual(set(re.findall(r'\$(\w+)', template)), set(values))
        record = json.loads(re.sub(r'\$(\w+)', lambda m: values[m[1]], template))
        self.assertEqual(record, {"project": "archlinux", "ts": 123.456, "bytes": 42,
                                  "request_bytes": 128, "status": 200})
        self.assertRegex(text, r'access_log /var/log/nginx/keimirror-telemetry\.log\s+'
                              r'keimirror_telemetry\s+buffer=64k\s+flush=1s\s+if=\$keimirror_project;')
        self.assertEqual(len(re.findall(r'(?m)^\s*access_log\b', text)), 1)
        self.assertNotRegex(text, r'(?m)^\s*(server|location)\s')
        for forbidden in ("$remote_addr", "$http_", "$request_uri", "$args", "$request_filename", "$host"):
            self.assertNotIn(forbidden, text)

    def test_exact_readonly_status_locations_keep_inherited_logs_and_security_headers(self):
        for name, endpoint, filename in (
                ("nginx-mirror-location.conf", "/.well-known/keimirror-status.json", "snapshot.json"),
                ("nginx-home-location.conf", "/status/mirrors.json", "mirrors.json")):
            with self.subTest(snippet=name):
                text = self.snippet(name)
                self.assertIn(f"location = {endpoint} {{", text)
                self.assertIn(f"alias /var/lib/keimirror-telemetry/public/{filename};", text)
                self.assertIn("default_type application/json;", text)
                self.assertRegex(text, r'types\s*\{\s*\}')
                self.assertRegex(text, r'limit_except GET\s*\{\s*deny all;\s*\}')
                for directive in ("expires -1;", "etag off;", "if_modified_since off;", "autoindex off;"):
                    self.assertIn(directive, text)
                for forbidden in ("add_header", "access_log", "proxy_pass", "listen", "$", "auth_basic off", "allow all"):
                    self.assertNotIn(forbidden, text)
                self.assertEqual(len(re.findall(r'\blocation\s', text)), 1)


    def test_second_log_is_http_scoped_and_keeps_existing_custom_log_verbatim(self):
        existing = '    access_log /var/log/nginx/access.log custom buffer=1024k flush=1s;\n'
        combined = 'http {\n' + existing + self.snippet("nginx-http.conf") + '\n}\n'
        self.assertEqual(combined.count(existing), 1)
        module = importlib.import_module("telemetry.adapt")
        logs = [d for d in module._nginx_directives(combined) if d.words[0] == "access_log"]
        self.assertEqual(len(logs), 2)
        self.assertEqual([d.scope for d in logs], [(0,), (0,)])

    def test_real_nginx_syntax_in_an_isolated_temporary_prefix_when_available(self):
        nginx = shutil.which("nginx")
        if nginx is None and Path("/usr/sbin/nginx").is_file():
            nginx = "/usr/sbin/nginx"
        if nginx is None:
            self.skipTest("nginx is not installed locally; parent must run the live nginx -t")
        with tempfile.TemporaryDirectory(prefix="telemetry-nginx-test-") as temporary:
            directory = Path(temporary)
            http = self.snippet("nginx-http.conf").replace('/var/log/nginx/keimirror-telemetry.log',
                                                         str(directory / 'telemetry.log'))
            mirror = self.snippet("nginx-mirror-location.conf").replace('/var/lib/keimirror-telemetry/public', temporary)
            home = self.snippet("nginx-home-location.conf").replace('/var/lib/keimirror-telemetry/public', temporary)
            for name, content in (("http.conf", http), ("mirror.conf", mirror), ("home.conf", home)):
                (directory / name).write_text(content)
            config = (f'pid {temporary}/nginx.pid;\nerror_log {temporary}/error.log;\n'
                      'events { worker_connections 16; }\nhttp {\n'
                      'log_format custom "$request";\n'
                      f'access_log {temporary}/existing.log custom buffer=1024k flush=1s;\n'
                      f'include {temporary}/http.conf;\n'
                      'server { listen 127.0.0.1:18080; server_name mirror.keiminem.com;\n'
                      f'root {temporary}; include {temporary}/mirror.conf; }}\n'
                      'server { listen 127.0.0.1:18081; server_name keiminem.com;\n'
                      f'root {temporary}; include {temporary}/home.conf; }}\n}}\n')
            (directory / "nginx.conf").write_text(config)
            result = subprocess.run([nginx, "-t", "-p", temporary + "/", "-c", str(directory / "nginx.conf")],
                                    capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


    def test_atomic_snapshot_replacement_bypasses_inherited_open_file_cache(self):
        nginx = shutil.which('nginx')
        if not nginx:
            self.skipTest('nginx unavailable')
        for name, endpoint, filename in (
                ('nginx-mirror-location.conf', '/.well-known/keimirror-status.json', 'snapshot.json'),
                ('nginx-home-location.conf', '/status/mirrors.json', 'mirrors.json')):
            with self.subTest(snippet=name), tempfile.TemporaryDirectory(prefix='telemetry-cache-') as temporary:
                directory = Path(temporary)
                with socket.socket() as listener:
                    listener.bind(('127.0.0.1', 0))
                    port = listener.getsockname()[1]
                snippet = self.snippet(name).replace('/var/lib/keimirror-telemetry/public', temporary)
                config = ('daemon off; master_process off;\n'
                          f'pid {temporary}/nginx.pid; error_log stderr crit;\n'
                          'events { worker_connections 16; }\nhttp { access_log off;\n'
                          'open_file_cache max=10 inactive=30s; open_file_cache_valid 30s;\n'
                          'open_file_cache_min_uses 1;\n'
                          f'server {{ listen 127.0.0.1:{port}; {snippet} }} }}\n')
                config_path = directory / 'nginx.conf'
                config_path.write_text(config)
                target = directory / filename
                target.write_text('{"generation":1}')
                process = subprocess.Popen([nginx, '-p', temporary+'/', '-c', str(config_path)],
                                           stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
                try:
                    url = f'http://127.0.0.1:{port}{endpoint}'
                    deadline = time.monotonic()+3
                    while True:
                        try:
                            with urllib.request.urlopen(url, timeout=.5) as response:
                                self.assertEqual(json.load(response)['generation'], 1)
                            break
                        except OSError:
                            if time.monotonic() >= deadline:
                                raise
                            time.sleep(.02)
                    replacement = directory / 'replacement.json'
                    replacement.write_text('{"generation":2}')
                    replacement.replace(target)
                    with urllib.request.urlopen(url, timeout=1) as response:
                        self.assertEqual(json.load(response)['generation'], 2)
                finally:
                    process.terminate()
                    try:
                        process.communicate(timeout=3)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.communicate(timeout=3)


class IncludeHelperTests(unittest.TestCase):
    def helper(self):
        module = importlib.import_module("telemetry.adapt")
        self.assertTrue(callable(getattr(module, "add_location_include", None)), "missing safe vhost include helper")
        return module.add_location_include

    def test_insert_only_after_single_server_root_line_preserves_all_existing_rules(self):
        source = ('# private comments remain private\nserver\n{\n'
                  '    server_name mirror.keiminem.com;\n'
                  '    root /srv/mirrors; # original root\n'
                  '    if ($blocked_ip) { return 403; }\n'
                  '    if ($blocked_asn) { return 403; }\n'
                  '    if ($crawler ~ "bot{1,3}") { return 403; }\n'
                  '    location / { try_files $uri $uri/ =404; }\n}\n')
        path = "/etc/nginx/snippets/keimirror-mirror-location.conf"
        expected = source.replace('    root /srv/mirrors; # original root\n',
                                  '    root /srv/mirrors; # original root\n' + f'    include {path};\n')
        result = self.helper()(source, "mirror.keiminem.com", "/srv/mirrors", path)
        self.assertEqual(result, expected)
        self.assertEqual(self.helper()(result, "mirror.keiminem.com", "/srv/mirrors", path), result)
        self.assertEqual(self.helper()(source.encode(), "mirror.keiminem.com", "/srv/mirrors", path), expected.encode())
        for newline in ("\r\n",):
            self.assertEqual(self.helper()(source.replace("\n", newline), "mirror.keiminem.com", "/srv/mirrors", path),
                             expected.replace("\n", newline))

    def test_helper_refuses_unverified_or_ambiguous_scope_even_on_rerun(self):
        include = "/etc/nginx/snippets/keimirror-mirror-location.conf"
        base = 'server {\n    server_name mirror.keiminem.com;\n    root /srv/mirrors;\n}\n'
        insertion = f'    include {include};\n'
        invalid = (
            base + base,
            'http {\n' + base + '}\n',
            base.replace('mirror.keiminem.com', 'other.example'),
            base.replace('mirror.keiminem.com;', 'mirror.keiminem.com other.example;'),
            base.replace('root /srv/mirrors;', 'root /srv/elsewhere;'),
            base.replace('    root /srv/mirrors;\n', '    location / {\n    root /srv/mirrors;\n    }\n'),
            base.replace('    root /srv/mirrors;\n', '    root /srv/mirrors;\n' * 2),
            base.replace('root /srv/mirrors;', 'root /srv/mirrors; autoindex on;'),
            base.replace('}\n', '    location / {\n' + insertion + '    }\n}\n'),
            base.replace('}\n', insertion * 2 + '}\n'),
            base.replace('mirror.keiminem.com', 'other.example').replace('}\n', insertion + '}\n'),
            base[:-2],
            base + '}',
            base.replace('    root /srv/mirrors;\n', '    set $fake "\n    root /srv/mirrors;\n";\n'),
            base.replace('}\n', '    set $bad "unterminated;\n}\n'),
            base.replace('}\n', '    location = /.well-known/keimirror-status.json { return 200; }\n}\n'),
            base.replace('}\n', insertion + '    location = /.well-known/keimirror-status.json { return 200; }\n}\n'),
        )
        for source in invalid:
            with self.subTest(source=source), self.assertRaises(ValueError):
                self.helper()(source, "mirror.keiminem.com", "/srv/mirrors", include)
        for path in ('relative.conf', '/etc/nginx/*', '/etc/nginx/a; deny all;', '/etc/nginx/../a.conf',
                     '/etc/nginx/$uri.conf', '/etc/nginx/a\n.conf'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.helper()(base, "mirror.keiminem.com", "/srv/mirrors", path)


if __name__ == "__main__":
    unittest.main()
