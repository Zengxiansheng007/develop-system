"""Validate the extensible routing registry. No third-party dependencies."""

import json
import re
import sys
from pathlib import Path
from codex_runtime import resolve_skill


def required_closure(data, names, select, prerequisites=False):
    """Qualify only the requested call graph with the caller's actual source selector."""
    index = {item['id']: item for item in data['skills']}
    selected, active = {}, []
    def visit(name, helper=False):
        if name in active:
            raise ValueError('Required call cycle: ' + ' -> '.join([*active, name]))
        item = index.get(name)
        if not item or not item.get('enabled') or item.get('role') == 'controller':
            raise ValueError('Unavailable required capability: ' + ' -> '.join([*active, name]))
        if not isinstance(item.get('required_calls'), list):
            raise ValueError(name + ': missing required_calls declaration')
        source = selected.get(name) or select(name, item)
        if helper and not source.get('callable', True):
            raise ValueError(name + ': invocation policy forbids helper calls')
        if name in selected:
            return
        active.append(name)
        selected[name] = source
        for target in item['required_calls']:
            visit(target, True)
        if prerequisites:
            for target in item['requires']:
                visit(target)
        active.pop()
    for name in names:
        visit(name)
    return selected


def package_source(name, item, root):
    path = item.get('path', '../' + name + '/SKILL.md')
    return resolve_skill(name, explicit=Path(root) / path)


def validate(data, root, package=True):
    errors = []
    if not isinstance(data, dict):
        return ["Registry must be an object"]
    if data.get("schema_version") != 3:
        errors.append("Unsupported schema_version")
    if data.get("controller_policy") != "dispatch-only":
        errors.append("Controller must be dispatch-only")
    controller = data.get("controller")
    if controller != "develop-system" or data.get("return_to") != controller:
        errors.append("Controller and return_to must be develop-system")
    entrypoints = data.get("entrypoints", {})
    if entrypoints != {"develop-system": {"mode": "execute", "skill": "develop-system"}, "ask-matt": {"mode": "navigate", "skill": "ask-matt"}}:
        errors.append("Separate execute and navigate entrypoints are required")
    stages = data.get("stages", [])
    if not isinstance(stages, list) or not stages or any(not isinstance(s, str) for s in stages):
        return errors + ["stages must be a nonempty string array"]
    if len(set(stages)) != len(stages):
        errors.append("Duplicate stages")
    entries = data.get("skills", [])
    if not isinstance(entries, list):
        return errors + ["skills must be an array"]
    index = {}
    required = {"id", "stage", "role", "enabled", "return_to", "fallback", "when", "done_when", "requires", "required_calls", "outputs"}
    for item in entries:
        if not isinstance(item, dict):
            errors.append("Skill entry must be an object")
            continue
        skill_id = item.get("id")
        if not isinstance(skill_id, str) or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", skill_id):
            errors.append("Invalid skill id")
            continue
        if skill_id in index:
            errors.append(f"Duplicate skill: {skill_id}")
        index[skill_id] = item
        if skill_id == "ask-matt":
            errors.append("ask-matt cannot be an execution skill")
        missing = required - item.keys()
        if missing:
            errors.append(f"{skill_id}: missing {sorted(missing)}")
        if item.get("stage") not in stages:
            errors.append(f"{skill_id}: unknown stage")
        if item.get("role") not in {"controller", "entry", "primary", "auxiliary"}:
            errors.append(f"{skill_id}: invalid role")
        if item.get("role") == "controller" and skill_id != controller:
            errors.append(f"{skill_id}: extra controller")
        if not isinstance(item.get("enabled"), bool):
            errors.append(f"{skill_id}: enabled must be boolean")
        if item.get("return_to") != controller:
            errors.append(f"{skill_id}: invalid return target")
        if item.get("fallback") != "blocked":
            errors.append(f"{skill_id}: invalid fallback")
        for field in ("when", "done_when"):
            if not isinstance(item.get(field), str) or not item[field].strip():
                errors.append(f"{skill_id}: empty {field}")
        if "inline" in item:
            errors.append(f"{skill_id}: inline execution is forbidden for dispatch-only controller")
        for field in ("requires", "required_calls", "outputs", "next_candidates"):
            values = item.get(field, [])
            if not isinstance(values, list) or any(not isinstance(v, str) or not v for v in values):
                errors.append(f"{skill_id}: {field} must be a string array")
        if not item.get("outputs"):
            errors.append(f"{skill_id}: outputs cannot be empty")
        if "path" in item:
            path = item["path"]
            if not isinstance(path, str) or not path:
                errors.append(f"{skill_id}: invalid path")
            elif package and not (root / path).is_file():
                errors.append(f"{skill_id}: explicit skill path does not exist: {path}")
    if controller not in index or index[controller].get("role") != "controller" or index[controller].get("enabled") is not True:
        errors.append("Enabled controller entry is required")
    for skill_id, item in index.items() if package else []:
        for field in ("requires", "next_candidates"):
            values = item.get(field, [])
            if not isinstance(values, list):
                continue
            for target in values:
                if not isinstance(target, str):
                    continue
                if target not in index:
                    errors.append(f"{skill_id}: unknown {field} target {target}")
                elif item.get("enabled") and not index[target].get("enabled"):
                    errors.append(f"{skill_id}: enabled route points to disabled {target}")
                if target == controller:
                    errors.append(f"{skill_id}: use return_to for controller, not {field}")
    visited, active = set(), set()

    def visit(skill_id):
        if skill_id in active:
            errors.append(f"Dependency cycle at {skill_id}")
            return
        if skill_id in visited:
            return
        active.add(skill_id)
        dependencies = index[skill_id].get("requires", [])
        if isinstance(dependencies, list):
            for target in dependencies:
                if isinstance(target, str) and target in index:
                    visit(target)
        active.remove(skill_id)
        visited.add(skill_id)

    for skill_id in index if package else []:
        visit(skill_id)
    if package:
        for skill_id, item in index.items():
            if item.get('enabled') and item.get('required_calls'):
                try:
                    required_closure(data, [skill_id], lambda name, entry: package_source(name, entry, root))
                except (OSError, ValueError, KeyError, TypeError) as exc:
                    errors.append(skill_id + ': ' + str(exc))
    for field in ("aliases", "default_flows"):
        mapping = data.get(field, {})
        if not isinstance(mapping, dict):
            errors.append(f"{field} must be an object")
            continue
        for name, value in mapping.items():
            if field == "aliases" and name == "ask-matt":
                errors.append("ask-matt cannot alias an execution entry")
            targets = [value] if field == "aliases" else value
            if not isinstance(targets, list) or not targets:
                errors.append(f"{field}.{name}: invalid targets")
                continue
            for target in targets:
                if not isinstance(target, str) or (package and target not in index):
                    errors.append(f"{field}.{name}: unknown target")
                elif package and not index[target].get("enabled"):
                    errors.append(f"{field}.{name}: disabled target {target}")
                elif field == "default_flows" and target == controller:
                    errors.append(f"{field}.{name}: controller cannot be a leaf step")
    return errors


def main():
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[1] / "registry.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        errors = validate(data, path.resolve().parent)
    except (OSError, ValueError) as exc:
        print(f"INVALID: {exc}")
        return 1
    if errors:
        print("\n".join(f"INVALID: {error}" for error in errors))
        return 1
    print(f"VALID: {len(data['skills'])} skills; {len(data['default_flows'])} flow templates; all return to develop-system")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
