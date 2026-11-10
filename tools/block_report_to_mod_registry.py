"""Convert an actual Java/NeoForge generated reports/blocks.json into an offline registry.
Only explicitly selected, independently verified plain decorations become placeable.
python tools/block_report_to_mod_registry.py blocks.json --output mod_blocks.json --decorative mydecor:panel
"""
import argparse,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from ship_architect.block_registry import validate_mod_registry


def convert(source,decorative):
    output={}
    if len(source)>10000:raise ValueError('레지스트리 블록 수 제한 초과')
    for key,row in source.items():
        if key.startswith('minecraft:'):continue
        default=next((s for s in row.get('states',[]) if s.get('default')),None)
        if default is None:raise ValueError('기본 상태가 없는 블록: '+key)
        safe=key in decorative
        output[key]={'properties':row.get('properties',{}),'default':default.get('properties',{}),
            'safe_full_cube':safe,'has_block_entity':not safe,'functional':not safe}
    missing=set(decorative)-set(output)
    if missing:raise ValueError('실제 레지스트리에 없는 ID: '+', '.join(sorted(missing)))
    data={'minecraft_version':'1.21.1','source':'Imported generated reports/blocks.json; decorative classification explicitly supplied by user','blocks':output}
    validate_mod_registry(data);return data

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('report',type=Path);parser.add_argument('--output',type=Path,required=True);parser.add_argument('--decorative',action='append',default=[])
    a=parser.parse_args()
    if a.report.stat().st_size>64_000_000:raise ValueError('보고서 크기 제한 초과')
    data=convert(json.loads(a.report.read_text(encoding='utf-8')),a.decorative)
    a.output.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8');print('등록된 모드 블록:',len(data['blocks']))
