"""Runtime-only distribution. The allowlist cannot accumulate old development artefacts."""
from pathlib import Path
import argparse,zipfile,ast,json,hashlib
ROOT=Path(__file__).resolve().parents[1]
RUNTIME_FILES=('START_WINDOWS.cmd','START_GUI.vbs','run.ps1','launch.py','requirements.txt','README.md','RUN_WINDOWS.md')

def build(destination):
    files=[ROOT/n for n in RUNTIME_FILES]
    files += sorted(p for p in (ROOT/'src').rglob('*') if p.is_file() and '__pycache__' not in p.parts and not any(x.startswith('.') for x in p.relative_to(ROOT/'src').parts) and p.suffix in ('.py','.json','.png','.svg','.ico','.qss','.ttf','.otf'))
    for p in files:
        if not p.is_file():raise ValueError('Runtime file missing: '+p.name)
        if p.suffix=='.py':compile(ast.parse(p.read_text(encoding='utf-8-sig')),str(p),'exec')
    destination=Path(destination);destination.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(destination,'w',zipfile.ZIP_DEFLATED,compresslevel=7) as z:
        for p in files:
            info=zipfile.ZipInfo('ShipArchitect/'+p.relative_to(ROOT).as_posix(),date_time=(2026,11,10,0,0,0));info.external_attr=0o644<<16
            data=p.read_bytes()
            if p.suffix in ('.cmd','.ps1','.vbs'):
                data=data.replace(b'\r\n',b'\n').replace(b'\n',b'\r\n')
            elif p.suffix in ('.py','.json','.md','.txt','.svg','.qss'):
                data=data.replace(b'\r\n',b'\n')
            z.writestr(info,data,compress_type=zipfile.ZIP_DEFLATED,compresslevel=7)
    with zipfile.ZipFile(destination) as z:
        assert z.testzip() is None and len(z.namelist())==len(set(z.namelist()))
        assert not any('/'+name+'/' in n for n in z.namelist() for name in ('tests','tools','docs','legacy','private_dev','examples'))
    result={'filename':destination.name,'bytes':destination.stat().st_size,'files':len(files),'sha256':hashlib.sha256(destination.read_bytes()).hexdigest()};print(json.dumps(result));return result
if __name__=='__main__':
    a=argparse.ArgumentParser();a.add_argument('--output',type=Path,default=ROOT/'dist/ShipArchitect_v0.9.3.4.zip');args=a.parse_args();build(args.output)
