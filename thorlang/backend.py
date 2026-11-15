"""Relax Odin's single-declaration control-block error into a warning."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time

from .compiler import Source
from .sourcemap import Origin


UNUSED = re.compile(r"^'([^']+)' declared but not used$")
UNUSED_VET_FLAGS = {'-vet', '-vet-unused', '-vet-unused-variables', '-vet-unused-procedures'}


def run_process(command, *, timeout=None, **options):
    """Preserve long deadlines without overflowing platform subprocess waits."""
    interval = 24 * 60 * 60
    if timeout is None or timeout <= interval:
        return subprocess.run(command, timeout=timeout, **options)

    input_data = options.pop('input', None)
    if input_data is not None:
        if options.get('stdin') is not None:
            raise ValueError('stdin and input arguments may not both be used')
        options['stdin'] = subprocess.PIPE
    if options.pop('capture_output', False):
        if options.get('stdout') is not None or options.get('stderr') is not None:
            raise ValueError('stdout and stderr cannot be combined with capture_output')
        options['stdout'] = subprocess.PIPE
        options['stderr'] = subprocess.PIPE
    check = options.pop('check', False)
    with subprocess.Popen(command, **options) as process:
        started = time.monotonic()
        try:
            while True:
                remaining = timeout - (time.monotonic() - started)
                try:
                    stdout, stderr = process.communicate(
                        input_data, timeout=max(0, min(remaining, interval)))
                    break
                except subprocess.TimeoutExpired as error:
                    if time.monotonic() - started >= timeout:
                        error.timeout = timeout
                        raise
                    # communicate retains buffered output and partially sent input.
                    input_data = None
        except subprocess.TimeoutExpired as error:
            process.kill()
            if os.name == 'nt':
                error.stdout, error.stderr = process.communicate()
            else:
                process.wait()
            raise
        except BaseException:
            process.kill()
            raise
        if check and process.returncode:
            raise subprocess.CalledProcessError(
                process.returncode, command, output=stdout, stderr=stderr)
        return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)


def reporting(command):
    return any(flag.startswith(('-show-', '-export-')) or flag in {'-build-diagnostics', '-print-linker-flags'} for flag in command)


def decode(stderr, successful, reports):
    if not stderr: return {'errors':[]}
    text=stderr.decode(errors='replace') if isinstance(stderr,bytes) else stderr
    try:
        packet=json.loads(text)
        if isinstance(packet,dict) and isinstance(packet.get('errors'),list): return packet
    except ValueError: pass
    errors=[]; information=''; cursor=0; scan=0; decoder=json.JSONDecoder()
    while scan<len(text):
        if text[scan]!='{': scan+=1; continue
        try: packet,end=decoder.raw_decode(text,scan)
        except ValueError: scan+=1; continue
        if isinstance(packet,dict) and isinstance(packet.get('errors'),list):
            information+=text[cursor:scan]; errors.extend(packet['errors']); scan=end; cursor=scan
        else: scan+=1
    if cursor or successful and reports:
        information+=text[cursor:]
        if not successful and not errors and information.strip():
            return {'errors':[{'type':'error','stage':'toolchain','pos':None,'msgs':[information.strip()]}]}
        return {'errors':errors,'information':information}
    return {'errors':[{'type':'warning' if successful else 'error','stage':'toolchain','pos':None,'msgs':[text.strip()]}]}


def run(project, command, **options):
    warnings = []
    single_module = '-use-single-module' in command
    while True:
        result = run_process(command, **options)
        if result.stderr and result.returncode >= 0:
            packet=decode(result.stderr, result.returncode==0, reporting(command))
            source_errors=any(error.get('type')=='error' and (error.get('pos') or {}).get('file')
                              for error in packet.get('errors',[]))
            collision=any('duplicate symbol' in line and '::' in line and ':proc(' in line
                          for error in packet.get('errors',[]) for message in error.get('msgs',[])
                          for line in message.splitlines())
            if result.returncode and not single_module and collision and not source_errors:
                command=[argument for argument in command if argument!='-use-separate-modules']+['-use-single-module']
                single_module=True
                continue
            result.stderr=json.dumps(packet).encode()
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
            result.stderr = json.dumps({'errors': [*remaining, *warnings], 'information':json.loads(result.stderr).get('information','')}).encode()
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
