"""Build the generic bridge manager; vendor assets and user config stay separate."""
import argparse
from pathlib import Path
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--work', type=Path)
    args = parser.parse_args()
    source = Path(__file__).resolve().parent
    output = args.output.resolve()
    work = (args.work or source / '.build-feiniao').resolve()
    output.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)
    command = [sys.executable, '-m', 'PyInstaller', '--onefile', '--console',
               '--noconfirm', '--name', 'FeiniaoClashBridge', '--distpath', str(output),
               '--workpath', str(work / 'work'), '--specpath', str(work / 'spec'),
               '--icon', str(source / 'assets' / 'app.ico'),
               str(source / 'feiniao_bridge.py')]
    result = subprocess.run(command, cwd=source)
    return result.returncode


if __name__ == '__main__':
    raise SystemExit(main())
