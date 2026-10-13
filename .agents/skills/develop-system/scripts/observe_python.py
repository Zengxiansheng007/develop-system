"""Execute a Python script/module and observe code sources inside declared source ranges."""
import hashlib
import json
from pathlib import Path
import runpy
import sys


def python_invocation(command):
    """One grammar shared by the real interpreter launch and observed target."""
    flags=[]
    target=command[1:]
    while target:
        if target[0] in {'-B','-u'}:
            flags.append(target[0]);target=target[1:]
        elif target[:2]==['-X','utf8']:
            flags.extend(target[:2]);target=target[2:]
        else:
            break
    if not target or (target[0].startswith('-') and target[0]!='-m') or (target[0]=='-m' and len(target)<2):
        raise ValueError('Managed Python observation supports script or -m with -B/-u/-X utf8 only')
    return flags,target


def main():
    request_path,output_path=sys.argv[1:3]
    request=json.loads(Path(request_path).read_text(encoding='utf-8'))
    # -B prevents writes but still reads old .pyc. Isolate cache lookup so the
    # observed file hash describes the source actually compiled for this check.
    sys.pycache_prefix=str(Path(request_path).parent/'fresh-bytecode')
    _,target=python_invocation(request['command'])
    roots=[Path(p).resolve() for p in request['source_roots']]
    loaded={}
    entry=None
    def observe(event,args):
        nonlocal entry
        if event!='exec':return
        name=args[0].co_filename
        if name.startswith('<'):return
        path=Path(name).resolve()
        if not path.is_file():return
        if str(path)==request['entry']:entry=str(path)
        if any(path.is_relative_to(root) for root in roots):
            loaded[str(path)]=hashlib.sha256(path.read_bytes()).hexdigest()
    try:
        if target[0]=='-m':
            sys.argv=target[1:]
            sys.path.insert(0,str(Path.cwd()))
            sys.addaudithook(observe)
            runpy.run_module(target[1],run_name='__main__',alter_sys=True)
        else:
            script=Path(target[0]).resolve()
            sys.argv=[str(script),*target[1:]]
            sys.path.insert(0,str(script.parent))
            sys.addaudithook(observe)
            runpy.run_path(str(script),run_name='__main__')
    finally:
        Path(output_path).write_text(json.dumps({'adapter':'python','entry':entry,
            'loaded':[{'path':p,'sha256':s} for p,s in sorted(loaded.items())]}),encoding='utf-8')


if __name__=='__main__':main()
