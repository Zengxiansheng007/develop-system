"""Build and verify an allowlisted, deterministic source Skill bundle."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SECRET = re.compile(rb'(?:github_pat_[A-Za-z0-9_]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----)')

def inventory(root):
    config = json.loads((root / 'release-manifest.json').read_text(encoding='utf-8'))
    names = config['skills']
    if config.get('schema_version') != 1 or not names or len(names) != len(set(names)):
        raise ValueError('Invalid release manifest')
    data = {}
    for name in names:
        if not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', name):
            raise ValueError('Invalid skill name')
        source = root / '.agents/skills' / name
        if source.is_symlink() or not (source / 'SKILL.md').is_file() or not (source / 'LICENSE').is_file():
            raise ValueError('Skill source or license missing: ' + name)
        for path in sorted(source.rglob('*')):
            if path.is_symlink():
                raise ValueError('Symlink outside the release contract: ' + str(path))
            if not path.is_file() or '__pycache__' in path.parts or path.suffix == '.pyc':
                continue
            if any(p.startswith('.') for p in path.relative_to(source).parts):
                raise ValueError('Hidden file in skill source')
            content = path.read_bytes()
            if SECRET.search(content):
                raise ValueError('Credential-like content in skill source')
            data['skills/' + name + '/' + path.relative_to(source).as_posix()] = content
    return data

def stage_skills(root, output):
    data = inventory(root)
    output.mkdir(parents=True, exist_ok=False)
    for name, content in data.items():
        path = output / name.removeprefix('skills/')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

def build(root, output, version, source_commit):
    if not re.fullmatch(r'\d+\.\d+\.\d+(?:-[a-zA-Z0-9.-]+)?', version):
        raise ValueError('Invalid version')
    if not re.fullmatch(r'[0-9a-f]{40}', source_commit):
        raise ValueError('Source commit must be a full Git SHA')
    contents = inventory(root)
    for name in ('LICENSE', 'CHANGELOG.md'):
        contents[name] = (root / name).read_bytes()
    manifest = {'schema_version': 1, 'version': version, 'source_commit': source_commit,
                'skills': json.loads((root / 'release-manifest.json').read_text())['skills'],
                'files': {name: hashlib.sha256(body).hexdigest() for name, body in sorted(contents.items())}}
    contents['MANIFEST.json'] = (json.dumps(manifest, sort_keys=True, indent=2) + '\n').encode()
    output.mkdir(parents=True, exist_ok=False)
    archive = output / f'develop-system-{version}.zip'
    with zipfile.ZipFile(archive, 'x', compression=zipfile.ZIP_STORED) as bundle:
        for name, body in sorted(contents.items()):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            bundle.writestr(info, body)
    verify(archive)
    checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
    (output / 'SHA256SUMS').write_text(f'{checksum}  {archive.name}\n', encoding='utf-8', newline='\n')
    return archive

def verify(archive):
    with zipfile.ZipFile(archive) as bundle:
        names = bundle.namelist()
        if len(names) != len(set(names)):
            raise ValueError('Duplicate archive members')
        manifest = json.loads(bundle.read('MANIFEST.json'))
        if set(names) != set(manifest['files']) | {'MANIFEST.json'}:
            raise ValueError('Unexpected or missing archive member')
        for name, expected in manifest['files'].items():
            if hashlib.sha256(bundle.read(name)).hexdigest() != expected:
                raise ValueError('Bundle hash mismatch: ' + name)
    return manifest

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--version')
    args = parser.parse_args()
    # A release must bind committed bytes; ignored runs/receipts are excluded.
    status = subprocess.check_output(['git', '-C', str(ROOT), 'status', '--porcelain'], text=True)
    if status.strip():
        raise SystemExit('Commit the release source before building a release bundle')
    commit = subprocess.check_output(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], text=True).strip()
    version = (ROOT / 'VERSION').read_text().strip()
    if args.version and args.version != version:
        raise SystemExit('Requested version differs from VERSION')
    print(build(ROOT, args.output_dir, version, commit))

if __name__ == '__main__':
    main()
