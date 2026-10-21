"""One verification entry for local development and GitHub Actions."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
SKILLS = tuple(json.loads((ROOT / 'release-manifest.json').read_text(encoding='utf-8'))['skills'])
SCRIPTS = ROOT / '.agents/skills/develop-system/scripts'
sys.path.insert(0, str(SCRIPTS))
from record_check import run_record


def files(root):
    if not root.is_dir() or not (root / 'SKILL.md').is_file():
        raise ValueError('Missing skill copy: ' + str(root))
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in root.rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix != '.pyc'}


def compare_copies(source, target):
    left, right = files(source), files(target)
    changed = [name for name in sorted(left.keys() | right.keys()) if left.get(name) != right.get(name)]
    if changed:
        raise ValueError(f'Copy mismatch {source} -> {target}: {changed}')
    print(f'IDENTICAL: {source} -> {target} ({len(left)} files)')


def installed_root():
    return Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex'))) / 'skills'


def stage(name, installed):
    if name == 'dependencies':
        import importlib.metadata
        import yaml
        actual = importlib.metadata.version('PyYAML')
        if actual != '6.0.3':
            raise ValueError('Install requirements-checks.txt; expected PyYAML 6.0.3, got ' + actual)
        print('PyYAML ' + actual)
    elif name == 'format':
        sys.path.insert(0, str(ROOT / 'tools/verification'))
        from skill_format import validate_skill
        roots = [ROOT / '.agents/skills', Path(os.environ['DEVELOP_SYSTEM_PACKAGE_ROOT'])]
        if installed:
            roots.append(Path(installed))
        for root in roots:
            for skill in SKILLS:
                valid, message = validate_skill(root / skill)
                if not valid:
                    raise ValueError(f'{root / skill}: {message}')
                print(f'VALID: {root / skill}: {message}')
    elif name == 'registry':
        from validate_registry import validate
        roots = [ROOT / '.agents/skills', Path(os.environ['DEVELOP_SYSTEM_PACKAGE_ROOT'])]
        if installed:
            roots.append(Path(installed))
        for root in roots:
            path = root / 'develop-system/registry.json'
            data = json.loads(path.read_text(encoding='utf-8'))
            errors = validate(data, path.parent)
            if errors:
                raise ValueError('; '.join(errors))
            print(f'VALID: {path}: {len(data["skills"])} skills')
    elif name == 'copies':
        for skill in SKILLS:
            source = ROOT / '.agents/skills' / skill
            compare_copies(source, Path(os.environ['DEVELOP_SYSTEM_PACKAGE_ROOT']) / skill)
            if installed:
                compare_copies(source, Path(installed) / skill)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--installed', nargs='?', const=str(installed_root()),
        help='Also require installed copies (defaults to CODEX_HOME/skills)')
    parser.add_argument('--output-dir', help='New result directory; never reuse previous results')
    parser.add_argument('--timeout', type=float, default=300, help='Bounded seconds for each actual verification command')
    parser.add_argument('--stage', choices=['dependencies', 'format', 'registry', 'copies'], help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error('timeout must be positive')
    if args.stage:
        stage(args.stage, args.installed)
        return 0
    output = Path(args.output_dir) if args.output_dir else ROOT / '.scratch/verification' / uuid.uuid4().hex
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    from release_bundle import stage_skills
    package_root = output / 'skill-packages'
    stage_skills(ROOT, package_root)
    os.environ['DEVELOP_SYSTEM_PACKAGE_ROOT'] = str(package_root)
    extra = ['--installed', args.installed] if args.installed else []
    def stage_command(name):
        return [sys.executable, '-B', str(Path(__file__).resolve()), '--stage', name, *extra]
    commands = [
        ('dependencies', stage_command('dependencies')),
        ('workflow-tests', [sys.executable, '-B', '-m', 'unittest', 'discover', '-s', str(SCRIPTS), '-p', 'test_*.py', '-v']),
        ('verification-tests', [sys.executable, '-B', '-m', 'unittest', 'discover', '-s', str(ROOT / 'tools/verification'), '-p', 'test_*.py', '-v']),
        ('skill-format', stage_command('format')),
        ('registry', stage_command('registry')),
        ('copies', stage_command('copies')),
    ]
    records = []
    for name, command in commands:
        path = output / (name + '.json')
        record = run_record(command, ROOT, path, timeout=args.timeout)
        records.append({'name': name, 'status': record['status'], 'exit_code': record['exit_code'], 'context': record['context'],
            'record': {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}})
        print(f'{name}: {record["status"]} (exit={record["exit_code"]})', flush=True)
    passed = all(r['status'] == 'passed' for r in records)
    summary = {'schema_version': 1, 'status': 'passed' if passed else 'failed',
        'scope': {'source': str(ROOT / '.agents/skills'), 'packages': str(Path(os.environ['DEVELOP_SYSTEM_PACKAGE_ROOT'])),
                  'installed': args.installed}, 'checks': records}
    (output / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print('Results: ' + str(output / 'summary.json'))
    return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(main())
