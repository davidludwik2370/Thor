#!/usr/bin/env python3
"""Inspect Linux release ABI requirements without executing packaged binaries."""
import argparse
import json
from pathlib import Path
import struct
import tarfile

ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT/'release/linux-compatibility.json'
LIMIT = 1024 * 1024 * 1024


def version(value):
    return tuple(int(p) for p in value.split('.'))


def elf_requirements(data):
    if data[:4] != b'\x7fELF':
        return None
    if len(data) >= 52 and data[4] == 1:
        return None  # A 64-bit SDK may include 32-bit cross-target libraries.
    if len(data) < 64 or data[4] != 2 or data[5] not in (1, 2) or data[6] != 1:
        raise ValueError('Unsupported or truncated ELF header')
    endian = '<' if data[5] == 1 else '>'
    kind, machine = struct.unpack_from(endian+'HH', data, 16)
    offset = struct.unpack_from(endian+'Q', data, 40)[0]
    size, count = struct.unpack_from(endian+'HH', data, 58)
    if kind not in (2, 3):
        return None  # Relocatable compiler inputs are not host executables.
    if size < 64 or not count or offset + count*size > len(data):
        raise ValueError('Invalid ELF section table')
    sections = [struct.unpack_from(endian+'IIQQQQIIQQ', data, offset+i*size)
                for i in range(count)]
    needs = set()
    for section in sections:
        if section[1] != 0x6ffffffe:  # SHT_GNU_verneed
            continue
        if section[6] >= count:
            raise ValueError('Invalid ELF version string-table link')
        table = sections[section[6]]
        if table[1] != 3 or table[4]+table[5] > len(data):
            raise ValueError('Invalid ELF version string table')
        strings = data[table[4]:table[4]+table[5]]
        position, end = section[4], section[4]+section[5]
        if end > len(data):
            raise ValueError('Truncated ELF version requirements')
        records = 0
        while position < end:
            if position+16 > end:
                raise ValueError('Truncated ELF version record')
            revision, entries, _, aux, step = struct.unpack_from(endian+'HHIII', data, position)
            if revision != 1 or not entries or aux < 16:
                raise ValueError('Invalid ELF version record')
            cursor = position+aux
            for index in range(entries):
                if cursor+16 > end:
                    raise ValueError('Truncated ELF version auxiliary record')
                _, _, _, name, following = struct.unpack_from(endian+'IHHII', data, cursor)
                stop = strings.find(b'\0', name)
                if name >= len(strings) or stop < 0:
                    raise ValueError('Invalid ELF version string offset')
                try:
                    needs.add(strings[name:stop].decode('ascii'))
                except UnicodeDecodeError as error:
                    raise ValueError('Invalid ELF version string') from error
                if index+1 < entries:
                    if following < 16:
                        raise ValueError('Invalid ELF auxiliary chain')
                    cursor += following
                elif following:
                    raise ValueError('Inconsistent ELF auxiliary count')
            records += 1
            if not step:
                break
            if step < 16 or position+step >= end:
                raise ValueError('Invalid ELF version chain')
            position += step
        if section[7] and records != section[7]:
            raise ValueError('Inconsistent ELF version count')
    maxima, unknown = {}, []
    features = {'GLIBC_ABI_DT_RELR': ('glibc', '2.36')}
    legacy = {'CXXABI_TM_1', 'CXXABI_FLOAT128', 'CXXABI_ARM_1.3.3'}
    for name in needs:
        if name in features:
            family, floor = features[name]
            maxima[family] = floor
    for prefix in ('GLIBC', 'GLIBCXX', 'CXXABI'):
        values = [s[len(prefix)+1:] for s in needs if s.startswith(prefix+'_')]
        numeric = [s for s in values if all(p.isdigit() for p in s.split('.'))]
        unknown.extend(prefix+'_'+s for s in values if s not in numeric
                       and prefix+'_'+s not in features and prefix+'_'+s not in legacy)
        if numeric:
            family = prefix.lower()
            maxima[family] = max([*numeric, *([maxima[family]] if family in maxima else [])], key=version)
    return {'machine': machine, 'requirements': maxima, 'unknown_versions': sorted(unknown)}


def inspect(entries, arch, *, portable=False, policy=None):
    policy = policy or json.loads(POLICY.read_text())
    if arch not in policy['architectures']:
        raise ValueError('Unsupported Linux release architecture: '+arch)
    expected = policy['architectures'][arch]
    rows, maxima = [], {}
    required = {'thor', 'thor-install', 'odin/odin', 'tls/bin/tls'}
    seen = set()
    for name, data in entries:
        try:
            info = elf_requirements(data)
        except ValueError as error:
            raise ValueError(f'{name}: {error}') from error
        if name in required:
            if not info or info['machine'] != expected:
                raise ValueError(f'{name}: expected native Linux {arch} ELF executable')
            seen.add(name)
        if not info or info['machine'] != expected:
            continue  # SDKs also contain libraries for other targets.
        row = {'file': name, **info['requirements']}
        if info['unknown_versions']:
            row['unknown_versions'] = info['unknown_versions']
            if portable:
                raise ValueError(f'{name}: unverified system ABI versions: '+', '.join(info['unknown_versions']))
        rows.append(row)
        for family, value in info['requirements'].items():
            if family not in maxima or version(value) > version(maxima[family]):
                maxima[family] = value
            if portable and version(value) > version(policy[family]):
                raise ValueError(f'{name} requires {family} {value}; portable releases must '
                                 f'support {policy[family]} ({policy["baseline"]}). '
                                 'Rebuild this component on the release baseline.')
    if seen != required:
        raise ValueError('Missing native release executables: '+', '.join(sorted(required-seen)))
    return {'baseline': policy['baseline'], 'portable': portable,
            'required': maxima, 'files': rows}


def inspect_directory(directory, arch, *, portable=False):
    def entries():
        for p in sorted(Path(directory).rglob('*')):
            if not p.is_file():
                continue
            with p.open('rb') as stream:
                header = stream.read(64)
                if header[:4] == b'\x7fELF':
                    if p.stat().st_size > LIMIT:
                        raise ValueError('Oversized ELF component: '+str(p))
                    yield p.relative_to(directory).as_posix(), header+stream.read()
    return inspect(entries(), arch, portable=portable)


def inspect_archive(archive, *, portable=True):
    with tarfile.open(archive, 'r|*') as incoming:
        manifest = incoming.next()
        if not manifest or manifest.name != 'THOR-DISTRIBUTION.json' or manifest.size > 16*1024*1024:
            raise ValueError('Missing or oversized distribution manifest')
        metadata = json.load(incoming.extractfile(manifest))
        target = metadata['target']
        if not target.startswith('linux-'):
            raise ValueError('Expected a Linux distribution')
        def entries():
            names = set()
            while True:
                member = incoming.next()
                if member is None:
                    break
                if member.name in names:
                    raise ValueError('Duplicate distribution component: '+member.name)
                names.add(member.name)
                if len(names) > 100000 or member.name == 'THOR-DISTRIBUTION.json':
                    raise ValueError('Excessive or repeated distribution metadata')
                if not member.isfile():
                    continue
                if member.size > LIMIT:
                    raise ValueError('Oversized distribution component: '+member.name)
                stream = incoming.extractfile(member)
                header = stream.read(64)
                if header[:4] == b'\x7fELF':
                    yield member.name, header+stream.read()
        return inspect(entries(), target.removeprefix('linux-'), portable=portable)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('bundle', type=Path)
    parser.add_argument('--report', type=Path)
    options = parser.parse_args()
    result = inspect_archive(options.bundle)
    text = json.dumps(result, indent=2)+'\n'
    if options.report:
        options.report.write_text(text)
    print(text, end='')


if __name__ == '__main__':
    main()
