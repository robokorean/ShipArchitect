"""Read-only, dependency-free Python source quality inventory.

Usage: python tools/audit_code.py --output docs/CODE_AUDIT_V061.json
"""
from __future__ import annotations
import argparse
import ast
import json
from pathlib import Path
from collections import Counter


def audit(root):
    root = Path(root)
    modules=[]
    duplicate_function_names=[]
    long_functions=[]
    for path in sorted(root.rglob('*.py')):
        if '__pycache__' in path.parts:continue
        relative=path.relative_to(root).as_posix()
        text=path.read_text(encoding='utf-8-sig')
        lines=text.splitlines()
        try:
            tree=ast.parse(text,filename=relative)
            compile(tree,relative,'exec')
            syntax=None
        except (SyntaxError, ValueError) as exc:
            syntax=str(exc)
            tree=None
        functions=[];imports=[];decls=[]
        if tree:
            for node in ast.walk(tree):
                if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)):
                    span=int(getattr(node,'end_lineno',node.lineno)-node.lineno+1)
                    item={'name':node.name,'line':node.lineno,'lines':span}
                    functions.append(item)
                    if span>100:long_functions.append({'file':relative,**item})
                elif isinstance(node,ast.Import):
                    imports.extend(x.name for x in node.names)
                elif isinstance(node,ast.ImportFrom):
                    imports.append(('.'*node.level)+(node.module or ''))
            for node in tree.body:
                if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef)):
                    decls.append(node.name)
            repeated=[name for name,count in Counter(decls).items() if count>1]
            if repeated:duplicate_function_names.append({'file':relative,'names':repeated})
        modules.append({'path':relative,'lines':len(lines),'blank_lines':sum(not x.strip() for x in lines),
                        'function_count':len(functions),'max_function_lines':max([f['lines'] for f in functions],default=0),
                        'imports':sorted(set(imports)),'syntax_error':syntax})
    return {'python_files':len(modules),'total_lines':sum(x['lines'] for x in modules),
            'syntax_errors':[{'file':x['path'],'error':x['syntax_error']} for x in modules if x['syntax_error']],
            'files_by_length':sorted(modules,key=lambda x:-x['lines']),
            'functions_over_100_lines':sorted(long_functions,key=lambda x:-x['lines']),
            'repeated_top_level_names':duplicate_function_names,
            'note':'정적 점검입니다. 실행 중 오류, 모듈 간 설계 품질, 코드 중복을 완전히 판별하지는 못합니다.'}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    project=Path(__file__).resolve().parents[1]
    result=audit(project/'src')
    text=json.dumps(result,indent=2,ensure_ascii=False)
    if args.output:
        path=args.output if args.output.is_absolute() else project/args.output
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(text+'\n',encoding='utf-8')
        print('Saved',path)
    else:print(text)
    return 1 if result['syntax_errors'] or result['repeated_top_level_names'] else 0


if __name__=='__main__':raise SystemExit(main())
