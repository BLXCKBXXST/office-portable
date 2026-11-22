"""Office Portable build orchestration. Uses only Python's standard library."""
from __future__ import annotations
import argparse, concurrent.futures, hashlib, io, json, os, pathlib, re, shutil
import subprocess, tarfile, time, tomllib, urllib.error, urllib.parse, urllib.request, zipfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
WORK = ROOT / '.build'
TITLES = {'wordcraft':'Word', 'gridcraft':'Excel', 'deckcraft':'PowerPoint', 'pdfcraft':'PDF'}
SECTIONS = ('Settings','User','Cache','Logs','Recovery','Runtime','Temp')
BASE = json.loads((ROOT/'base-versions.json').read_text(encoding='utf-8'))
ALLOWED_RESOURCE = re.compile(r'^(?:LICENSE-[^/]+|OFL-[^/]+|NOTICE)$')
MODELS = {'ATTRIBUTION.txt','text-detection.onnx','text-recognition.onnx','text-detection.onnx.LICENCE.txt','text-recognition.onnx.LICENCE.txt'}

class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        result = super().redirect_request(req,fp,code,msg,headers,newurl)
        if result and urllib.parse.urlparse(req.full_url).netloc != urllib.parse.urlparse(newurl).netloc:
            result.remove_header('Authorization')
        return result
OPENER = urllib.request.build_opener(SafeRedirect())

def fetch(url: str) -> bytes:
    if urllib.parse.urlparse(url).scheme != 'https': raise ValueError('HTTPS required')
    headers={'User-Agent':'Office-Portable-build','Accept':'application/vnd.github+json'}
    token=os.environ.get('GH_TOKEN') or os.environ.get('GITHUB_TOKEN')
    if token and urllib.parse.urlparse(url).netloc=='api.github.com': headers['Authorization']='Bearer '+token
    for attempt in range(4):
        try:
            with OPENER.open(urllib.request.Request(url,headers=headers),timeout=90) as response: return response.read()
        except urllib.error.HTTPError as error:
            if error.code not in (429,500,502,503,504) or attempt==3: raise
        except (OSError,TimeoutError):
            if attempt==3: raise
        time.sleep(2*(attempt+1))
    raise RuntimeError('Download failed')

def api(path): return json.loads(fetch('https://api.github.com/'+path))
def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()
def run(args, *, cwd=ROOT):
    result=subprocess.run([str(a) for a in args],cwd=cwd,capture_output=True,text=True,encoding='utf-8',errors='replace')
    if result.returncode:
        print(result.stderr[-6000:]);print(result.stdout[-2000:])
        raise subprocess.CalledProcessError(result.returncode,args)
    return result.stdout
def clean_parts(name):
    path=pathlib.PurePosixPath(name.replace('\\','/'))
    if path.is_absolute() or any(p in ('..','.') or ':' in p for p in path.parts): raise ValueError('Unsafe archive path: '+name)
    return path.parts
def untar(data, destination):
    destination.mkdir(parents=True,exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(data)) as archive:
        for member in archive:
            parts=clean_parts(member.name)[1:]
            if not parts or not member.isfile(): continue
            target=destination.joinpath(*parts);target.parent.mkdir(parents=True,exist_ok=True)
            target.write_bytes(archive.extractfile(member).read())
def source_commit(app, tag):
    obj=api('repos/storytold/'+app+'/git/ref/tags/'+urllib.parse.quote(tag,safe=''))['object']
    for _ in range(4):
        if obj['type']=='commit':
            if not re.fullmatch('[0-9a-f]{40}',obj['sha']): raise ValueError('Invalid source commit')
            return obj['sha']
        if obj['type']!='tag': break
        obj=api('repos/storytold/'+app+'/git/tags/'+obj['sha'])['object']
    raise ValueError('Release tag must resolve to a commit')
def release(app):
    value=api('repos/storytold/'+app+'/releases/latest');tag=value['tag_name']
    if value['draft'] or value['prerelease'] or not re.fullmatch(r'v?\d+\.\d+\.\d+',tag): raise ValueError('Stable release required: '+app)
    assets=[a for a in value['assets'] if a['name'].endswith('-windows-x64-portable.zip')]
    if len(assets)!=1: raise ValueError('Expected one Windows x64 portable archive: '+app)
    asset=assets[0];digest=asset.get('digest','')
    if not re.fullmatch(r'sha256:[0-9a-f]{64}',digest): raise ValueError('Missing GitHub asset SHA256: '+app)
    return {'tag':tag,'commit':source_commit(app,tag),'releaseId':value['id'],'asset':{'name':asset['name'],'url':asset['browser_download_url'],'sha256':digest[7:]}}
def recipe_digest():
    digest=hashlib.sha256()
    for folder in ('ci','patches','overlay','office-setup','.github/workflows'):
        for p in sorted((ROOT/folder).rglob('*')):
            if p.is_file() and '__pycache__' not in p.parts:
                digest.update(p.relative_to(ROOT).as_posix().encode());digest.update(p.read_bytes())
    for filename in ('base-versions.json','USER-GUIDE.txt','LICENSE-MIT','LICENSE-APACHE'):digest.update((ROOT/filename).read_bytes())
    return digest.hexdigest()
def discover(force=False):
    WORK.mkdir(exist_ok=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        sources=dict(zip(TITLES,pool.map(release,TITLES)))
    state={'schema':1,'sources':sources,'recipeDigest':recipe_digest(),'recipeCommit':os.environ.get('GITHUB_SHA','local')}
    previous=None;repo=os.environ.get('GITHUB_REPOSITORY')
    if repo:
        try:
            own=api('repos/'+repo+'/releases/latest')
            asset=next((a for a in own['assets'] if a['name']=='upstream-versions.json'),None)
            if asset: previous=json.loads(fetch(asset['browser_download_url']))
        except urllib.error.HTTPError as e:
            if e.code!=404: raise
    changed=force or previous is None or previous.get('sources')!=sources or previous.get('recipeDigest')!=state['recipeDigest']
    (WORK/'state.json').write_text(json.dumps(state,indent=2),encoding='utf-8')
    if os.environ.get('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'],'a',encoding='utf-8') as f:f.write('changed='+str(changed).lower()+'\n')
    print(json.dumps({'changed':changed,'versions':{a:m['tag'] for a,m in sources.items()}}))
def normalize(source):
    for p in source.rglob('*'):
        if p.is_file() and p.suffix in ('.rs','.toml','.lock','.md'):
            p.write_text(p.read_text(encoding='utf-8'),encoding='utf-8',newline='\n')
def prepare(app):
    state=json.loads((WORK/'state.json').read_text(encoding='utf-8'));meta=state['sources'][app]
    source=WORK/'source'/app
    if source.exists(): raise RuntimeError('Source directory already exists; use a fresh build workspace')
    untar(fetch('https://api.github.com/repos/storytold/'+app+'/tarball/'+meta['commit']),source);normalize(source)
    version=tomllib.loads((source/'Cargo.toml').read_text(encoding='utf-8'))['workspace']['package']['version']
    if meta['tag'].lstrip('v')!=version: raise ValueError('Tag / Cargo version mismatch')
    parent=source.parent;run(['git','init','--quiet',parent])
    for patch in ['apps.patch']+(['base-locks.patch'] if meta['tag']==BASE['upstream'][app] else []):
        args=['git','-c','core.autocrlf=false','apply','--include='+app+'/**',ROOT/'patches'/patch]
        run(args[:4]+['--check']+args[4:],cwd=parent);run(args,cwd=parent)
    overlay=ROOT/'overlay'/app
    if overlay.exists(): shutil.copytree(overlay,source,dirs_exist_ok=True)
    for p in list((source/'apps').rglob('*.rs'))+list((source/'crates').rglob('*.rs')):
        if re.search(r'include_(?:bytes|str)!\s*\(\s*"[^"\n]*brand/',p.read_text(encoding='utf-8')):
            raise ValueError('Restricted upstream brand artwork still referenced: '+str(p.relative_to(source)))
    if any(re.search(r'^craft-update\s*=',p.read_text(encoding='utf-8'),re.M) for p in source.rglob('Cargo.toml')):
        raise ValueError('Upstream updater dependency remains')
    workflow=(source/'.github/workflows/release.yml').read_text(encoding='utf-8')
    match=re.search(r'craft-fonts-ref:\s*[\"\']?([0-9a-f]{40})',workflow)
    if not match: raise ValueError('Missing pinned font revision')
    font_ref=match[1];font_path=WORK/'assets'/font_ref
    if not font_path.exists(): untar(fetch('https://api.github.com/repos/storytold/craft-fonts/tarball/'+font_ref),font_path)
    i18n=None
    if (source/'craft-i18n.rev').exists():
        revision=(source/'craft-i18n.rev').read_text().strip()
        if not re.fullmatch('[0-9a-f]{40}',revision): raise ValueError('Invalid i18n revision')
        i18n=WORK/'assets'/revision
        if not i18n.exists(): untar(fetch('https://api.github.com/repos/storytold/craft-i18n/tarball/'+revision),i18n)
        for p in i18n.rglob('*.tsv'):
            if 'locales' not in p.parts: continue
            rows=[]
            for line in p.read_text(encoding='utf-8').splitlines(True):
                if not any(v in line for v in ('Copyright','copyright','©','github.com','getartcraft.com')):
                    for old,new in [('WordCraft','Word'),('GridCraft','Excel'),('DeckCraft','PowerPoint'),('PdfCraft','PDF')]:line=line.replace(old,new)
                rows.append(line)
            p.write_text(''.join(rows),encoding='utf-8',newline='\n')
    config={'source':str(source),'fonts':str(font_path),'i18n':str(i18n) if i18n else '', 'version':version}
    (WORK/(app+'-config.json')).write_text(json.dumps(config,indent=2),encoding='utf-8')
    print('Prepared '+app+' '+meta['tag'])
def resources(app,target):
    meta=json.loads((WORK/'state.json').read_text())['sources'][app];blob=fetch(meta['asset']['url'])
    if hashlib.sha256(blob).hexdigest()!=meta['asset']['sha256']:raise ValueError('Official portable resource archive checksum mismatch')
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        names=z.namelist()
        for name in names:clean_parts(name)
        executables=[name for name in names if pathlib.PurePosixPath(name).name==app+'.exe']
        if len(executables)!=1:raise ValueError('Unexpected upstream archive layout')
        prefix=pathlib.PurePosixPath(executables[0]).parent
        for name in names:
            path=pathlib.PurePosixPath(name)
            try:relative=path.relative_to(prefix)
            except ValueError:continue
            if len(relative.parts)==1 and ALLOWED_RESOURCE.fullmatch(relative.name) or app=='pdfcraft' and len(relative.parts)==2 and relative.parts[0]=='models' and relative.name in MODELS:
                p=target.joinpath(*relative.parts);p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(z.read(name))
def stage(app):
    title=TITLES[app];target=WORK/'out'/title;target.mkdir(parents=True,exist_ok=True)
    mingw=pathlib.Path(os.environ['OFFICE_MINGW_BIN']);binary=WORK/'target/x86_64-pc-windows-gnu/release'
    for suffix in ('','-cli'):
        p=target/(title+suffix+'.exe');shutil.copy2(binary/(app+suffix+'.exe'),p)
        run([mingw/'strip.exe','--strip-unneeded',p])
        dlls=re.findall(r'DLL Name:\s*(\S+)',run([mingw/'objdump.exe','-p',p]))
        bad=[d for d in dlls if not d.lower().startswith(('api-ms-','ext-ms-')) and not (pathlib.Path(os.environ['WINDIR'])/'System32'/d).exists()]
        if bad:raise ValueError('Non-system DLL imports: '+str(bad))
    resources(app,target)
    (target/'portable.txt').write_text('Office Portable: native profile storage in Data beside the executable. Keep this marker.\n',encoding='utf-8')
    for part in SECTIONS:(target/'Data'/part).mkdir(parents=True,exist_ok=True)
    report={'app':app,'name':title,'tag':json.loads((WORK/'state.json').read_text())['sources'][app]['tag'],'cliHelp':bool(run([target/(title+'-cli.exe'),'--help'])),'files':{p.name:{'sha256':sha(p),'bytes':p.stat().st_size} for p in target.glob('*.exe')}}
    (target/'Build-info.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print('Staged '+title)
def package():
    state=json.loads((WORK/'state.json').read_text(encoding='utf-8'));dist=ROOT/'dist';dist.mkdir(exist_ok=True)
    package=WORK/'package/Office Portable';package.mkdir(parents=True)
    for title in TITLES.values():
        incoming=WORK/'incoming'/title
        if not incoming.exists():raise ValueError('Missing native build artifact: '+title)
        target=package/title;target.mkdir()
        for p in incoming.rglob('*'):
            relative=p.relative_to(incoming)
            if 'Data' in relative.parts or not p.is_file():continue
            q=target/relative;q.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,q)
        for part in SECTIONS:(target/'Data'/part).mkdir(parents=True,exist_ok=True)
    templates=WORK/'templates';templates.mkdir(exist_ok=True)
    run([package/'Word/Word-cli.exe','run','--save',templates/'Empty.docx'])
    run([package/'Excel/Excel-cli.exe','run','--out',templates/'Empty.xlsx','--quiet'])
    run([package/'PowerPoint/PowerPoint-cli.exe','run','--save',templates/'Empty.pptx'])
    run([package/'Word/Word-cli.exe','convert',templates/'Empty.docx',templates/'Empty.pdf'])
    for ext in ('docx','xlsx','pptx'):
        with zipfile.ZipFile(templates/('Empty.'+ext)) as z:
            if z.testzip() is not None:raise ValueError('Invalid blank template: '+ext)
    run([package/'Word/Word-cli.exe','inspect',templates/'Empty.docx'])
    run([package/'Excel/Excel-cli.exe','run','--in',templates/'Empty.xlsx','--quiet'])
    run([package/'PowerPoint/PowerPoint-cli.exe','run','--in',templates/'Empty.pptx'])
    # Verify the actual headless engine's default author, not just a source string.
    probe=WORK/'identity-probe.docx'
    run([package/'Word/Word-cli.exe','run','--cmd','text.insert={"text":"Office Portable verification"}','--cmd','select.text={"text":"Office Portable verification"}','--cmd','review.newComment={"text":"profile author probe"}','--save',probe])
    import xml.etree.ElementTree as ET
    with zipfile.ZipFile(probe) as z:comments=ET.fromstring(z.read('word/comments.xml'))
    ns='{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
    if not list(comments) or any(c.attrib.get(ns+'author')!='user' or c.attrib.get(ns+'initials','') for c in comments):raise ValueError('Neutral user / no-initials regression')
    pdf=json.loads(run([package/'PDF/PDF-cli.exe','info',templates/'Empty.pdf']))
    if pdf.get('pages')!=1 or pdf.get('warnings') or pdf.get('javascript'):raise ValueError('Invalid blank PDF template')
    compiler=pathlib.Path(os.environ['WINDIR'])/'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
    args=[compiler,'/nologo','/target:winexe','/optimize+','/win32manifest:'+str(ROOT/'office-setup/app.manifest'),'/out:'+str(package/'Office Setup.exe'),'/reference:System.Windows.Forms.dll','/reference:System.Drawing.dll']
    for title,ext in [('Word','docx'),('Excel','xlsx'),('PowerPoint','pptx'),('PDF','pdf')]:args.append('/resource:'+str(templates/('Empty.'+ext))+',CraftPortable.'+title+'.Template')
    args.append(ROOT/'office-setup/OfficeSetup.cs');run(args)
    ps=pathlib.Path(os.environ['WINDIR'])/'System32/WindowsPowerShell/v1.0/powershell.exe'
    test="$a=[Reflection.Assembly]::LoadFile('"+str(package/'Office Setup.exe').replace("'","''")+"'); $a.GetType('Program').GetMethod('SelfTest').Invoke($null,@())"
    result=run([ps,'-NoProfile','-STA','-Command',test])
    if 'PASS:' not in result:raise ValueError('Office Setup registry sandbox tests failed')
    print(result.strip())
    shutil.copy2(ROOT/'USER-GUIDE.txt',package/'Readme.txt')
    state['verification']={'portableMarkerTests':'passed','nativeCliSmokeTests':'passed','blankTemplates':'passed','officeSetupSandboxTests':'passed','systemDllImportsOnly':True,'profilesExcluded':True,'nativeGuiVisualTests':'not run on hosted CI'}
    (package/'Build-info.json').write_text(json.dumps(state,indent=2),encoding='utf-8')
    (dist/'upstream-versions.json').write_text(json.dumps(state,indent=2),encoding='utf-8')
    sums=[sha(p)+'  '+p.relative_to(package).as_posix() for p in sorted(package.rglob('*')) if p.is_file() and 'Data' not in p.relative_to(package).parts]
    (package/'SHA256SUMS.txt').write_text('\n'.join(sums)+'\n',encoding='ascii')
    archive=dist/'Office-Portable-Windows-x64.zip'
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as z:
        for p in sorted(package.rglob('*')):
            rel=p.relative_to(package)
            if 'Data' in rel.parts and p.is_file():continue
            z.write(p,p.relative_to(package.parent).as_posix())
    with zipfile.ZipFile(archive) as z:
        if z.testzip() is not None:raise ValueError('Distribution ZIP failed CRC validation')
        if any('/Data/' in n and not n.endswith('/') for n in z.namelist()):raise ValueError('A profile entered the distribution')
    (dist/'SHA256SUMS.txt').write_text('\n'.join(sha(p)+'  '+p.name for p in sorted(dist.iterdir()) if p.is_file() and p.name!='SHA256SUMS.txt')+'\n',encoding='ascii')
    notes=['Office Portable — Windows x64','', 'Неофициальные модифицированные сборки Craft; не Microsoft Office.','', '| Программа | Исходный проект | Версия |','|---|---|---|']
    for app,title in TITLES.items():notes.append('| '+title+' | '+app+' | '+state['sources'][app]['tag']+' |')
    notes+=['','Единая структура Data, имя user по умолчанию, нейтральный аватар Word, модуль оригинального обновления удалён. Office Setup добавляет ассоциации и четыре корректных шаблона в «Создать».','', 'Архив содержит пустые Data. При обновлении сохраните свои Settings, User и Recovery.','', 'Сборка без цифровой подписи оригинального издателя. Контрольные суммы и исходные изменения приложены.']
    (dist/'Release-notes.md').write_text('\n'.join(notes)+'\n',encoding='utf-8')
    print('Packaged '+str(archive))
def main():
    parser=argparse.ArgumentParser();parser.add_argument('command',choices=['discover','prepare','stage','package']);parser.add_argument('app',nargs='?',choices=list(TITLES));parser.add_argument('--force',action='store_true');args=parser.parse_args()
    if args.command=='discover':discover(args.force)
    elif args.command=='package':package()
    else:
        if args.app is None:parser.error('An application is required')
        (prepare if args.command=='prepare' else stage)(args.app)
if __name__=='__main__':main()
