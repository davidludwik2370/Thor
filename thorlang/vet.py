"""Opt-in Thor conventions, checked fixes and explicit source updates."""
from __future__ import annotations

import contextlib
from bisect import bisect_left
from dataclasses import dataclass
import io
import json
import os
from pathlib import Path
import stat
import tempfile

from .compiler import ThorError
from .sourcemap import Origin, position

CHECKS = ('immutable', 'pure', 'unused', 'semicolon')


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
        if self.rule == 'semicolon':
            message = 'Unnecessary semicolon.'
            code = 'thor/unnecessary-semicolon'
        elif self.rule == 'immutable':
            message = f"Declare '{self.names}' immutable with ':>'."
            code = 'thor/prefer-immutable'
        elif self.rule == 'iterator':
            message = f"'{self.names}' declared but not used"
            code = 'thor/unused'
        elif self.rule == 'import':
            message = f"Unused import '{self.names}'."
            code = 'thor/unused-import'
        else:
            message = f"Procedure '{self.names}' can be declared with 'fn'."
            code = 'thor/prefer-fn'
        if not hasattr(self.source, 'vet_snapshot'):
            digest = 14695981039346656037
            for byte in self.source.raw:
                digest = ((digest ^ byte)*1099511628211) & ((1 << 64)-1)
            self.source.vet_snapshot = f'{digest:016x}'
        return {'type': 'warning', 'stage': 'vet', 'code': code,
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
                   for field in (typ.node.role('fields') or typ.node).children if field.kind=='Field')
    if typ.kind=='union' and typ.node:
        return all(possible_pure_type(resolver.type_node(variant,typ.substitutions),resolver,seen.copy())
                   for variant in typ.node.group('variants'))
    return True


def reference_output(typ, project, seen=None):
    seen=set() if seen is None else seen
    key=typ.kind,typ.node
    if key in seen:return False
    seen.add(key)
    if typ.kind in {'unknown','generic'}:return True
    if typ.kind in {'pointer','slice','dynamic','map','set'}:
        if typ.elem is None:return True
        return project.purity.owned_type(typ.elem) or not possible_pure_type(typ.elem,project.resolver)
    if typ.kind in {'array','fixed'}:return reference_output(typ.elem,project,seen)
    if typ.kind=='struct':
        fields=list(typ.members.values())
        if not fields and typ.node:
            fields=[project.resolver.type_node(field.role('type'),typ.substitutions)
                    for field in (typ.node.role('fields') or typ.node).children if field.kind=='Field']
        return any(reference_output(field,project,seen) for field in fields)
    if typ.kind=='union' and typ.node:
        return any(reference_output(project.resolver.type_node(variant,typ.substitutions),project,seen)
                   for variant in typ.node.group('variants'))
    return False


class PortableBodies:
    def __init__(self, project):
        self.project=project
        self.known={}
        self.contracts={}

    def contracted(self, call):
        if call not in self.contracts:
            access=self.project.slice_access
            library=self.project.purity.library(call)
            self.contracts[call]=access.verified_reader(call,library) or bool(
                library and library[0]=='core:slice' and library[1] in {'clone','clone_to_dynamic'}
                and access.native_library(call,library[0]))
        return self.contracts[call]

    def check(self, root, argument_contracts=False):
        key=root,argument_contracts
        if key in self.known:return self.known[key]
        pending=[root];seen=set();callers={};rejected=set()
        while pending:
            procedure=pending.pop()
            key=procedure,argument_contracts
            if key in self.known:
                if not self.known[key]:rejected.add(procedure)
                continue
            if procedure in seen:continue
            seen.add(procedure);source=procedure.source
            if source.info.get('target_specific') or any(tag.startswith('#+build') for tag in source.info.get('file_tags',[])):
                rejected.add(procedure)
                continue
            for node in self.project.purity.walk(procedure):
                if node.kind in {'When','WhenExpr','CompileSwitch','CompileSwitchExpr'}:
                    rejected.add(procedure)
                    break
                if node.kind=='Call' and not (argument_contracts and self.contracted(node)):
                    for target in self.project.purity.targets(node):
                        callers.setdefault(target,set()).add(procedure)
                        pending.append(target)
        pending=list(rejected)
        while pending:
            for caller in callers.get(pending.pop(),()):
                if caller not in rejected:
                    rejected.add(caller)
                    pending.append(caller)
        for procedure in seen:self.known[procedure,argument_contracts]=procedure not in rejected
        return self.known[root,argument_contracts]


def candidates(project, checks):
    if not {'immutable', 'pure'}.intersection(checks):
        return []
    root = project.entry.parent if project.file_entry else project.entry
    result = []
    resolver = project.resolver
    portable = PortableBodies(project)
    for source in list(project.sources.values()):
        if not source.thor or source.excluded or not source.path.is_relative_to(root):
            continue
        written = set()
        mutated = set()
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
                    if node.kind in {'Assignment','IncDec'}:mutated.add(symbol.declaration)
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
                generic_local=any(parent.kind=='Procedure' and resolver.generic_parameters(parent.role('type'))
                                  for parent in node.ancestors())
                types=[resolver.type_of(name) for name in names]
                if any(project.purity.owned_type(typ) or not possible_pure_type(typ,resolver) for typ in types):
                    owner=next(parent for parent in node.ancestors() if parent.kind=='Procedure')
                    if not portable.check(owner,argument_contracts=True):continue
                owner=next(parent for parent in node.ancestors() if parent.kind=='Procedure')
                if any(resolver.frozen_slice_storage(typ) for typ in types):
                    outputs=[resolver.type_node(annotation) for _,annotation in resolver.fields(owner.role('type').role('results'))]
                    if any(project.purity.owned_type(typ) or not possible_pure_type(typ,resolver) for typ in outputs):continue
                    inputs=[resolver.type_node(field.role('type')) if field.role('type') else resolver.type_of(field.role('default'))
                            for field in owner.role('type').role('params').children if field.kind=='Field']
                    if any(reference_output(typ,project) for typ in inputs):continue
                independent=not generic_local or all(not project.purity.owned_type(typ)
                    and possible_pure_type(typ,resolver) for typ in types)
                separator = node.data.get('roles', {}).get('separator')
                if independent and separator and source.raw[separator[0]:separator[1]] == b'=':
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
            input_fields=[field for field in signature.role('params').children if field.kind=='Field']
            inputs=[field_type(field) for field in input_fields]
            if any(not possible_pure_type(typ,resolver) for typ in [*inputs,*outputs]):continue
            if any(field in mutated and project.purity.owned_type(typ)
                   for field,typ in zip(input_fields,inputs)):continue
            if any(n.kind=='Call' and project.purity.io_call(n) for n in project.purity.walk(proc)):continue
            # Reuse purity's source analysis to discard known violations before
            # asking the complete compiler to validate the proposed conversion.
            project.purity.analyze(proc)
            if proc in project.purity.contracts or any(project.purity.owner(site) is proc for site in project.purity.problems):
                continue
            if not portable.check(proc):continue
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
        from .cli import CATALOG, helper_flag
        action = 'build' if project.test else 'check'
        excluded = {flag['name'] for flag in CATALOG['native_flags'] if action not in flag['commands']}
        self.flags = [flag for flag in flags if (helper_flag(flag) or project.test and flag=='-all-packages')
                      and not flag.startswith(('-out:', '-build-mode:'))
                      and flag.lstrip('-').split(':', 1)[0] not in excluded
                      and flag != '-keep-executable']
        if project.test:
            self.flags.extend(['-build-mode:test', '-out:'+str(self.directory/'test-probe')])

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
        command = ['build' if self.project.test else 'check', str(self.project.entry), '--odin', self.options.odin,
                   '--overlay', str(overlay), '--output-dir', str(self.directory/'output'),
                   '--cache-dir', str(self.project.cache_directory), '--run-timeout', str(self.options.run_timeout),
                   '-json-errors', *self.flags]
        if self.options.no_cache:
            command.append('--no-cache')
        output, errors = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            status = _main(command)
        self.errors=[]
        if status:
            try:
                diagnostics = json.loads(errors.getvalue())['errors']
            except (ValueError, KeyError) as exc:
                raise ThorError('Thor vet could not validate its proposed corrections.') from exc
            failure = next((item for item in diagnostics if item.get('stage') == 'toolchain'), None)
            if failure:
                raise ThorError('Thor vet validation failed in the native toolchain. '+failure['msgs'][0])
            self.errors=diagnostics
        return status == 0

    def blocked_pure(self,fix,combined):
        if fix.rule!='pure':return False
        start=fix.start+sum(len(edit.text)-(edit.end-edit.start) for edit in combined
            if edit.source is fix.source and edit.end<=fix.start)
        rooted=any(note.get('message','').startswith("fn '")
            and 'requires its entire call chain to be pure' in note['message']
            and note.get('pos',{}).get('file')==str(fix.source.path)
            and note['pos'].get('offset')==start
            for error in self.errors if error.get('type')=='error' for note in error.get('related',[]))
        if rooted:return True
        procedure=next((node for node in fix.source.nodes if node.kind=='Procedure' and node.start==fix.start),None)
        if procedure is None:return False
        def shifted(offset):
            return offset+sum(len(edit.text)-(edit.end-edit.start) for edit in combined
                if edit.source is fix.source and edit.end<=offset)
        for error in self.errors:
            pos=error.get('pos',{});offset=pos.get('offset',-1)
            if (error.get('type')!='error' or pos.get('file')!=str(fix.source.path)
                    or not any(message.startswith('Pure ') for message in error.get('msgs',[]))
                    or not start<=offset<shifted(procedure.end)):continue
            nested=any(node.kind=='Procedure' and procedure.start<node.start and node.end<=procedure.end
                and shifted(node.start)<=offset<shifted(node.end) for node in fix.source.nodes)
            if not nested:return True
        return False

    def select(self, proposed, accepted):
        if not proposed:
            return
        combined=[*accepted,*proposed]
        if self.check(combined):
            accepted.extend(proposed)
        else:
            remaining=[fix for fix in proposed if not self.blocked_pure(fix,combined)]
            if len(remaining)<len(proposed):self.select(remaining,accepted)
            elif len(proposed)>1:
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


def run(project, options, flags, native_errors=None):
    checks = CHECKS if options.vet is None else tuple(options.vet.split(','))
    from .vet_unused import analyze
    unused_findings, immutable_exemptions, usage_fixes = analyze(project, include_unused='unused' in checks) if {'unused', 'immutable'}.intersection(checks) else ([], [], [])
    exempt_offsets = {}
    for node in immutable_exemptions:
        exempt_offsets.setdefault(str(node.source.path), []).append(node.start)
    for offsets in exempt_offsets.values():
        offsets.sort()

    def exempt_declaration(fix):
        if fix.rule != 'immutable':
            return False
        offsets = exempt_offsets.get(str(fix.source.path), ())
        index = bisect_left(offsets, fix.highlight_start)
        return index < len(offsets) and offsets[index] < fix.highlight_end

    proposed = [*usage_fixes, *(fix for fix in candidates(project, checks) if not exempt_declaration(fix))]
    findings = unused_findings if 'unused' in checks else []
    if native_errors is not None:
        reported = {(finding['pos']['file'], finding['pos']['offset'], tuple(finding['msgs']))
                    for finding in findings}
        native_errors[:] = [finding for finding in native_errors if finding['type'] != 'warning'
                            or ((finding.get('pos') or {}).get('file'),
                                (finding.get('pos') or {}).get('offset'), tuple(finding['msgs'])) not in reported]
    from .source_tools import semicolon_edits
    accepted = semicolon_edits(project, thor_only=True) if 'semicolon' in checks else []
    covered_semicolons = [fix for fix in accepted if any(
        item.source is fix.source and item.start <= fix.start and fix.end <= item.end for item in usage_fixes if item.rule=='import')]
    accepted = [fix for fix in accepted if fix not in covered_semicolons]
    if proposed:
        with tempfile.TemporaryDirectory(prefix='thor-vet-') as directory:
            Validator(project, options, flags, directory).select(proposed, accepted)
    accepted.extend(fix for fix in covered_semicolons if not any(
        item.rule == 'import' and item.source is fix.source and item.start <= fix.start and fix.end <= item.end
        for item in accepted))
    corrected_imports = {(str(fix.source.path), fix.highlight_start) for fix in accepted if fix.rule=='import'}
    findings = [finding for finding in findings if finding['code'] != 'thor/unused-import'
                or (finding['pos']['file'], finding['pos']['offset']) not in corrected_imports]
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
            corrected={(str(fix.source.path),fix.highlight_start) for fix in accepted if fix.rule=='iterator'}
            findings=[finding for finding in findings if finding['code']!='thor/unused'
                      or (finding['pos']['file'],finding['pos']['offset']) not in corrected]
            apply(accepted)
            updated = revisions(accepted)
            project.vet_updated_sources = {str(path): raw for path, raw in updated.items()}
            for finding in findings:
                pos = finding['pos']
                path = Path(pos['file'])
                if path not in updated:
                    continue
                fixes = [fix for fix in accepted if fix.source.path == path]
                for key, line, column in [('offset','line','column'), ('end_offset','end_line','end_column')]:
                    old = pos[key]
                    pos[key] += sum(len(fix.text)-(fix.end-fix.start) for fix in fixes if fix.end <= old)
                    pos[line], pos[column] = position(updated[path], pos[key])
            print(f'Applied {len(accepted)} corrections.')
            return findings
    adapter = project.diagnostic_adapter()
    iterator_fixes={(str(fix.source.path),fix.highlight_start):fix for fix in accepted if fix.rule=='iterator'}
    findings=[iterator_fixes[(finding['pos']['file'],finding['pos']['offset'])].diagnostic(adapter)
              if finding['code']=='thor/unused' and (finding['pos']['file'],finding['pos']['offset']) in iterator_fixes
              else finding for finding in findings]
    return [*findings, *(fix.diagnostic(adapter) for fix in accepted if fix.rule!='iterator')]
