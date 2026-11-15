"""Optional windowed entry point for installed Python on Windows."""
from pathlib import Path
import runpy

runpy.run_path(str(Path(__file__).resolve().with_name('launch.py')),run_name='__main__')
