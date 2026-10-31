"""Read-only explicitly selected host artifact capture; no remote file writes or signals."""
import base64, io, hashlib, json, os, shlex, subprocess, tarfile
from pathlib import Path
os.umask(0o077)
import argparse
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--host-id',type=int,required=True)
parser.add_argument('--hostname',required=True)
parser.add_argument('--output',type=Path,required=True)
args=parser.parse_args()
D=args.output; D.mkdir(mode=0o700,exist_ok=False)
code=r'''
import base64,hashlib,io,json,os,pathlib,re,subprocess,tarfile
assert os.uname().nodename==EXPECTED_HOSTNAME
pid=int(pathlib.Path('/run/nginx.pid').read_text())
paths={pathlib.Path('/usr/sbin/nginx')}
modules=[]
for row in pathlib.Path(f'/proc/{pid}/maps').read_text().splitlines():
 f=row.split(maxsplit=5)
 if len(f)==6 and f[5].startswith('/') and '/nginx/modules/' in f[5]:
  assert not f[5].endswith(' (deleted)')
  paths.add(pathlib.Path(f[5]));modules.append(f[5])
modules=sorted(set(modules));assert any('nchan' in p for p in modules)
for p in list(paths):
 raw=subprocess.check_output(['ldd',str(p)],text=True)
 for dep in re.findall(r'(?:=>\s+)?(/[^\s]+)',raw):paths.add(pathlib.Path(dep))
# The loaded Perl module initializes even without perl directives; retain its
# packaged interpreter support files, not host configuration or credentials.
for directory in ('/usr/share/perl/5.40.1', '/usr/lib/x86_64-linux-gnu/perl/5.40.1', '/usr/lib/x86_64-linux-gnu/perl5/5.40', '/usr/share/lua/5.1', '/usr/lib/x86_64-linux-gnu/lua/5.1', '/usr/share/luajit-2.1'):
 root=pathlib.Path(directory)
 if root.exists():paths.update(p for p in root.rglob('*') if p.is_file())
for name in subprocess.check_output(['dpkg-query','-L','libnginx-mod-http-perl'],text=True).splitlines():
 p=pathlib.Path(name)
 if p.is_file() and ('nginx.pm' in name or name.endswith('/nginx.so')):paths.add(p)
buf=io.BytesIO(); manifest={}
with tarfile.open(fileobj=buf,mode='w:gz') as tf:
 for p in sorted(paths):
  raw=p.read_bytes();manifest[str(p)]=dict(sha256=hashlib.sha256(raw).hexdigest(),size=len(raw))
  ti=tarfile.TarInfo(str(p).lstrip('/'));ti.size=len(raw);ti.mode=0o755 if p.name=='nginx' or '.so' in p.name else 0o644
  tf.addfile(ti,io.BytesIO(raw))
blob=buf.getvalue()
print(json.dumps(dict(modules=modules,files=manifest,sha256=hashlib.sha256(blob).hexdigest(),archive=base64.b64encode(blob).decode())))
'''
code='EXPECTED_HOSTNAME='+repr(args.hostname)+'\n'+code
cmd='sudo -n /usr/bin/python3 -B -c '+shlex.quote(code)
p=subprocess.run(['termix','exec',str(args.host_id),cmd],capture_output=True,text=True,timeout=180,env={**os.environ,'TERMIX_REQUEST_TIMEOUT_MS':'180000'})
(D/'transport.json').write_text(json.dumps(dict(rc=p.returncode,stdout=p.stdout,stderr=p.stderr)))
assert p.returncode==0,p.stderr
d=json.loads(p.stdout)
if 'stdout' in d:
 assert d.get('exitCode',d.get('exit_code'))==0
 d=json.loads(d['stdout'])
blob=base64.b64decode(d.pop('archive'));assert hashlib.sha256(blob).hexdigest()==d['sha256']
(D/'payload.tar.gz').write_bytes(blob);(D/'manifest.json').write_text(json.dumps(d,indent=2))
print(json.dumps(dict(modules=len(d['modules']),files=len(d['files']),archive_bytes=len(blob),sha256=d['sha256'])))
