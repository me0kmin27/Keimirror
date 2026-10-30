"""Reviewed staging boundary. Only stdlib imports; no artifact code is imported.

Bootstrap MUST verify this script's captured bytes against a separately reviewed
SHA256 before exec, using isolated Python. Manifest SHA is an approval argument,
not a value accepted from the untrusted input directory. OS Python/stdlib and
system executables/libraries are the trusted host base, never captured libraries.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import sys

LIMIT = 16 * 1024 * 1024
ARTIFACTS = {
    'nginx': '3f804ae7dda33a8368993d5d6087bfbc5fbf8c8ed1760af12fc9e8a74966d8a7',
    'old.so': '700adce1c053f3a0b1accf590f77d1f79d68a574599b47025af65b0dea0d02f1',
    'candidate.deb': 'a057348974aa3ab2ae5297e02ddaac7b882d4c4534c285e2d6f649e69ca6ea4e',
    'reader/http_rates.py': '9b85d1cf82abec9124834c71139f9b8b514bed29dddf4c21bd26b213c1523386',
    'reader/private_reasons.py': '0b99330982235456c72fb45f9c46d81c08f640e673e50eece5aa72081647675b',
}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def safe_read(path, limit=LIMIT):
    """Walk by directory descriptors: never follow even an ancestor symlink."""
    path = Path(path).absolute()
    if '..' in path.parts:
        raise ValueError('parent traversal')
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        for part in path.parts[1:-1]:
            nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
            os.close(fd); fd = nxt
        leaf = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=fd)
        try:
            before = os.fstat(leaf)
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > limit:
                raise ValueError('bounded single-link regular file required')
            data = bytearray()
            while len(data) <= limit:
                block = os.read(leaf, min(65536, limit + 1 - len(data)))
                if not block:
                    break
                data.extend(block)
            after = os.fstat(leaf)
            if len(data) > limit or (before.st_size, before.st_mtime_ns, before.st_ctime_ns, before.st_nlink) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns, after.st_nlink):
                raise ValueError('input changed while captured')
            return bytes(data)
        finally:
            os.close(leaf)
    finally:
        os.close(fd)


def trusted(path):
    path = Path(path).absolute()
    for part in [*reversed(path.parents), path]:
        s = part.lstat()
        if s.st_uid != 0 or s.st_gid != 0 or s.st_mode & 0o022 or stat.S_ISLNK(s.st_mode):
            raise PermissionError('root-controlled path required: ' + str(part))
    return path


def copy_verified(source, target, expected, mode=0o400):
    data = safe_read(source)
    if digest(data) != expected:
        raise ValueError('unreviewed input: ' + str(source))
    # Target parent is private and root-controlled; create a new inode, no links.
    with open(target, 'xb') as stream:
        os.chmod(target, mode)
        stream.write(data); stream.flush(); os.fsync(stream.fileno())
    if digest(safe_read(target)) != expected:
        raise ValueError('staged-byte mismatch')


def stage_artifacts(source, target):
    target.mkdir(mode=0o700)
    (target / 'reader').mkdir(mode=0o700)
    for name, expected in ARTIFACTS.items():
        copy_verified(source / name, target / name, expected, 0o500 if name == 'nginx' else 0o400)
    # No copytree: extra modules, pyc, shared objects, libraries and links ignored.
    return target


def manifest(source, expected):
    raw = safe_read(source / 'root-staging-manifest.json')
    if digest(raw) != expected:
        raise ValueError('root-reviewed manifest SHA mismatch')
    value = json.loads(raw)
    required = {'root_stage.py', 'isolated.py', 'transaction.py', 'inventory.py',
                'host-baselines.json', 'host-readonly-configs.json',
                'reader/http_rates.py', 'reader/private_reasons.py'}
    if set(value['files']) != required:
        raise ValueError('source closure mismatch')
    return raw, value['files']


def stage(source, artifacts, target, approved):
    if os.geteuid() != 0:
        raise PermissionError('root required')
    trusted(target.parent)
    raw, files = manifest(source, approved)
    target.mkdir(mode=0o700)  # exclusive: never repair/reuse a pre-existing stage
    (target / 'reader').mkdir(mode=0o700)
    for name, expected in files.items():
        copy_verified(source / name, target / name, expected)
    with open(target / 'root-staging-manifest.json', 'xb') as stream:
        stream.write(raw)
    os.chmod(target / 'root-staging-manifest.json', 0o400)
    stage_artifacts(artifacts, target / 'artifacts')
    verify(target, approved)
    print(json.dumps({'staged': str(target), 'manifest_sha256': approved, 'runtime_executed': False}))


def verify(target, approved):
    trusted(target)
    _, files = manifest(target, approved)
    for name, expected in files.items():
        if digest(safe_read(trusted(target / name))) != expected:
            raise ValueError('staged source mismatch')
    for name, expected in ARTIFACTS.items():
        if digest(safe_read(trusted(target / 'artifacts' / name))) != expected:
            raise ValueError('staged artifact mismatch')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=('stage', 'forward', 'rollback'))
    parser.add_argument('target', type=Path)
    parser.add_argument('approved_manifest_sha256')
    parser.add_argument('--source', type=Path)
    parser.add_argument('--artifacts', type=Path)
    args = parser.parse_args()
    if args.action == 'stage':
        stage(args.source, args.artifacts, args.target, args.approved_manifest_sha256)
    else:
        if os.geteuid() != 0:
            raise PermissionError('root required')
        verify(args.target, args.approved_manifest_sha256)
        # No local import before complete verification. Python -I -S -B required.
        sys.path.insert(0, str(args.target))
        import isolated
        isolated.run(args.target / 'artifacts', args.target / (args.action + '.json'), args.action)


if __name__ == '__main__':
    main()
