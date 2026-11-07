"""Relax Odin's single-declaration control-block error into a warning."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import subprocess

from .compiler import Source
from .sourcemap import Origin


UNUSED = re.compile(r"^'([^']+)' declared but not used$")
UNUSED_VET_FLAGS = {'-vet', '-vet-unused', '-vet-unused-variables', '-vet-unused-procedures'}


def run(project, command, **options):
    warnings = []
    while True:
        result = subprocess.run(command, **options)
        if result.returncode <= 0 or UNUSED_VET_FLAGS.intersection(command):
            break
        try:
            errors = json.loads(result.stderr).get('errors', [])
        except (ValueError, TypeError):
            break
        adapter = project.diagnostic_adapter()
        files, current, remaining = {}, [], []
        for error in errors:
            match = UNUSED.fullmatch(error['msgs'][0]) if error.get('msgs') else None
            pos = error.get('pos') or {}
            file = str(Path(pos['file']).resolve()) if pos.get('file') else ''
            if error.get('type') != 'error' or not match or file not in project.maps or not Path(file).is_relative_to(project.destination):
                remaining.append(error)
                continue
            if file not in files:
                native = Source(Path(file), project.frontend, thor=False, raw=project.generated[file],
                                cache=False, test=project.test, target=project.target)
                files[file] = native, [], set()
            native, edits, wrapped = files[file]
            explicit_vet = any(tag.removeprefix('#+').split()[:1] == ['vet']
                               for tag in native.info.get('file_tags') or [])
            declaration = next((node for node in native.nodes if node.kind == 'Declaration'
                                and any(name.start == pos['offset'] for name in node.group('names'))), None)
            block = declaration.parent if declaration else None
            # Only Odin's default one-declaration block check is relaxed.
            # Explicit unused-variable vetting retains its requested policy.
            if explicit_vet or not block or block.kind != 'Block':
                remaining.append(error)
                continue
            origin = adapter.lookup(file, pos['offset'])
            source = project.sources.get(Path(origin.file)) if origin else None
            name = next((node for node in source.nodes if node.kind == 'Ident'
                         and node.start == origin.start and node.text == match[1]), None) if source else None
            if name is None or not any(parent.kind == 'Procedure' for parent in name.ancestors()):
                remaining.append(error)
                continue
            origin = Origin(str(source.path), name.start, name.end, 'unused-local')
            translated = {**error, 'type': 'warning', 'msgs': error['msgs'][:1], 'pos': adapter.locate(origin)}
            current.append(translated)
            # The declaration and its initializer stay intact. A second inert
            # statement bypasses Odin's single-statement block check.
            if not edits or not any(start == declaration.end for start, _, _, _ in edits):
                edits.insert(0, (declaration.end, declaration.end, '\n_ = 0\n', origin))
            if native.raw[block.start:block.start+1] != b'{' and block not in wrapped:
                token = next(span for span in reversed(native.info.get('do_tokens') or [])
                             if block.parent.start <= span[0] < block.start)
                edits.extend([(*token, '', origin), (block.start, block.start, '{\n', origin),
                              (block.end, block.end, '\n}', origin)])
                wrapped.add(block)
        if not current:
            break
        warnings.extend(current)
        if '-warnings-as-errors' in command or any(error.get('type') == 'error' for error in remaining):
            result.stderr = json.dumps({'errors': [*remaining, *warnings]}).encode()
            return result
        for file, (_, edits, _) in files.items():
            if not edits:
                continue
            raw, mapping = project.maps[file].rewrite(project.generated[file], edits)
            project.maps[file], project.generated[file] = mapping, raw
            relative = Path(file).relative_to(project.destination).as_posix()
            project._planned[relative] = raw
            for source_path, output in project.outputs.items():
                if output == Path(file):
                    project.sources[source_path].source_map = mapping
            if project.persist_maps:
                data = mapping.json(file, project.sources)
                data['generated_sha256'] = hashlib.sha256(raw).hexdigest()
                project._planned[relative+'.map.json'] = json.dumps(data, ensure_ascii=False)+'\n'
        project.write()
    if warnings:
        try:
            packet = json.loads(result.stderr) if result.stderr else {'errors': []}
        except (ValueError, TypeError):
            return result
        packet['errors'] = [*warnings, *packet.get('errors', [])]
        result.stderr = json.dumps(packet).encode()
    return result
