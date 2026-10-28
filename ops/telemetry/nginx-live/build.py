#!/usr/bin/env python3
"""Off-host, no-install exact Debian build and tests. Requires cc/make/dpkg/curl.
Usage: python3 build.py /absolute/private/evidence-directory
Downloads stable .deb dependencies into that directory, NEVER installs them.
"""
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request

ROOT = Path(__file__).resolve().parent
E = Path(sys.argv[1]).resolve()
E.mkdir(parents=True, exist_ok=True)
S = E/'source'
VERSION = '1.26.3-3+deb13u10'
BINARY_SHA = '3f804ae7dda33a8368993d5d6087bfbc5fbf8c8ed1760af12fc9e8a74966d8a7'
BASE = 'https://deb.debian.org/debian-security/pool/updates/main/n/nginx/'

def run(args, name, cwd=E, env=None, expected=0):
    p = subprocess.run([str(x) for x in args], cwd=cwd, env=env, capture_output=True)
    (E/(name+'.log')).write_bytes(p.stdout+p.stderr)
    if p.returncode != expected:
        raise RuntimeError(f'{name} returned {p.returncode}; see evidence log')
    return p

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def download(name):
    p=E/name
    if not p.exists(): urllib.request.urlretrieve(BASE+name,p)
    return p

for tool in ('cc','make','dpkg-source','dpkg-deb','apt','openssl'):
    if not shutil.which(tool): raise SystemExit(f'missing prerequisite: {tool}; not auto-installing')
if platform.machine() != 'x86_64': raise SystemExit('this artifact pins amd64')
os_release=Path('/etc/os-release').read_text()
if 'VERSION_ID="13"' not in os_release: raise SystemExit('requires Debian 13 build host; no OS upgrades')
run(['dpkg-query','-W','libssl-dev'], 'prerequisite-ssl')
run(['apt-cache','policy','libpcre2-dev','zlib1g-dev'], 'prerequisite-dependencies')
for n in [f'nginx_{VERSION}.dsc', f'nginx_{VERSION}.debian.tar.xz', 'nginx_1.26.3.orig.tar.gz', f'nginx_{VERSION}_amd64.deb']:
    download(n)
dsc=(E/f'nginx_{VERSION}.dsc').read_text()
checks=dsc.split('Checksums-Sha256:\n',1)[1].split('\nFiles:',1)[0]
for digest,size,name in re.findall(r'^ ([0-9a-f]{64}) (\d+) (\S+)$',checks,re.M):
    p=E/name
    assert p.stat().st_size==int(size) and sha(p)==digest, name
if not S.exists(): run(['dpkg-source','-x',f'nginx_{VERSION}.dsc',S], 'source-extract')
run(['dpkg-deb','-x',f'nginx_{VERSION}_amd64.deb','binary'], 'binary-extract')
assert sha(E/'binary/usr/sbin/nginx')==BINARY_SHA
for package in ['libpcre2-dev=10.46-1~deb13u3','zlib1g-dev=1:1.3.dfsg+really1.3.1-1+b1']:
    if not list(E.glob(package.split('=')[0]+'_*.deb')):
        run(['apt','download',package], 'download-'+package.split('=')[0])
for p in E.glob('*dev_*.deb'):
    run(['dpkg-deb','-x',p,E/'deps'],'extract-'+p.name)
flags=['--with-compat','--with-debug','--with-threads','--with-http_ssl_module','--with-http_v2_module','--with-http_v3_module','--with-pcre-jit',f'--with-cc-opt=-I{E}/deps/usr/include',f'--with-ld-opt=-L{E}/deps/usr/lib/x86_64-linux-gnu',f'--add-dynamic-module={ROOT}']
run(['./configure',*flags],'configure',S)
run(['make','modules'],'build',S)
shutil.copy2(S/'objs/ngx_http_project_live_module.so', E/'ngx_http_project_live_module.so')
includes=['src/core','src/event','src/event/modules','src/event/quic','src/os/unix','src/http','src/http/modules','src/http/v2','src/http/v3','objs',str(E/'deps/usr/include')]
cc=['cc','-O1','-g',*[arg for p in includes for arg in ('-I',p)]]
run([*cc,'-ffunction-sections','-fdata-sections','-Wl,--gc-sections',ROOT/'tests/test_send.c','src/core/ngx_rbtree.c','-o',E/'test_send'],'unit-build',S)
run([E/'test_send'],'unit')
env={**os.environ,'HTTP_MODULE_EVIDENCE':str(E)}
run([sys.executable,'-m','unittest','discover','-s',ROOT/'tests','-v'],'integration',ROOT,env)

# Deliberately broken, completion-only variant: prove the live test rejects it.
source=(ROOT/'ngx_http_project_live_module.c').read_text()
a=source.index('static void live_progress(')
b=source.index('static ngx_chain_t *live_send_chain(',a)
negative=source[:a]+'static void live_progress(live_connection *lc, off_t before) { (void)lc; (void)before; }\n'+source[b:]
needle='    live_request *q = data;\n'
assert negative.count(needle)==1
negative=negative.replace(needle,needle+'    ngx_shmtx_lock(&live_pool->mutex);\n    live->repos[q->repo].tx_bytes += q->request->connection->sent;\n    ngx_shmtx_unlock(&live_pool->mutex);\n')
(E/'completion-only.c').write_text(negative)
run([*cc,'-fPIC','-shared',E/'completion-only.c','objs/ngx_http_project_live_module_modules.c','-o',E/'completion-only.so'],'negative-build',S)
negative_env={**env,'HTTP_MODULE_SO':str(E/'completion-only.so')}
run([sys.executable,ROOT/'tests/test_integration.py','Integration.test_10_live_progress_before_request_completion'],'negative-completion-only',ROOT,negative_env,expected=1)
assert b'AssertionError: 0 not greater than 0' in (E/'negative-completion-only.log').read_bytes()

# Package normal files only; no postinst, /etc auto-enablement, install or reload.
# Exclusively create staging; never reuse or clean an unknown evidence tree.
with tempfile.TemporaryDirectory(prefix='package-', dir=E) as staging:
    pkg = Path(staging)
    (pkg/'DEBIAN').mkdir()
    (pkg/'usr/lib/nginx/modules').mkdir(parents=True)
    shutil.copy2(E/'ngx_http_project_live_module.so',pkg/'usr/lib/nginx/modules/ngx_http_project_live_module.so')
    for kind, target in {
        'load': 'usr/share/keimirror-live/load/project-live.conf',
        'http': 'usr/share/keimirror-live/http/project-live.conf',
        'server': 'usr/share/keimirror-live/server/project-live.conf',
        'runtime': 'usr/lib/systemd/system/nginx.service.d/keimirror-live.conf',
    }.items():
        destination = pkg/target
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT.parent/f'nginx-live-{kind}.conf', destination)
    (pkg/'DEBIAN/control').write_text(f'''Package: libnginx-mod-http-project-live
Version: 0.1.0+nginx{VERSION}
Architecture: amd64
Maintainer: Local module maintainer <root@localhost>
Depends: nginx (= {VERSION}), nginx-abi-1.26.3-1
Section: httpd
Priority: optional
Description: Private project live-byte telemetry (custom, review required)
 Exact-build HTTP/1 application-byte counters. No configuration auto-enablement.
''')
    run(['dpkg-deb','--root-owner-group','--build',pkg,E/'libnginx-mod-http-project-live_amd64.deb'],'package')
artifacts=[p for p in E.iterdir() if p.suffix in ('.deb','.gz','.xz','.dsc','.so')]
manifest={'nginx_version':VERSION,'nginx_binary_sha256':BINARY_SHA,'module_sha256':sha(E/'ngx_http_project_live_module.so'),'artifacts':{p.name:sha(p) for p in sorted(artifacts)},'configure':flags,'tests':'integration + deterministic send wrapper + rejected completion-only negative control','source_authentication':'HTTPS Debian-security archive; DSC component SHA256 verified; signer key not available locally; binary matches audited production hash','production_changes':False,'system_packages_installed':False}
(E/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
print(json.dumps(manifest,indent=2))
