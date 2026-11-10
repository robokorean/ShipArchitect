"""Rebuild the bundled vanilla registry from official 1.21.1 Mojang artifacts.
Requires Java 21 and network access ONLY when rebuilding. Normal app needs neither.
"""
import argparse,hashlib,json,subprocess,urllib.request
from pathlib import Path


def download(url,path,sha1=None):
    urllib.request.urlretrieve(url,path)
    if sha1 and hashlib.sha1(path.read_bytes()).hexdigest()!=sha1:raise ValueError('다운로드 SHA-1 불일치: '+path.name)
    return json.loads(path.read_text()) if path.suffix=='.json' else None


def build(cache,output):
    cache.mkdir(parents=True,exist_ok=True)
    m=download('https://piston-meta.mojang.com/mc/game/version_manifest_v2.json',cache/'manifest.json')
    info=next(v for v in m['versions'] if v['id']=='1.21.1');version=download(info['url'],cache/'version.json',info['sha1'])
    server=version['downloads']['server'];download(server['url'],cache/'server.jar',server['sha1'])
    assets=version['assetIndex'];index=download(assets['url'],cache/'assets.json',assets['sha1'])
    langhash=index['objects']['minecraft/lang/ko_kr.json']['hash'];lang=download('https://resources.download.minecraft.net/'+langhash[:2]+'/'+langhash,cache/'ko_kr.json',langhash)
    subprocess.run(['java','-DbundlerMainClass=net.minecraft.data.Main','-jar','server.jar','--reports','--output','generated'],cwd=cache,check=True)
    report=cache/'generated/reports/blocks.json';blocks=json.loads(report.read_text());compact={}
    for key,row in blocks.items():
        default=next(s for s in row['states'] if s.get('default'))
        compact[key]={'properties':row.get('properties',{}),'default':default.get('properties',{}),'ko':lang.get('block.'+key.replace(':','.'),key)}
    data={'minecraft_version':'1.21.1','source':'Official Mojang server 1.21.1 --reports; official ko_kr asset index 17','server_sha1':server['sha1'],'blocks_report_sha256':hashlib.sha256(report.read_bytes()).hexdigest(),'blocks':compact}
    output.parent.mkdir(parents=True,exist_ok=True);output.write_text(json.dumps(data,ensure_ascii=False,separators=(',',':')),encoding='utf-8');print('Verified vanilla blocks:',len(compact))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--cache',type=Path,default=Path('registry_build'));p.add_argument('--output',type=Path,default=Path(__file__).resolve().parents[1]/'src/ship_architect/data/vanilla_1_21_1.json');a=p.parse_args();build(a.cache.resolve(),a.output.resolve())
