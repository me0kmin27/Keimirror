"""Read-only inventory for review; stdout is private, never apply input.

No nginx -T, signals, stage creation, chmod/chown or service operations. Execute
only on the explicitly selected existing host. Freeze and review output before
constructing readonly_configs; discovery itself does not authorize exceptions.
"""
import glob
import hashlib
import json
import os
from pathlib import Path
import shlex
import stat
import subprocess


def metadata(path):
    p = Path(path); s = p.lstat()
    value = dict(uid=s.st_uid, gid=s.st_gid, mode=stat.S_IMODE(s.st_mode),
                 dev=s.st_dev, ino=s.st_ino)
    if stat.S_ISREG(s.st_mode):
        if s.st_size > 16 * 1024 * 1024:
            raise ValueError('inventory file bound')
        value.update(mtime_ns=s.st_mtime_ns, sha256=hashlib.sha256(p.read_bytes()).hexdigest())
    elif stat.S_ISLNK(s.st_mode):
        value.pop('mode')
        value.update(target=os.readlink(p), mtime_ns=s.st_mtime_ns, ctime_ns=s.st_ctime_ns)
    elif not stat.S_ISDIR(s.st_mode):
        raise ValueError('unsupported inventory object')
    if p.lstat() != s:
        raise ValueError('inventory object changed')
    return value


def parents(path):
    return {str(a): metadata(a) for a in Path(path).parents}


def inventory():
    files, links, directives = {}, {}, []
    def visit(path):
        p = Path(path)
        for part in [*reversed(p.parents), p]:
            if part.is_symlink():
                links[str(part)] = dict(metadata(part), parents=parents(part))
        p = p.resolve(strict=True)
        if str(p) in files:
            return
        if len(files) >= 256:
            raise ValueError('inventory include bound')
        files[str(p)] = dict(metadata(p), parents=parents(p))
        lex = shlex.shlex(p.read_text(), posix=True, punctuation_chars=';{}')
        lex.whitespace_split = True
        tokens = []
        for token in lex:
            if token and all(c in ';{}' for c in token):
                if tokens:
                    directives.append(tokens)
                    if tokens[0] == 'include':
                        if len(tokens) != 2 or '$' in tokens[1]:
                            raise ValueError('unsupported include')
                        pattern = tokens[1] if tokens[1].startswith('/') else '/etc/nginx/' + tokens[1]
                        for match in sorted(glob.glob(pattern)):
                            visit(match)
                tokens = []
            else:
                tokens.append(token)
        if tokens:
            raise ValueError('unterminated configuration')
    visit('/etc/nginx/nginx.conf')
    # Existing document root and real file must both be demonstrated; no guessed
    # endpoint is promoted to an acceptance URL by this inventory.
    downloads = []
    for root in sorted({d[1] for d in directives if d[0] == 'root' and len(d) == 2}):
        if not root.startswith('/') or '$' in root:
            continue
        candidate = Path(root) / 'archlinux/lastupdate'
        if candidate.is_file() and candidate.stat().st_size <= 4096:
            data = candidate.read_bytes()
            downloads.append(dict(document_root=root, source_path=str(candidate),
                                  relative_url='/archlinux/lastupdate', size=len(data),
                                  sha256=hashlib.sha256(data).hexdigest(),
                                  source_metadata=metadata(candidate)))
    timers = subprocess.check_output(['systemctl', 'list-timers', '--all', '--no-pager', '--no-legend'], text=True, timeout=10)
    names = sorted({word for word in timers.split() if word.startswith('keimirror-') and word.endswith('.timer')})
    states = {name: subprocess.check_output(['systemctl', 'show', name, '-p', 'ActiveState', '-p', 'UnitFileState'], text=True, timeout=10) for name in names}
    # Re-read every object before reporting a frozen policy proposal.
    for path, expected in {**files, **links}.items():
        if dict(metadata(path), parents=parents(path)) != expected:
            raise ValueError('configuration changed during inventory')
    return dict(hostname=os.uname().nodename, boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
                files=files, links=links, timers=states, downloads=downloads,
                roots=[d for d in directives if d[0] in ('root', 'server_name', 'listen', 'worker_shutdown_timeout')],
                ready_to_apply=False, deployment_authorized=False)


if __name__ == '__main__':
    print(json.dumps(inventory(), sort_keys=True))
