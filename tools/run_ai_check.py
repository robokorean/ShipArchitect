"""Run the real Ollama check using the same private dependencies as START_WINDOWS.
Use the installed base Python: python tools/run_ai_check.py --source ship.schem
"""
import os,runpy,sys
from pathlib import Path
project=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(project))
from launch import prepare
root=(Path(os.environ['LOCALAPPDATA']) if os.environ.get('LOCALAPPDATA') else Path.home()/'.cache')/'ShipArchitect'/'packages'
sys.path.insert(0,str(prepare(project/'requirements.txt',root)))
script='verify_ai_materials.py' if '--materials' in sys.argv else 'verify_ai.py'
if '--materials' in sys.argv:sys.argv.remove('--materials')
runpy.run_path(str(project/'tools'/script),run_name='__main__')
