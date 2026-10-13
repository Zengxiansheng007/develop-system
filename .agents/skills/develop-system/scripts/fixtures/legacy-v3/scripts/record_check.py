"""Run an argv command and persist its actual result without a shell."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import uuid


def now():
    return datetime.now(timezone.utc).isoformat()


def run_record(command, cwd, output, timeout=300):
    if not command or any(not isinstance(a, str) or not a for a in command):
        raise ValueError('Nonempty argv required')
    output = Path(output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    # Reserve a new path before execution; never leave an old passed result at it.
    with output.open('x', encoding='utf-8') as destination:
        record = {'schema_version': 1, 'record_id': uuid.uuid4().hex,
            'command': command, 'cwd': str(Path(cwd).resolve()),
            'started_at': now(), 'finished_at': None, 'exit_code': None,
            'stdout': '', 'stderr': '', 'status': 'failed', 'error': None}
        env = {**os.environ, 'PYTHONUTF8': '1', 'PYTHONIOENCODING': 'utf-8', 'PYTHONDONTWRITEBYTECODE': '1'}
        try:
            result = subprocess.run(command, cwd=record['cwd'], env=env, shell=False,
                capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=timeout)
            record.update(exit_code=result.returncode, stdout=result.stdout, stderr=result.stderr)
        except subprocess.TimeoutExpired as exc:
            def decode(value):
                return value.decode('utf-8', errors='replace') if isinstance(value, bytes) else (value or '')
            record.update(stdout=decode(exc.stdout), stderr=decode(exc.stderr), error='timeout')
        except OSError as exc:
            record['error'] = str(exc)
        record['finished_at'] = now()
        record['status'] = 'passed' if record['exit_code'] == 0 and record['error'] is None else 'failed'
        json.dump(record, destination, ensure_ascii=False, indent=2)
        destination.write('\n')
        destination.flush()
        os.fsync(destination.fileno())
    return record


def validate_record(record):
    if not isinstance(record, dict) or record.get('schema_version') != 1:
        raise ValueError('Unsupported check record')
    for field in ('record_id', 'cwd', 'started_at', 'finished_at'):
        if not isinstance(record.get(field), str) or not record[field]:
            raise ValueError('Incomplete check record: ' + field)
    if not Path(record['cwd']).is_absolute():
        raise ValueError('Check cwd must be absolute')
    command = record.get('command')
    if not isinstance(command, list) or not command or any(not isinstance(a, str) or not a for a in command):
        raise ValueError('Check command must be argv')
    for field in ('stdout', 'stderr'):
        if not isinstance(record.get(field), str):
            raise ValueError('Check output must be text')
    started = datetime.fromisoformat(record['started_at'])
    finished = datetime.fromisoformat(record['finished_at'])
    if started.tzinfo is None or finished.tzinfo is None or finished < started:
        raise ValueError('Invalid check timestamps')
    code = record.get('exit_code')
    if code is not None and type(code) is not int:
        raise ValueError('Invalid check exit code')
    if 'error' not in record or (record['error'] is not None and not isinstance(record['error'], str)):
        raise ValueError('Invalid check error')
    actual = 'passed' if code == 0 and record['error'] is None else 'failed'
    if record.get('status') != actual:
        raise ValueError('Check status contradicts execution result')
    return actual


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    parser.add_argument('--cwd', default=str(Path.cwd()))
    parser.add_argument('--timeout', type=float, default=300)
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ['--'] else args.command
    if args.timeout <= 0:
        parser.error('timeout must be positive')
    result = run_record(command, args.cwd, args.output, args.timeout)
    print(json.dumps({'record': str(Path(args.output).resolve()), 'status': result['status'], 'exit_code': result['exit_code']}))
    return result['exit_code'] if isinstance(result['exit_code'], int) and 0 <= result['exit_code'] <= 255 else 1


if __name__ == '__main__':
    raise SystemExit(main())
