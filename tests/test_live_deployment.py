"""Small deployment contract checks; never build/install/reload nginx."""
import ast
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
OPS = ROOT / 'ops/telemetry'
MODULE = OPS / 'nginx-live'
FILES = {
    'load': 'usr/share/keimirror-live/load/project-live.conf',
    'http': 'usr/share/keimirror-live/http/project-live.conf',
    'server': 'usr/share/keimirror-live/server/project-live.conf',
    'runtime': 'usr/lib/systemd/system/nginx.service.d/keimirror-live.conf',
}


class LiveDeployment(unittest.TestCase):
    def test_package_owned_configuration_contract(self):
        for kind in FILES:
            self.assertTrue((OPS / f'nginx-live-{kind}.conf').is_file(), kind)
        load = (OPS / 'nginx-live-load.conf').read_text()
        self.assertEqual(load.strip(), 'load_module /usr/lib/nginx/modules/ngx_http_project_live_module.so;')
        http = (OPS / 'nginx-live-http.conf').read_text()
        self.assertEqual(http.strip(), '''project_live_zone;
server {
    listen unix:/run/keimirror-live/status.sock;
    server_name localhost;
    project_live off;
    access_log off;
    location = / {
        project_live_status;
    }
    location / { return 404; }
}''')
        self.assertEqual((OPS / 'nginx-live-server.conf').read_text().strip(), 'project_live on;')
        self.assertEqual((OPS / 'nginx-live-runtime.conf').read_text().strip(),
                         '[Service]\nRuntimeDirectory=keimirror-live\nRuntimeDirectoryMode=0700')
        mirror = (OPS / 'nginx-mirror-location.conf').read_text()
        include = 'include /usr/share/keimirror-live/server/*.conf;'
        self.assertEqual(mirror.count(include), 1)
        self.assertLess(mirror.index(include), mirror.index('location'))

    def test_packaging_section_without_module_rebuild(self):
        """Execute only packaging AST nodes with an inert module fixture."""
        tree = ast.parse((MODULE / 'build.py').read_text())
        start = next(i for i, node in enumerate(tree.body)
                     if (isinstance(node, ast.Assign)
                         and any(isinstance(t, ast.Name) and t.id == 'pkg' for t in node.targets))
                     or (isinstance(node, ast.With)
                         and any(isinstance(item.context_expr, ast.Call)
                                 and isinstance(item.context_expr.func, ast.Attribute)
                                 and isinstance(item.context_expr.func.value, ast.Name)
                                 and item.context_expr.func.value.id == 'tempfile'
                                 and item.context_expr.func.attr == 'TemporaryDirectory'
                                 for item in node.items)))
        end = next(i for i, node in enumerate(tree.body) if i >= start
                   and isinstance(node, ast.Assign)
                   and any(isinstance(t, ast.Name) and t.id == 'artifacts' for t in node.targets))
        section = ast.Module(body=tree.body[start:end], type_ignores=[])
        with tempfile.TemporaryDirectory() as directory:
            evidence = Path(directory)
            (evidence / 'ngx_http_project_live_module.so').write_bytes(b'packaging-only inert fixture\n')
            # A prior staging tree and unrelated evidence are not ours to reuse or delete.
            preserved = {
                'package/etc/nginx/modules-enabled/stale.conf': b'load_module stale.so;\n',
                'package/DEBIAN/conffiles': b'/etc/nginx/modules-enabled/stale.conf\n',
                'package/DEBIAN/postinst': b'#!/bin/sh\nexit 0\n',
                'package/DEBIAN/prerm': b'#!/bin/sh\nexit 0\n',
                'package/unrelated.txt': b'old staging evidence\n',
                'unrelated-evidence.txt': b'keep this evidence\n',
                'package-unrelated/keep.txt': b'not our temporary staging\n',
            }
            for relative, content in preserved.items():
                path = evidence / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
                if path.name in ('postinst', 'prerm'):
                    path.chmod(0o755)
            original_directories = {p for p in evidence.iterdir() if p.is_dir()}
            def run(args, name):
                subprocess.run([str(a) for a in args], check=True, capture_output=True)
            exec(compile(section, str(MODULE / 'build.py'), 'exec'),
                 {'E': evidence, 'ROOT': MODULE, 'VERSION': '1.26.3-3+deb13u10',
                  'shutil': shutil, 'tempfile': tempfile, 'Path': Path, 'run': run})
            for relative, content in preserved.items():
                self.assertEqual((evidence / relative).read_bytes(), content, relative)
            self.assertEqual({p for p in evidence.iterdir() if p.is_dir()}, original_directories)
            package = evidence / 'libnginx-mod-http-project-live_amd64.deb'
            extracted = evidence / 'extracted'
            subprocess.run(['dpkg-deb', '-R', str(package), str(extracted)], check=True, capture_output=True)
            expected = set(FILES.values()) | {'usr/lib/nginx/modules/ngx_http_project_live_module.so', 'DEBIAN/control'}
            actual = {str(p.relative_to(extracted)) for p in extracted.rglob('*') if p.is_file()}
            self.assertEqual(actual, expected)  # No conffiles, maintainer scripts, /etc auto-enable.
            for kind, target in FILES.items():
                self.assertEqual((extracted / target).read_bytes(), (OPS / f'nginx-live-{kind}.conf').read_bytes())
            self.assertIn('Depends: nginx (= 1.26.3-3+deb13u10), nginx-abi-1.26.3-1\n',
                          (extracted / 'DEBIAN/control').read_text())

    @unittest.skipUnless(os.environ.get('LIVE_DEPLOYMENT_NGINX'), 'set LIVE_DEPLOYMENT_NGINX to extracted exact nginx')
    def test_removed_package_empty_globs_keep_nginx_config_valid(self):
        binary = Path(os.environ['LIVE_DEPLOYMENT_NGINX'])
        self.assertEqual(hashlib.sha256(binary.read_bytes()).hexdigest(),
                         '3f804ae7dda33a8368993d5d6087bfbc5fbf8c8ed1760af12fc9e8a74966d8a7')
        with tempfile.TemporaryDirectory() as directory:
            prefix = Path(directory)
            share = prefix / 'removed-package/usr/share/keimirror-live'
            mirror = (OPS / 'nginx-mirror-location.conf').read_text().replace('/usr/share/keimirror-live', str(share))
            config = prefix / 'nginx.conf'
            config.write_text(f'''error_log stderr;
pid {prefix}/nginx.pid;
include {share}/load/*.conf;
events {{ }}
http {{
    access_log off;
    client_body_temp_path {prefix}/body;
    proxy_temp_path {prefix}/proxy;
    fastcgi_temp_path {prefix}/fastcgi;
    uwsgi_temp_path {prefix}/uwsgi;
    scgi_temp_path {prefix}/scgi;
    include {share}/http/*.conf;
    server {{
        listen unix:{prefix}/mirror.sock;
        {mirror}
    }}
}}
''')
            result = subprocess.run([str(binary), '-t', '-p', directory + '/', '-c', str(config)],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('test is successful', result.stderr)


if __name__ == '__main__':
    unittest.main()
