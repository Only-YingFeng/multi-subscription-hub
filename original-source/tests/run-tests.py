"""Run the offline suites; no dependency installation or production API calls."""
from pathlib import Path
import shutil
import subprocess
import sys


def main():
    here = Path(__file__).resolve().parent
    node = shutil.which('node')
    if not node:
        print('TESTS_FAILED:NodeUnavailable')
        return 1
    commands = [
        [node, str(here / 'regression-tests.js')],
        [sys.executable, '-B', str(here / 'mapping-update-tests.py')],
        [sys.executable, '-B', str(here / 'manager-transaction-tests.py')],
        [sys.executable, '-B', str(here / 'portable-interface-tests.py')],
        [sys.executable, '-B', str(here / 'connectivity-tests.py')],
    ]
    failed = 0
    for command in commands:
        result = subprocess.run(command, cwd=here, capture_output=True, text=True, encoding='utf8', timeout=120)
        # Child scripts output synthetic case labels and exception types only.
        print(result.stdout.rstrip())
        if result.returncode:
            failed += 1
            print('SUITE_FAILED:' + Path(command[-1]).name)
    print('OFFLINE_SUITES:' + str(len(commands) - failed) + '/' + str(len(commands)))
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
