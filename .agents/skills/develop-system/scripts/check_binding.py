"""Shared managed-check identities; authorization stays on the dispatched frame."""
import hashlib
import json
from pathlib import Path
import subprocess
import uuid


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def git_identity(root):
    root=Path(root).resolve(strict=True)
    def git(*args):
        try:
            return subprocess.check_output(['git','-C',str(root),*args],stderr=subprocess.PIPE,
                text=True,encoding='utf-8').strip()
        except subprocess.CalledProcessError as exc:
            raise ValueError('Actual Git worktree required') from exc
    top=Path(git('rev-parse','--show-toplevel')).resolve()
    if top != root:raise ValueError('Exact Git worktree root required')
    return {'root':str(root),'common_dir':str(Path(git('rev-parse','--path-format=absolute','--git-common-dir')).resolve()),
            'git_dir':str(Path(git('rev-parse','--absolute-git-dir')).resolve())}


def relative(root,value):
    if not isinstance(value,str) or not value or Path(value).is_absolute():
        raise ValueError('Explicit relative candidate range required')
    path=(root/value).resolve()
    if not path.is_relative_to(root):raise ValueError('Candidate range escapes worktree')
    return path


def bind_worktree(project,run_id,frame_id,input_version,declaration):
    if not isinstance(declaration,dict) or not isinstance(declaration.get('authorization_reference'),str) or not declaration['authorization_reference'].strip():
        raise ValueError('Existing task authorization reference required')
    project_id=git_identity(project)
    worktree=git_identity(declaration['worktree'])
    if project_id['common_dir'] != worktree['common_dir']:
        raise ValueError('Worktree belongs to another repository')
    roots=declaration.get('execution_roots')
    ranges=declaration.get('source_ranges')
    if not isinstance(roots,list) or not roots or not isinstance(ranges,list) or not ranges:
        raise ValueError('Explicit execution and source scopes required')
    execution=[]
    for item in roots:
        path=Path(item).resolve(strict=True)
        if not path.is_dir() or not any(path.is_relative_to(Path(i['root'])) for i in (project_id,worktree)):
            raise ValueError('Execution root outside assigned project/worktree')
        execution.append(str(path))
    source=[relative(Path(worktree['root']),p).relative_to(worktree['root']).as_posix() for p in ranges]
    return {'binding_id':uuid.uuid4().hex,'run_id':run_id,'frame_id':frame_id,'input_version':input_version,
            'authorization_reference':declaration['authorization_reference'],'project':project_id,'worktree':worktree,
            'execution_roots':execution,'source_ranges':source}


def validate_binding(binding,cwd):
    if git_identity(binding['project']['root']) != binding['project'] or git_identity(binding['worktree']['root']) != binding['worktree']:
        raise ValueError('Repository or worktree identity changed')
    path=Path(cwd).resolve()
    if not any(path.is_relative_to(Path(p)) for p in binding['execution_roots']):
        raise ValueError('Check cwd outside explicit execution scope')
    try:
        actual=Path(subprocess.check_output(['git','-C',str(path),'rev-parse','--show-toplevel'],
            stderr=subprocess.PIPE,text=True,encoding='utf-8').strip()).resolve()
    except subprocess.CalledProcessError as exc:
        raise ValueError('Check cwd has no authorized Git execution scope') from exc
    if str(actual) not in {binding['project']['root'],binding['worktree']['root']}:
        raise ValueError('Check cwd is an unassigned worktree outside execution scope')


def authorized_source(root,source_ranges,path):
    """Authorize the actual target before any candidate/observer byte collection."""
    target=path.resolve(strict=True)
    # Ranges were canonicalized at dispatch. Do not resolve them again: replacing
    # a permitted directory with a link must not move the original authorization.
    if not any(target.is_relative_to(root/p) for p in source_ranges):
        raise ValueError('Candidate outside authorized source scope')
    for name in (path,target):
        if not name.is_relative_to(root):raise ValueError('Candidate escapes worktree')
        parts=[p.lower() for p in name.relative_to(root).parts]
        if any(p in {'.git','.env','credentials','secrets.json','credentials.json'} or p.startswith('.env.')
               for p in parts) or name.suffix.lower() in {'.pem','.key','.pfx'}:
            raise ValueError('Sensitive or repository metadata excluded from candidate ranges')
    return target


def candidate_snapshot(binding,selection):
    root=Path(binding['worktree']['root'])
    if not isinstance(selection,dict):raise ValueError('Professional candidate selection required')
    ranges=selection.get('ranges')
    if not isinstance(ranges,list) or not ranges:raise ValueError('Related candidate ranges required')
    files={}
    for item in ranges:
        if item.get('role') not in {'source','test','config','dependency'}:raise ValueError('Candidate role required')
        start=relative(root,item['path'])
        authorized_source(root,binding['source_ranges'],root/item['path'])
        if not start.exists():raise ValueError('Related range missing')
        for path in ([start] if start.is_file() else sorted(start.rglob('*'))):
            if not path.is_file() or '__pycache__' in path.parts or path.suffix=='.pyc':continue
            target=authorized_source(root,binding['source_ranges'],path)
            name=path.relative_to(root).as_posix()
            evidence={'path':name,'sha256':hashlib.sha256(target.read_bytes()).hexdigest(),'role':item['role']}
            if name in files and files[name]!=evidence:raise ValueError('Overlapping candidate roles')
            files[name]=evidence
    loaded=selection.get('loaded')
    entry=selection.get('entry')
    if not isinstance(loaded,list) or not loaded or entry not in files or any(p not in files for p in loaded):
        raise ValueError('Entry and expected loaded sources must belong to candidate')
    result={'selection':selection,'files':[files[p] for p in sorted(files)]}
    result['candidate_id']=digest(result)
    return result


def validate_managed(record,binding):
    if record.get('schema_version') != 2 or record.get('context') != 'managed' or not binding or record.get('binding') != binding:
        raise ValueError('Current checks require their original managed v2 frame binding')
    validate_binding(binding,record['cwd'])
    if not isinstance(record.get('candidate'),dict):
        raise ValueError('Managed check did not collect an authorized candidate')
    current=candidate_snapshot(binding,record['candidate']['selection'])
    if current != record['candidate'] or record.get('candidate_after') != current:
        raise ValueError('Candidate changed or incomplete')
    observation=record.get('observation',{})
    root=Path(binding['worktree']['root'])
    observed={p['path']:p['sha256'] for p in observation.get('loaded',[])}
    for file in current['files']:
        path=str(root/file['path'])
        if file['path'] in current['selection']['loaded'] and observed.get(path) != file['sha256']:
            raise ValueError('Actual loaded source does not match candidate')
    declared={str(root/f['path']):f['sha256'] for f in current['files']}
    if any(path not in declared or declared[path]!=sha for path,sha in observed.items()):
        raise ValueError('Loaded related source omitted from candidate')
    if observation.get('adapter') != 'python' or observation.get('entry') != str(root/current['selection']['entry']):
        raise ValueError('Actual entry does not match candidate')
    return True
