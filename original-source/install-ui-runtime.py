"""Install the desktop UI build dependencies in this project's own virtualenv."""
import json
from pathlib import Path
import platform
import subprocess
import sys
import venv

platform._wmi = None


def main():
    root = Path(__file__).resolve().parent
    environment = root / '.venv-qt'
    venv.EnvBuilder(system_site_packages=True, with_pip=False).create(environment)
    python = environment / 'Scripts' / 'python.exe'
    packages = ['PySide6-Essentials==6.11.2', 'QtAwesome==1.4.2']
    arguments = ['pip', '--isolated', 'install', '--disable-pip-version-check',
                 '--index-url', 'https://pypi.org/simple', *packages]
    code = ('import platform; platform._wmi=None; import runpy,sys; '
            'sys.argv=' + repr(arguments) + '; runpy.run_module("pip",run_name="__main__")')
    installed = subprocess.run([str(python), '-B', '-c', code], capture_output=True, timeout=240,
                               creationflags=subprocess.CREATE_NO_WINDOW)
    # Pip diagnostics can include machine environment; never print the raw output.
    print(json.dumps({'projectEnvironment': str(environment), 'requestedPackages': packages,
                      'installed': installed.returncode == 0, 'returnCode': installed.returncode}))
    return installed.returncode


if __name__ == '__main__':
    sys.exit(main())
