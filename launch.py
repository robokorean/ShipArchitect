"""Run with the installed Python; install dependencies in a private target directory.
No venv, ensurepip or copied python.exe is needed. Never change antivirus settings.
"""
from __future__ import annotations
import hashlib
import contextlib
import traceback
import os
from pathlib import Path
import platform
import runpy
import shutil
import subprocess
import sys
import tempfile


class Tee:
    def __init__(self,console,log):self.console,self.log=console,log
    def write(self,text):
        self.log.write(text);self.log.flush()
        return self.console.write(text) if self.console is not None else len(text)
    def flush(self):
        self.log.flush()
        if self.console is not None:self.console.flush()


def run_logged(command):
    with subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,errors='replace') as child:
        for line in child.stdout:print(line,end='',flush=True)
        return child.wait()


def package_path(root, requirements):
    abi=f'py{sys.version_info.major}{sys.version_info.minor}-{platform.machine().lower()}'
    digest=hashlib.sha256(requirements.read_bytes()).hexdigest()[:20]
    return root/abi/digest


def prepare(requirements, cache_root):
    if sys.version_info < (3,10):
        raise RuntimeError('Python 3.10 or newer is required.')
    if sys.prefix != sys.base_prefix:
        raise RuntimeError('Use the installed base Python, not .venv/Scripts/python.exe.')
    if 'veroot' in [p.lower() for p in Path(sys.executable).parts]:
        raise RuntimeError('V3 has redirected the base Python too. This launcher cannot resolve that environment. Send the startup log for diagnosis.')
    target=package_path(cache_root,requirements)
    marker=target/'.complete'
    if marker.is_file():
        return target
    probe=run_logged([sys.executable,'-I','-m','pip','--version'])
    if probe:
        raise RuntimeError('Base Python has no working pip. Repair this Python installation with the pip option enabled, then retry.')
    target.parent.mkdir(parents=True,exist_ok=True)
    staging=Path(tempfile.mkdtemp(prefix='install-',dir=target.parent))
    try:
        print('Installing private Ship Architect packages:',target,flush=True)
        result=run_logged([sys.executable,'-I','-m','pip','install','--disable-pip-version-check',
            '--target',str(staging),'-r',str(requirements)])
        if result:
            raise RuntimeError('Package installation failed. See the pip error above.')
        (staging/'.complete').write_text('ShipArchitect dependency target\n',encoding='utf-8')
        if target.exists():
            # A failed install from this launcher never publishes a target.
            # An existing incomplete target is left untouched for diagnosis.
            if marker.is_file():return target
            raise RuntimeError(f'Incomplete package target exists: {target}. Send the startup log.')
        staging.rename(target)
    finally:
        if staging.exists():shutil.rmtree(staging,ignore_errors=True)
    return target


def main():
    project=Path(__file__).resolve().parent
    local=os.environ.get('LOCALAPPDATA')
    root=Path(local)/'ShipArchitect'/'packages' if local else Path.home()/'.cache'/'ShipArchitect'/'packages'
    target=prepare(project/'requirements.txt',root)
    sys.path.insert(0,str(target))
    import PySide6,numpy,nbtlib,numba
    print('Python:',sys.executable,flush=True)
    print('Packages:',target,flush=True)
    print('PySide6:',PySide6.__version__,'NumPy:',numpy.__version__,'Numba:',numba.__version__,flush=True)
    if '--check' in sys.argv[1:]:return 0
    if '--gui' in sys.argv:sys.argv.remove('--gui')
    sys.path.insert(0,str(project/'src'))
    os.chdir(project)
    runpy.run_path(str(project/'src'/'main.py'),run_name='__main__')
    return 0


if __name__=='__main__':
    log_path=Path(__file__).resolve().parent/'startup.log'
    with log_path.open('w',encoding='utf-8') as log:
        with contextlib.redirect_stdout(Tee(sys.stdout,log)),contextlib.redirect_stderr(Tee(sys.stderr,log)):
            try:
                status=main()
            except Exception:
                traceback.print_exc()
                status=1
    raise SystemExit(status)
