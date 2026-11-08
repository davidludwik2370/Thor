"""Opt-in Thor conventions, checked fixes and explicit source updates."""
from __future__ import annotations

import contextlib
from dataclasses import dataclass
import io
import json
import os
from pathlib import Path
import stat
import tempfile

from .compiler import ThorError
from .sourcemap import Origin

CHECKS = ('immutable', 'pure')


@dataclass(frozen=True)
class Fix:
    source: object
    start: int
    end: int
    text: bytes
    rule: str
    names: str
    highlight_start: int
    highlight_end: int

    def diagnostic(self, adapter):
        message = (f"Declare '{self.names}' immutable with ':>'." if self.rule == 'immutable'
                   else f"Procedure '{self.names}' can be declared with 'fn'.")
        if not hasattr(self.source, 'vet_snapshot'):
            digest = 14695981039346656037
            for byte in self.source.raw:
                digest = ((digest ^ byte)*1099511628211) & ((1 << 64)-1)
            self.source.vet_snapshot = f'{digest:016x}'
        return {'type': 'warning', 'stage': 'vet', 'code': 'thor/prefer-'+('immutable' if self.rule == 'immutable' else 'fn'),
                'pos': adapter.locate(Origin(str(self.source.path), self.highlight_start, self.highlight_end)),
                'msgs': [message],
                'fix': {'pos': adapter.locate(Origin(str(self.source.path), self.start, self.end)),
                        'text': self.text.decode(), 'snapshot': self.source.vet_snapshot}}


def binding(node, resolver):
    while node and node.kind in {'Selector', 'Index', 'Slice', 'Deref', 'Paren'}:
        node = node.role('expr')
    return resolver.symbol(node) if node and node.kind == 'Ident' else None


def possible_pure_type(typ, resolver, seen=None):
    """Discard definitely ineligible public fn values before tracing helpers.

    This is only a rejection filter. Unknown/native-dependent shapes still go
    through the complete compiler, which remains the authority for every fix.
    """
    seen=set() if seen is None else seen
    key=typ.kind,typ.node
    if key in seen:return True
    seen.add(key)
    if typ.kind in {'pointer','dynamic','fixed','map','set','procedure'} or typ.name in {
            'rawptr','any','typeid','cstring','cstring16','string16'}:return False
    if typ.elem and not possible_pure_type(typ.elem,resolver,seen.copy()):return False
    if typ.kind=='struct' and typ.node:
        if typ.node.data.get('op')=='raw_union':return False
        return all(possible_pure_type(resolver.type_node(field.role('type'),typ.substitutions),resolver,seen.copy())
                   for field in typ.node.role('fields').children if field.kind=='Field')
    if typ.kind=='union' and typ.node:
        return all(possible_pure_type(resolver.type_node(variant,typ.substitutions),resolver,seen.copy())
                   for variant in typ.node.group('variants'))
    return True


def candidates(project, checks):
    root = project.entry.parent if project.file_entry else project.entry
    result = []
    resolver = project.resolver
    for source in list(project.sources.values()):
        if not source.thor or source.excluded or not source.path.is_relative_to(root):
            continue
        written = set()
        for node in source.nodes:
            targets = []
            if node.kind == 'Assignment' and node.data['op'] != 'in':
                targets = node.group('lhs')
            elif node.kind == 'IncDec' or node.kind == 'Unary' and node.data['op'] == '&':
                targets = [node.role('expr')]
            elif node.kind == 'Range' and any(name.text.startswith('&') for name in node.group('names')):
                targets = [node.role('expr')]
            for target in targets:
                symbol = binding(target, resolver)
                if symbol:
                    written.add(symbol.declaration)
        for node in source.nodes:
            if (node.kind != 'Declaration' or node.group('attributes')
                    or any(parent.kind in {'When', 'CompileSwitch', 'CompileSwitchExpr'} for parent in node.ancestors())):
                continue
            names = [name for name in node.group('names') if name.text != '_']
            if not names:
                continue
            label = ', '.join(name.text for name in names)
            if ('immutable' in checks and node.data.get('mutable') and not node.data.get('immutable')
                    and node.group('values') and node not in written
                    and any(parent.kind == 'Procedure' for parent in node.ancestors())):
                separator = node.data.get('roles', {}).get('separator')
                if separator and source.raw[separator[0]:separator[1]] == b'=':
                    result.append(Fix(source, *separator, b'>', 'immutable', label, names[0].start, names[-1].end))
            values = node.group('values')
            if ('pure' not in checks or len(values) != 1 or values[0].kind != 'Procedure'
                    or len(names) != 1 or names[0].text == 'main'):
                continue
            proc = values[0]
            signature = proc.role('type')
            if (proc.data.get('pure') or not proc.role('body') or resolver.generic_parameters(signature)
                    or signature.data.get('calling_convention') not in {'', 'odin'}):
                continue
            def field_type(field):
                return (resolver.type_node(field.role('type')) if field.role('type')
                        else resolver.type_of(field.role('default')))
            results=signature.role('results')
            outputs = [field_type(field) for field in results.children if field.kind=='Field'] if results else []
            # fn promotes owned results into independent storage. Keep aliasing
            # and allocation decisions explicit for reference-bearing results.
            if any(typ.kind in {'unknown', 'generic'} or project.purity.owned_type(typ) for typ in outputs):
                continue
            inputs=[field_type(field) for field in signature.role('params').children if field.kind=='Field']
            if any(not possible_pure_type(typ,resolver) for typ in [*inputs,*outputs]):continue
            if any(n.kind=='Call' and project.purity.io_call(n) for n in project.purity.walk(proc)):continue
            # Reuse purity's source analysis to discard known violations before
            # asking the complete compiler to validate the proposed conversion.
            project.purity.analyze(proc)
            if proc in project.purity.contracts or any(project.purity.owner(site) is proc for site in project.purity.problems):
                continue
            if source.raw[proc.start:proc.start+4] == b'proc':
                result.append(Fix(source, proc.start, proc.start+4, b'fn', 'pure', label, names[0].start, names[0].end))
    return sorted(result, key=lambda fix: (fix.rule != 'immutable', str(fix.source.path), fix.start))


def revisions(fixes):
    grouped = {}
    for fix in fixes:
        grouped.setdefault(fix.source.path, []).append(fix)
    result = {}
    for path, edits in grouped.items():
        raw = edits[0].source.raw
        for fix in sorted(edits, key=lambda item: item.start, reverse=True):
            raw = raw[:fix.start]+fix.text+raw[fix.end:]
        result[path] = raw
    return result


class Validator:
    def __init__(self, project, options, flags, directory):
        self.project, self.options = project, options
        self.directory = Path(directory)
        self.flags = [flag for flag in flags if not flag.startswith(('-out:', '-build-mode:'))
                      and flag not in {'-warnings-as-errors', '-ignore-warnings', '-keep-executable'}]
        if project.test:
            self.flags.append('-build-mode:test')

    def check(self, fixes):
        from .driver import _main
        overlays = {**self.project.overlays, **revisions(fixes)}
        replacements = {}
        for index, (path, raw) in enumerate(overlays.items()):
            snapshot = self.directory/f'source-{index}'
            snapshot.write_bytes(raw)
            replacements[str(path)] = str(snapshot)
        overlay = self.directory/'overlay.json'
        overlay.write_text(json.dumps({'Replace': replacements}))
        command = ['check', str(self.project.entry), '--odin', self.options.odin,
                   '--overlay', str(overlay), '--output-dir', str(self.directory/'output'),
                   '--cache-dir', str(self.project.cache_directory), '--run-timeout', str(self.options.run_timeout),
                   '-json-errors', *self.flags]
        if self.options.no_cache:
            command.append('--no-cache')
        output, errors = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            status = _main(command)
        if status:
            try:
                diagnostics = json.loads(errors.getvalue())['errors']
            except (ValueError, KeyError) as exc:
                raise ThorError('Thor vet could not validate its proposed corrections.') from exc
            failure = next((item for item in diagnostics if item.get('stage') == 'toolchain'), None)
            if failure:
                raise ThorError('Thor vet validation failed in the native toolchain. '+failure['msgs'][0])
        return status == 0

    def select(self, proposed, accepted):
        if not proposed:
            return
        if self.check([*accepted, *proposed]):
            accepted.extend(proposed)
        elif len(proposed) > 1:
            middle = len(proposed)//2
            self.select(proposed[:middle], accepted)
            self.select(proposed[middle:], accepted)


def apply(fixes):
    updated = revisions(fixes)
    originals = {fix.source.path: fix.source.raw for fix in fixes}
    staged, backups, applied = {}, {}, []
    try:
        for path, raw in updated.items():
            if path.read_bytes() != originals[path]:
                raise ThorError(f'Source changed during vetting at {path}. Run thor vet again.')
            mode = stat.S_IMODE(path.stat().st_mode)
            for contents, files in [(raw, staged), (originals[path], backups)]:
                with tempfile.NamedTemporaryFile(prefix='.thor-vet-', dir=path.parent, delete=False) as stream:
                    files[path] = Path(stream.name)
                    stream.write(contents)
                os.chmod(files[path], mode)
        for path, temporary in staged.items():
            if path.read_bytes() != originals[path]:
                raise ThorError(f'Source changed during vetting at {path}. Run thor vet again.')
            os.replace(temporary, path)
            applied.append(path)
    except BaseException:
        for path in reversed(applied):
            if path.read_bytes() == updated[path]:
                os.replace(backups[path], path)
        raise
    finally:
        for temporary in [*staged.values(), *backups.values()]:
            temporary.unlink(missing_ok=True)


def run(project, options, flags):
    checks = CHECKS if options.vet is None else tuple(options.vet.split(','))
    proposed = candidates(project, checks)
    accepted = []
    if proposed:
        with tempfile.TemporaryDirectory(prefix='thor-vet-') as directory:
            Validator(project, options, flags, directory).select(proposed, accepted)
    if options.command == 'vet' and (options.apply or options.ask) and accepted:
        adapter = project.diagnostic_adapter()
        for fix in accepted:
            pos = adapter.locate(Origin(str(fix.source.path), fix.highlight_start, fix.highlight_end))
            print(f"{pos['file']}({pos['line']}:{pos['column']}): {fix.diagnostic(adapter)['msgs'][0]}")
        confirmed = options.apply
        if options.ask:
            try:
                answer = input(f'Apply these {len(accepted)} corrections? [y/N] ')
            except EOFError:
                answer = ''
            confirmed = answer.strip().lower() in {'y', 'yes'}
        if confirmed:
            apply(accepted)
            print(f'Applied {len(accepted)} corrections.')
            return []
    adapter = project.diagnostic_adapter()
    return [fix.diagnostic(adapter) for fix in accepted]
