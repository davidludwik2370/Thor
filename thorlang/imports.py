"""File-local wildcard names over original package declarations, without re-exports."""
from pathlib import Path

from .compiler import Source, unquote


def private(node):
    for attribute in node.group('attributes'):
        for item in attribute.group('elems'):
            name = item.role('field') if item.kind == 'FieldValue' else item
            if name and name.text == 'private':
                return True
    return False


def file_private(node):
    return bool(node.source.info.get('private_file') or any(
        item.kind=='FieldValue' and item.role('field').text=='private' and item.role('value').text.strip('"')=='file'
        for declaration in [node,*[a for a in node.ancestors() if a.kind=='ForeignBlock']]
        for attribute in declaration.group('attributes') for item in attribute.group('elems')))


def exports(source):
    if source.excluded or source.info.get('private'):
        return set()
    names = set()
    for node in source.nodes:
        if node.kind != 'Declaration':
            continue
        ancestors = list(node.ancestors())
        if any(a.kind in {'Procedure', 'StructType', 'UnionType', 'EnumType'} for a in ancestors):
            continue
        if private(node) or any(a.kind == 'ForeignBlock' and private(a) for a in ancestors):
            continue
        names.update(name.text for name in node.group('names') if name.text != '_')
    return names


def prepare(project):
    native = {}
    for source in project.sources.values():
        for wildcard in source.wildcards:
            path = unquote(wildcard['path'])
            namespace = path.rsplit('/', 1)[-1].rsplit(':', 1)[-1]
            directory = source.import_dir(path)
            sources = project.packages.get(directory)
            if sources is None:
                collection, _, relative = path.partition(':')
                root = source.info.get('odin_root')
                if collection not in {'core', 'base', 'vendor'} or not root:
                    source.error('Cannot discover wildcard package exports.', wildcard['start'], wildcard['end'])
                directory = (Path(root)/collection/relative).resolve()
                if directory not in native:
                    native[directory] = [Source(file, project.frontend, thor=False, package=True,
                        test=project.test, target=project.target, project_name=project.entry.stem,
                        raw=project.overlays.get(file),cache=project.cache_frontend,cache_dir=project.cache_directory) for file in sorted(directory.glob('*.odin'))]
                sources = native[directory]
            names = set().union(*(exports(member) for member in sources))
            for name in sorted(names):
                if name in source.selected or name in source.imports:
                    source.error(f'Conflicting wildcard import: {name}', wildcard['start'], wildcard['end'])
                source.selected[name] = f'{namespace}.{name}'
                source.selected_origins[name] = (wildcard['start'], wildcard['end'])
                source.wildcard_names.add(name)
            for node in source.nodes:
                if node.kind != 'Declaration' or source.scope(node).kind != 'File':
                    continue
                for name in node.group('names'):
                    if name.text in source.wildcard_names:
                        if any(a.kind in {'When', 'CompileSwitch'} for a in node.ancestors()):
                            source.conditional_conflicts.add(node)
                        else:
                            source.error(f'Declaration conflicts with wildcard import: {name.text}', name.start, name.end)
