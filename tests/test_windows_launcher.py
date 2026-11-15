import io
import runpy
from pathlib import Path


def test_legacy_windows_console_encoding_does_not_interrupt_startup():
    tee=runpy.run_path(str(Path(__file__).parents[1]/'launch.py'))['Tee']
    log=io.StringIO()
    destination=io.BytesIO()
    console=io.TextIOWrapper(destination,encoding='cp1252')
    text='Install to 한글 경로\n'
    stream=tee(console,log)
    assert stream.write(text)==len(text)
    stream.flush()
    assert log.getvalue()==text
    assert destination.getvalue().startswith(b'Install to ')
    assert b'\\ud55c' in destination.getvalue()
    assert tee(None,io.StringIO()).write(text)==len(text)
