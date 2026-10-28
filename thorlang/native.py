"""On-demand standard-library source facts, isolated from ordinary emission."""
import os
import hashlib
from pathlib import Path

from .compiler import Source


class NativeSources:
    # These are compiler/runtime namespaces, rather than ordinary source APIs.
    compiler_packages = {'base:intrinsics', 'base:builtin', 'base:runtime'}

    @staticmethod
    def support(source):
        runtime=Path(__file__).resolve().parents[1] / 'runtime'
        return source.path.parent.parent==runtime and source.path.parent.name in {'eval','pure','format','numeric','sequence','sets'}

    def __init__(self, project, root=None):
        self.project = project
        self.root = root or next((Path(s.info['odin_root']) for s in project.sources.values() if s.info.get('odin_root')), None)
        self.packages = {}
        self.sources = {}
        self.discovery_scope = None

    def directory(self, source, path):
        if source.import_dir(path) is not None:
            return None
        collection, separator, relative = path.partition(':')
        root = self.root
        if not separator or collection not in {'base', 'core', 'vendor'} or not root:
            return None
        return (Path(root) / collection / relative).resolve()

    def load(self, source, path):
        directory = self.directory(source, path)
        if directory is None or path in self.compiler_packages:
            return None
        if directory not in self.packages and self.discovery_scope is not None and directory not in self.discovery_scope:
            return None
        if directory not in self.packages:
            members = []
            self.packages[directory] = members
            p = self.project
            files=set(directory.glob('*.odin')) | {path for path in p.overlays if path.parent==directory and path.suffix=='.odin'}
            for file in sorted(files):
                member = Source(file, p.frontend, thor=False, package=True, test=p.test,
                                project_name=p.entry.stem, raw=p.overlays.get(file), target=p.target, mapping=p.mapping,cache=p.cache_frontend)
                member.native_inspected = True
                member.project = p.packages
                member.collections = p.collections
                member.purity = p.purity
                members.append(member)
                self.sources[file] = member
            p.resolver.add_sources(members)
        return directory

    def verify_access(self, node, resolver, seen=None, *, context='#run'):
        from .imports import private,file_private
        seen=set() if seen is None else seen
        if node is None or node in seen:return
        seen.add(node)
        symbol=resolver.symbol(node)
        if not symbol:return
        owner=symbol.node.source
        if node.source.path.parent!=owner.path.parent:
            hidden=owner.info.get('private') or private(symbol.declaration) or any(
                a.kind=='ForeignBlock' and private(a) for a in symbol.declaration.ancestors())
            if hidden:node.source.error(f"'{symbol.name}' is private to its package and cannot be used by {context}.",node.start,node.end)
        if node.source.path!=owner.path and file_private(symbol.declaration):
            node.source.error(f"'{symbol.name}' is private to its file and cannot be used by {context}.",node.start,node.end)
        if symbol.value and symbol.value.kind in {'Ident','Selector','ProcedureGroup','Paren'}:
            if symbol.value.kind in {'ProcedureGroup','Paren'}:
                for child in symbol.value.children:self.verify_access(child,resolver,seen,context=context)
            else:self.verify_access(symbol.value,resolver,seen,context=context)

    def attach(self, helper, retained):
        originals = {node.source for node in retained if getattr(node.source, 'native_inspected', False)}
        members = []
        helper.native_package_names={}
        for original in sorted(originals, key=lambda s: str(s.path)):
            member = Source(original.path, helper.frontend, thor=False, package=True, test=helper.test,
                            project_name=helper.entry.stem, mapping=helper.mapping,
                            raw=original.raw, target=helper.target,cache=helper.cache_frontend)
            member.native_inspected = True
            member.project = helper.packages
            member.collections = helper.collections
            member.purity = helper.purity
            package = member.root.data.get('roles', {}).get('package')
            if package:
                name = 'thor_eval_' + hashlib.sha256(str(member.path.parent).encode()).hexdigest()[:12]
                member.edits.append((*package, name))
                helper.native_package_names[name]=member.root.data['name']
            helper.sources[member.path] = member
            helper.packages.setdefault(member.path.parent, []).append(member)
            members.append(member)
        helper.resolver.add_sources(members)
        helper.resolver.native = NativeSources(helper, self.root)
        # The closure already selected every SDK declaration required for
        # evaluation. Runtime support emitted around it must not expand that
        # closure by inspecting its own ordinary fmt/reflect/memory calls.
        # Previously inspected packages remain available as metadata, including
        # mutable declarations that must be diagnosed but never emitted.
        helper.resolver.native.discovery_scope = frozenset(self.packages)
        helper.resolver.native_enabled = True
        # Existing attached sources must retain the exact ASTs used by pruning.
        for member in members:
            helper.resolver.native.sources[member.path] = member
            helper.resolver.native.packages.setdefault(member.path.parent, []).append(member)
        roots = [p.parent for p in helper.sources] + list(helper.collections.values())
        helper.common = Path(os.path.commonpath(roots))
        directories = {member.path.parent for member in members}
        for source in helper.sources.values():
            if self.support(source):
                continue
            for namespace, path in source.imports.items():
                directory = self.directory(source, path)
                if directory in directories:
                    source.evaluation_import_paths[namespace] = os.path.relpath(directory, source.path.parent)
