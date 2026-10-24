"""Execute one explicitly selected public test module with source provenance."""
import hashlib
import argparse
import json
from pathlib import Path
import re
import platform
import sys
import unittest


def qualify(scripts, module, report):
    scripts = Path(scripts).resolve()  # Pin the real selected scripts directory before importing.
    report = Path(report).resolve()
    if report.exists():
        raise FileExistsError(report)  # Preserve earlier evidence before even loading the selected test module.
    if not re.fullmatch(r'test_[A-Za-z0-9_]+', module) or not (scripts / (module + '.py')).is_file():
        raise ValueError('Missing selected module: ' + module)  # A typo or arbitrary import cannot stand in for the requested test source.
    for path in scripts.glob('*.py'):
        cached = sys.modules.get(path.stem)
        origin = getattr(cached, '__file__', None)
        if cached is not None and (not origin or Path(origin).resolve() != path.resolve()):
            raise ValueError('Foreign loaded module: ' + path.stem)  # Never let an existing source cache stand in for a package module.
    original_path = sys.path[:]
    outcomes = []

    class Result(unittest.TextTestResult):
        def addSuccess(self, test):
            super().addSuccess(test)
            outcomes.append({'id': test.id(), 'status': 'passed'})  # Preserve individual outcomes rather than only a test count.

        def addFailure(self, test, error):
            super().addFailure(test, error)
            outcomes.append({'id': test.id(), 'status': 'failed'})

        def addError(self, test, error):
            super().addError(test, error)
            outcomes.append({'id': test.id(), 'status': 'error'})

        def addSkip(self, test, reason):
            super().addSkip(test, reason)
            outcomes.append({'id': test.id(), 'status': 'skipped', 'reason': reason})  # A skipped test is never relabeled passed.

        def addExpectedFailure(self, test, error):
            super().addExpectedFailure(test, error)
            outcomes.append({'id': test.id(), 'status': 'expected_failure'})

        def addUnexpectedSuccess(self, test):
            super().addUnexpectedSuccess(test)
            outcomes.append({'id': test.id(), 'status': 'unexpected_success'})

        def addSubTest(self, test, subtest, error):
            super().addSubTest(test, subtest, error)
            if error is not None:
                status = 'failed' if issubclass(error[0], test.failureException) else 'error'
                outcomes.append({'id': subtest.id(), 'status': status})  # Failed subtests need evidence even without a parent addFailure callback.

    try:
        sys.path.insert(0, str(scripts))
        suite = unittest.defaultTestLoader.loadTestsFromName(module)
        if not suite.countTestCases():
            raise ValueError('No tests in selected module: ' + module)
        result = unittest.TextTestRunner(verbosity=2, resultclass=Result).run(suite)
        loaded = {}
        for name, value in list(sys.modules.items()):
            path = getattr(value, '__file__', None)
            if path and Path(path).resolve().is_relative_to(scripts):
                path = Path(path).resolve()
                loaded[name] = {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
        status = 'passed_with_skips' if result.skipped else 'passed'
        data = {'schema_version': 1, 'status': status if result.wasSuccessful() else 'failed',
                'scripts': str(scripts), 'tests_run': result.testsRun, 'tests': outcomes, 'loaded': loaded,
                'environment': {'platform': sys.platform, 'python_version': platform.python_version(),
                                'executable': str(Path(sys.executable).resolve())}}  # Record identity only, never the process environment.
        with Path(report).open('x', encoding='utf-8') as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
        return data
    finally:
        sys.path[:] = original_path  # The caller's import search path is not permanently changed.


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--scripts', type=Path)
    mode.add_argument('--stage-package', type=Path)
    parser.add_argument('--module')
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    if args.stage_package:
        if args.module or args.report:
            parser.error('Staging cannot also execute a module or write a test report')
        from release_bundle import stage_skills
        stage_skills(Path(__file__).resolve().parents[1], args.stage_package)  # Reuse the production allowlist and non-existing-target contract.
        print(json.dumps({'package': str(args.stage_package.resolve())}))
        return 0
    if not args.module or not args.report:
        parser.error('Qualification requires --module and --report')
    result = qualify(args.scripts, args.module, args.report)
    print(json.dumps({'status': result['status'], 'report': str(args.report.resolve()), 'tests_run': result['tests_run']}))
    return 0 if result['status'] in {'passed', 'passed_with_skips'} else 1  # Skips remain explicit in the report even when unittest succeeds.


if __name__ == '__main__':
    raise SystemExit(main())
