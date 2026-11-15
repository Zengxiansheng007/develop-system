"""Prepare and restore an explicitly reviewed local implement update (no installer)."""
import argparse
import hashlib
import json
import os
import re
import stat
from pathlib import Path
import subprocess
import tempfile

BEGIN = b'<!-- BEGIN LOCAL IMPLEMENT RUNTIME v1 -->'
END = b'<!-- END LOCAL IMPLEMENT RUNTIME v1 -->'
HOOK = BEGIN + b'\nBefore implementation, read [the local runtime extension](../implement-runtime/SKILL.md). Apply it during planning, candidate verification, final review, and completion; missing extension is a blocker.\n' + END


def digest(data):
    return hashlib.sha256(data).hexdigest()


def safe(path):
    path = path.absolute()
    for part in (path, *path.parents):
        try:
            attributes = getattr(part.lstat(), 'st_file_attributes', 0)
        except FileNotFoundError:
            attributes = 0
        if part.is_symlink() or attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise ValueError('Symlink/junction outside restore contract')
    return path.resolve()


def inventory(root):
    result = {}
    for name in ('implement', 'implement-runtime', 'runtime-verify', 'develop-system', 'tdd', 'code-review'):
        skill = safe(root / name)
        for required in ('SKILL.md', 'LICENSE'):
            if not (skill / required).is_file():
                raise ValueError('Missing reference: ' + name + '/' + required)
        for path in sorted(skill.rglob('*')):
            safe(path)
            if path.is_file() and '__pycache__' not in path.parts and path.suffix != '.pyc':
                relative = path.relative_to(root).as_posix()
                if relative != 'implement/SKILL.md':
                    result[relative] = digest(path.read_bytes())
    return result


def check_hook(data):
    normalized = data.replace(b'\r\n', b'\n')
    if (normalized.count(b'BEGIN LOCAL IMPLEMENT RUNTIME') != 1 or
            normalized.count(b'END LOCAL IMPLEMENT RUNTIME') != 1 or normalized.count(HOOK) != 1):
        raise ValueError('Missing, duplicate, partial or conflicting hook')


def check_references(root, data):
    # Only local Markdown links: remote references are not downloaded or qualified.
    references = {}
    for owner, body in ((root / 'implement/SKILL.md', data),
                        (root / 'implement-runtime/SKILL.md', (root / 'implement-runtime/SKILL.md').read_bytes())):
        text = body.decode('utf-8')
        if re.search(r'\[[^\]\n]+\]\s*\[[^\]\n]*\]|^\s{0,3}\[[^\]\n]+\]:', text, re.MULTILINE):
            raise ValueError('Unsupported reference-style Markdown; use explicit inline links for review')
        for link in re.findall(r'\[[^\]]*\]\(([^)]+)\)', text):
            if '://' in link or link.startswith('#'):
                continue
            target = safe(owner.parent / link.split('#')[0])
            if root not in target.parents or not target.is_file():
                raise ValueError('Missing or unsafe reference: ' + link)
            references[target.relative_to(root).as_posix()] = digest(target.read_bytes())
    return references


def prepare(args, root, snapshot):
    if snapshot.exists():
        raise ValueError('Snapshot must be a new directory')
    local = (root / 'implement/SKILL.md').read_bytes()
    check_hook(local)
    base = safe(args.base).read_bytes()
    candidate = safe(args.candidate).read_bytes()
    source = json.loads((root / 'implement/source.json').read_text(encoding='utf-8'))
    if digest(base) != source['original_files']['SKILL.md']:
        raise ValueError('Base differs from pinned upstream provenance')
    if not args.review.strip():
        raise ValueError('Explicit semantic review reference required')
    dependencies = inventory(root)
    with tempfile.TemporaryDirectory() as folder:
        paths = [Path(folder) / name for name in ('local', 'base', 'candidate')]
        for path, data in zip(paths, (local, base, candidate)):
            path.write_bytes(data)
        merged = subprocess.run(['git', 'merge-file', '--stdout', *map(str, paths)],
                                capture_output=True, timeout=30)
    if merged.returncode:
        raise ValueError('Incompatible textual merge; retain inputs and review conflicts manually')
    check_hook(merged.stdout)
    dependencies.update(check_references(root, merged.stdout))
    snapshot.mkdir(parents=True, exist_ok=False)
    blobs = {'local': local, 'base': base, 'candidate': candidate, 'merged': merged.stdout}
    for name, data in blobs.items():
        (snapshot / (name + '.md')).write_bytes(data)
    manifest = {'schema_version': 1, 'skills_root': str(root), 'source': source,
                'review': args.review, 'dependencies': dependencies,
                'files': {name: digest(data) for name, data in blobs.items()}}
    (snapshot / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    return {'action': 'prepared', 'manifest_sha256': digest((snapshot / 'manifest.json').read_bytes())}


def restore(root, snapshot):
    manifest = json.loads(safe(snapshot / 'manifest.json').read_text(encoding='utf-8'))
    if manifest['schema_version'] != 1 or manifest['skills_root'] != str(root):
        raise ValueError('Snapshot identity mismatch')
    blobs = {}
    for name in ('local', 'base', 'candidate', 'merged'):
        data = safe(snapshot / (name + '.md')).read_bytes()
        if digest(data) != manifest['files'][name]:
            raise ValueError('Snapshot content changed')
        blobs[name] = data
    check_hook(blobs['merged'])
    dependencies = inventory(root)
    dependencies.update(check_references(root, blobs['merged']))
    if dependencies != manifest['dependencies']:
        raise ValueError('Dependencies changed or references missing; prepare a new reviewed update')
    target = safe(root / 'implement/SKILL.md')
    current = target.read_bytes()
    if current not in (blobs['candidate'], blobs['merged']):
        raise ValueError('Unexpected upstream or local edits; refusing overwrite')
    action = 'unchanged'
    if current != blobs['merged']:
        fd, temporary = tempfile.mkstemp(prefix='restore-', dir=target.parent)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(blobs['merged'])
            os.replace(temporary, target)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        action = 'restored'
    return {'action': action, 'before_sha256': digest(current), 'after_sha256': digest(target.read_bytes()),
            'manifest_sha256': digest((snapshot / 'manifest.json').read_bytes())}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('prepare', 'restore'))
    parser.add_argument('--skills-root', required=True, type=Path)
    parser.add_argument('--snapshot', required=True, type=Path)
    parser.add_argument('--base', type=Path)
    parser.add_argument('--candidate', type=Path)
    parser.add_argument('--review')
    args = parser.parse_args()
    try:
        root, snapshot = safe(args.skills_root), safe(args.snapshot)
        if snapshot == root or root in snapshot.parents:
            raise ValueError('Snapshot must be outside the skills replacement boundary')
        if args.operation == 'prepare':
            if not all((args.base, args.candidate, args.review)):
                raise ValueError('prepare requires base, candidate and review reference')
            result = prepare(args, root, snapshot)
        else:
            result = restore(root, snapshot)
        print(json.dumps({**result, 'runtime_status': 'unqualified'}))
        return 0
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        print(json.dumps({'action': 'blocked', 'runtime_status': 'unqualified', 'reason': str(error)}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
