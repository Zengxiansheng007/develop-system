"""Single-run adoption transactions; recovery material has no execution authority."""
import copy
import hashlib
import json
import os
from pathlib import Path
import uuid


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def encode(value):
    return json.dumps(value, ensure_ascii=False, indent=2).encode('utf-8')


def bound_file(reference):
    if not isinstance(reference, dict) or set(reference) != {'path', 'sha256'}:
        raise ValueError('Original version or authorization needs an exact path/hash reference')
    path = Path(reference['path'])
    if not path.is_absolute() or not path.is_file() or digest(path.read_bytes()) != reference['sha256']:
        raise ValueError('Bound version or authorization changed or is unavailable')
    return {'path': str(path.resolve()), 'sha256': reference['sha256']}


def bind_adoption(preview, adoption):
    if not isinstance(adoption, dict) or set(adoption) not in (
            {'authorization', 'historical'}, {'authorization', 'historical', 'compatibility'}):
        raise ValueError('Explicit adoption authorization and original-version evidence required')
    result = copy.deepcopy(adoption)
    authorization = result['authorization']
    if (authorization.get('run_id') != preview['source']['run_id']
            or authorization.get('source_sha256') != preview['source']['sha256']
            or authorization.get('target_sha256') != preview['target']['sha256']):
        raise ValueError('Adoption authorization must bind this run, source and exact target')
    authorization['reference'] = bound_file(authorization['reference'])
    historical = result['historical']
    historical['basis'] = bound_file(historical['basis'])
    historical['engine'] = bound_file(historical['engine'])
    historical['files'] = [bound_file(item) for item in historical['files']]
    if historical['engine'] not in historical['files']:
        raise ValueError('Original engine must be retained in the historical manifest')
    original = preview['inventory']['snapshot']
    required = [{'path': original['registry_path'], 'sha256': original['registry_sha256']}]
    if original.get('engine_binding'):
        if bound_file(original['engine_binding']) != historical['engine']:
            raise ValueError('Historical engine disagrees with the original recorded engine')
    for frame in original['frames']:
        if 'engine_binding' in frame:
            required.append(frame['engine_binding'])
        required.append({'path': frame['skill_path'], 'sha256': frame['skill_sha256']})
        required.extend(frame.get('resources', []))
    if any(bound_file(item) not in historical['files'] for item in required):
        raise ValueError('Historical manifest omits recorded engine/Skill/resource/registry versions')
    if 'compatibility' in result:
        from legacy_compat import bind
        result['compatibility'] = bind(preview, result['compatibility'])
    return result


def save_bytes(path, raw):
    with path.open('xb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    if path.read_bytes() != raw:
        raise OSError('Staged recovery bytes failed verification')
    return {'path': str(path), 'sha256': digest(raw)}


def replace_authority(store, raw, expected, recheck):
    temp = store.root / ('.migration-' + uuid.uuid4().hex + '.tmp')
    try:
        save_bytes(temp, raw)
        recheck()
        if store.path.read_bytes() != expected:
            raise ValueError('Source bytes changed inside the cutover window')
        os.replace(temp, store.path)
    finally:
        if temp.is_file():
            temp.unlink()
    if store.path.read_bytes() != raw:
        raise OSError('Authority commit completed but readback differs; inspect before any retry')


def apply(store, preview):
    if not isinstance(preview, dict) or 'adoption' not in preview:
        raise ValueError('An explicitly authorized adoption preview is required')
    with store.locked():
        def recheck():
            actual = store.preview_migration(preview['target']['registry']['path'], preview['adoption'])
            if actual != preview:
                raise ValueError('Reviewed source revision/hash, target or adoption evidence changed')
        recheck()
        original = store.path.read_bytes()
        state = store.read()
        if {'control', 'restrictions', 'selected_skills', 'migration'} & state.keys():
            raise ValueError('Existing legacy extensions need explicit mapping; conversion cannot overwrite them')
        if 'compatibility' not in preview['adoption'] and (any(f['status'] == 'running' for f in state['frames']) or (
                state['frames'] and state['status'] not in {'completed', 'cancelled'})):
            raise ValueError('Legacy continuing frames require explicit compatibility inventory; original state preserved')
        from workflow_state import load_registry, validate_plan, validate_control
        registry, registry_path, registry_sha = load_registry(preview['target']['registry']['path'])
        validate_plan(state['plan'], registry)
        identity = uuid.uuid4().hex
        recovery_dir = store.root / 'recovery' / identity
        recovery_dir.mkdir(parents=True, exist_ok=False)
        old = save_bytes(recovery_dir / 'original-state.bin', original)
        versions = []
        for i, reference in enumerate(preview['adoption']['historical']['files']):
            raw = Path(bound_file(reference)['path']).read_bytes()
            if digest(raw) != reference['sha256']:
                raise ValueError('Original version drifted while preserving its bytes')
            versions.append({'original': reference, 'saved': save_bytes(recovery_dir / (str(i) + '.bin'), raw)})
        changed = copy.deepcopy(state)
        changed.update(schema_version=4, revision=state['revision'] + 1,
            control=copy.deepcopy(preview['mapping']['control']), status=preview['mapping']['control']['status'],
            restrictions=[{**copy.deepcopy(item.get('basis', {})), **copy.deepcopy(item),
                           'restriction_id': identity + '-' + str(i)}
                          for i, item in enumerate(preview['mapping']['restrictions'])],
            registry=registry, registry_path=registry_path, registry_sha256=registry_sha, selected_skills={})
        validate_control(changed)
        changed['migration'] = {'id': identity, 'source': preview['source'], 'target': preview['target'],
            'authorization': preview['adoption']['authorization'], 'historical': preview['adoption']['historical'],
            'original_registry': copy.deepcopy(state['registry'])}
        if 'compatibility' in preview['adoption']:
            from legacy_compat import inventory
            changed['migration']['compatibility'] = inventory(preview)
        raw = encode(changed)
        recovery = {'format': 'develop-system-recovery-v1', 'id': identity,
            'source': preview['source'], 'target': preview['target'], 'original_state': old,
            'adopted': {'revision': changed['revision'], 'payload_sha256': digest(raw)}, 'versions': versions}
        reference = save_bytes(recovery_dir / 'recovery.json', encode(recovery))
        # Bind the receipt in authority; its payload digest excludes only this separately bound reference.
        changed['migration']['recovery'] = reference
        raw = encode(changed)
        replace_authority(store, raw, original, recheck)
        return {'state': store.read(), 'recovery': recovery, 'receipt': reference}


def rollback(store, revision, receipt):
    with store.locked():
        current = store.path.read_bytes()
        state = store.read()
        migration = state.get('migration')
        if state['schema_version'] != 4 or not migration or state['revision'] != revision:
            raise ValueError('Rollback requires the exact adopted revision and migration identity')
        receipt = bound_file(receipt)
        if receipt != migration.get('recovery') or revision != migration['source']['revision'] + 1:
            raise ValueError('Recovery reference or original adoption revision does not match authority')
        payload = copy.deepcopy(state)
        del payload['migration']['recovery']
        if encode(state) != current:
            raise ValueError('Authoritative bytes changed after adoption')
        recovery = json.loads(Path(receipt['path']).read_bytes())
        if (recovery.get('format') != 'develop-system-recovery-v1' or recovery['id'] != migration['id']
                or recovery['source'] != migration['source'] or recovery['target'] != migration['target']
                or recovery['adopted'] != {'revision': revision, 'payload_sha256': digest(encode(payload))}):
            raise ValueError('New facts or mismatched recovery material prevent rollback')
        if [v['original'] for v in recovery['versions']] != migration['historical']['files']:
            raise ValueError('Original version manifest does not match the adopted binding')
        original = Path(bound_file(recovery['original_state'])['path']).read_bytes()
        if digest(original) != migration['source']['sha256']:
            raise ValueError('Recovery does not contain the original state bytes')
        restored = json.loads(original)
        if (restored['schema_version'] != 3 or restored['run_id'] != store.run_id
                or Path(restored['project_root']).resolve() != store.project
                or restored['revision'] != migration['source']['revision']):
            raise ValueError('Original state identity/version is not restorable')
        def recheck():
            from migration_preview import target_binding
            from workflow_state import SKILL_ROOT
            target, _ = target_binding(SKILL_ROOT, migration['target']['registry']['path'])
            if target != migration['target']:
                raise ValueError('Adopted target version changed; file and state restoration are separate')
            bound_file(receipt)
            bound_file(recovery['original_state'])
            for item in recovery['versions']:
                actual = bound_file(item['original'])
                saved = bound_file(item['saved'])
                if actual['sha256'] != saved['sha256']:
                    raise ValueError('Saved original version differs from the preserved binding')
        recheck()
        restoration = {'format': 'develop-system-restoration-v1', 'migration_id': migration['id'],
            'run_id': store.run_id, 'from_revision': revision, 'from_sha256': digest(current),
            'to_revision': restored['revision'], 'to_sha256': digest(original),
            'engine': migration['historical']['engine'],
            'effect': 'Conditional restoration intent; authoritative state bytes determine whether committed'}
        path = Path(receipt['path']).parent / ('restoration-' + uuid.uuid4().hex + '.json')
        reference = save_bytes(path, encode(restoration))
        replace_authority(store, original, current, recheck)
        return {'state': store.read(), 'engine': migration['historical']['engine'], 'restoration': reference}
