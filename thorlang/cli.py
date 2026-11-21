"""One public CLI catalog for the native compiler and Python reference driver."""
from __future__ import annotations
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
CATALOG = json.loads((ROOT/'cli/help.json').read_text())
USAGE = 'Usage: thor <command> [source] [options] [-- arguments]'
VALUE_OPTIONS = {'--output-dir', '--cache-dir', '--odin', '--diagnostic-format', '--overlay',
                 '--command',
                 '--run-timeout', '--track-allocations', '--app-name', '--app-id', '--app-version',
                 '--app-icon', '--app-type', '--app-resource', '--app-library', '--publish-dir'}


def source_command(name):
    return any(c['name'] == name and c['source'] for c in CATALOG['commands'])


def odin_option(args):
    for index, arg in enumerate(args):
        if arg == '--odin' and index+1 < len(args):
            return args[index+1]
        if arg.startswith('--odin='):
            return arg.split('=', 1)[1]
    return os.environ.get('THOR_ODIN', 'odin')


def commands():
    return 'Commands:\n'+''.join(f"  {c['name']:17} {c['description']}\n" for c in CATALOG['commands'])


def overview(version):
    return f'''Thor {version}

{USAGE}

{commands()}
Common options:
  -debug               Enable debugging and default memory checks
  -o:MODE              Set optimization (none, minimal, size, speed, aggressive)
  -out:PATH            Choose the artifact path
  --vet[=CHECKS]       Check Thor conventions and unused bindings
  --output-dir PATH    Choose the generated-source directory
  --no-cache           Bypass parser and compile-time result caches

[source] defaults to . (the current directory). new requires a project name. migrate requires a directory or --command.
Build executables default to ./<working-directory-name>.

Use thor help for standard options.
Use thor help -full for all options, or thor help <command> for command help.
'''


def render(version, command=None, full=False, odin='odin'):
    text = f'Thor {version}\n\n'
    if command is None:
        text += USAGE+'\n\n'+commands()
    elif command == 'help':
        text += 'Usage: thor help [command] [-full]\n\nShow standard options, or every compatible option with -full.\n'
    elif command == 'new':
        text += 'Usage: thor new <name> [options]\n'
    elif command == 'migrate':
        text += 'Usage: thor migrate [Odin project] [options] [Odin flags]\n'
    elif command == 'bundle':
        text += 'Usage: thor bundle android [directory] [options]\n'
    elif source_command(command):
        text += f'Usage: thor {command} [source] [options]'+(' [-- arguments]' if command in {'run','test'} else '')+'\n'
    else:
        text += f'Usage: thor {command}'+(' [--odin PATH]' if command == 'report' else '')+'\n'
    if command is None or source_command(command):
        text += '\n[source] is a .thor/.odin file or package directory.\nIt defaults to . (the current directory).\nFile inputs are self-contained packages.\n'
    if command:
        text += '\n'+next(c['description'] for c in CATALOG['commands'] if c['name'] == command)+'\n'
    text += '\n'
    documented = set()
    for section in CATALOG['sections']:
        if command is None or command in section['commands']:
            # The reference implementation keeps its established output directory.
            section_text = section['text'].replace('.thor-rust-build/<source>', '.thor-build/<source>')
            text += section_text+'\n'
            for line in section_text.splitlines():
                match = re.match(r'\s+-([^: \[]+)', line)
                if match:
                    documented.add(match[1])
    if full:
        text += 'Compatible compiler options:\n  Diagnostics refer to original project files.\n  Native vet/style policies retain native diagnostics on original source scopes.\n  Thor --vet findings are errors for build and warnings otherwise.\n\n'
        known = set()
        for flag in CATALOG['native_flags']:
            scope = 'build' if command == 'migrate' else command
            if (scope is not None and scope not in flag['commands']) or flag['name'] in documented:
                continue
            if command == 'migrate' and flag['name'] in {'file','out','build-mode','export-dependencies','export-dependencies-file','doc-format','keep-temp-files','export-defineables','export-timings','export-timings-file','export-linked-libs-file'}:
                continue
            known.add(flag['name'])
            text += '  '+flag['syntax']
            if command is None:
                text += '  ['+', '.join(flag['commands'])+']'
            if flag['internal']:
                text += '  [compiler development]'
            text += '\n'+''.join('      '+line.rstrip()+'\n' for line in flag['description'].splitlines())
            text += '\n'
        native = shutil.which(odin)
        if native:
            action = {'vet':'check','publish':'build','migrate':'build'}.get(command, command)
            for action in ([action] if action else ['build','check','test','doc','bundle']):
                output = subprocess.run([native, 'help', action], capture_output=True, text=True)
                include = False
                for line in output.stdout.splitlines():
                    if line.startswith('\t-'):
                        name = re.split(r'[: \t]', line.lstrip('\t-'), 1)[0]
                        include = name != 'help' and name not in documented and name not in known
                        if command == 'migrate' and name in {'file','out','build-mode','export-dependencies','export-dependencies-file','doc-format','keep-temp-files','export-defineables','export-timings','export-timings-file','export-linked-libs-file'}:
                            include = False
                        if include:
                            known.add(name)
                    if include:
                        text += line+'\n'
        if command is None or source_command(command) and command != 'ast':
            text += '\nAdvanced toolchain and editor options:\n  --editor-info                Include editor metadata in JSON diagnostics\n  --odin-compatible            Preserve Odin command defaults and native policy checks\n  --capabilities               Print native compiler capabilities as JSON\n\n'
    text += 'Help and version:\n  thor help [command] [-full]   Show standard or exhaustive help\n  -help                        Compatible command-help spelling\n  --version                    Same as thor version\n'
    if not full:
        text += '\nUse thor help'+(' '+command if command else '')+' -full for all '+('command ' if command else '')+'options.\n'
    if command is None:
        text += '\nExamples:\n  thor new my_project --open\n  thor run\n  thor build -o:speed\n  thor run -debug\n  thor vet --ask\n  thor publish --app-type console\n  thor run -- first_argument\n'
    return text


def usage_error(message):
    print(USAGE+'\nthor: error: '+message+'\nRun thor help for help.', file=sys.stderr)
    return 2


def early(args, version):
    if not args:
        print(overview(version), end='')
        return 0
    tokens, skip = [], False
    for arg in args:
        if arg == '--': break
        if skip:
            skip = False
        elif arg in VALUE_OPTIONS:
            skip = True
        else:
            tokens.append(arg)
    if any(arg in {'-h','--help'} for arg in tokens):
        return usage_error('use thor help or thor help <command>; add -full for every option')
    request = args[0] == 'help'
    compatible = '-help' in tokens
    if request or compatible:
        selected = None
        if request:
            rest = iter(args[1:])
            for arg in rest:
                if arg == '-full': continue
                if arg == '--odin':
                    if next(rest, None) is None: return usage_error('--odin requires a path')
                    continue
                if arg.startswith('--odin='): continue
                if selected is not None or not any(c['name'] == arg for c in CATALOG['commands']):
                    return usage_error(f"unknown help topic or extra argument '{arg}'")
                selected = arg
        elif args[0] != '-help':
            selected = args[0]
            if not any(c['name'] == selected for c in CATALOG['commands']):
                return usage_error(f"unknown command '{selected}'")
        print(render(version, selected, '-full' in tokens, odin_option(args)), end='')
        return 0
    if '-full' in tokens:
        return usage_error('-full is a thor help option')
    if args in [['--version'], ['version']]:
        print('Thor '+version)
        return 0
    command = args[0]
    if command in {'version','root'}:
        if len(args) != 1: return usage_error(f'thor {command} takes no arguments')
        print(str(Path(os.environ.get('THOR_ROOT', ROOT)).resolve()).rstrip(os.sep)+os.sep, end='')
        return 0
    if command == 'report':
        rest = iter(args[1:])
        for arg in rest:
            if arg == '--odin':
                if next(rest, None) is None: return usage_error('--odin requires a path')
            elif not arg.startswith('--odin='):
                return usage_error(f"unexpected report argument '{arg}'")
        print(f'Thor: {version}\nThor root: {ROOT}\nHost: {sys.platform}_{os.uname().machine if hasattr(os,"uname") else os.name}')
        native = shutil.which(odin_option(args))
        if native:
            print('Native compiler: '+str(Path(native).resolve()))
            for action in ['version','root','report']:
                output = subprocess.run([native, action], capture_output=True, text=True)
                if action == 'report':
                    print(output.stdout.split('Useful information to add to a bug report:')[-1].strip())
                else:
                    print(action+': '+output.stdout.strip())
        else:
            print('Native compiler unavailable: '+odin_option(args))
        return 0
    if command == 'bundle':
        if len(args)<2 or args[1] != 'android':
            return usage_error('use thor bundle android [directory] [options]')
        native = shutil.which(odin_option(args))
        if not native:
            print('thor Error: Odin compiler not found: '+odin_option(args), file=sys.stderr)
            return 1
        rest = iter(args[2:]); directory = None; flags = []
        for arg in rest:
            if arg == '--odin':
                if next(rest, None) is None: return usage_error('--odin requires a path')
            elif arg.startswith('--odin='): continue
            elif not arg.startswith('-') and directory is None: directory = arg
            else: flags.append(arg)
        return subprocess.run([native,'bundle','android',directory or '.',*flags]).returncode
    return None


def normalize(options, flags, entry, overlays):
    if options.raw_diagnostics and options.command in {'doc','strip-semicolon'}:
        raise ValueError('source tools require mapped output without --raw-diagnostics')
    if '-file' in flags:
        if not entry.is_file() and entry not in overlays:
            raise ValueError('-file requires a .thor or .odin file input')
        flags[:] = [flag for flag in flags if flag != '-file']
    for prefix in ['-build-mode:', '-out:', '-thread-count:']:
        if sum(flag.startswith(prefix) for flag in flags)>1:
            raise ValueError(prefix.rstrip(':')+' may only be supplied once')
    mode = next((f.split(':',1)[1] for f in flags if f.startswith('-build-mode:')), None)
    if mode is not None:
        if options.command == 'run' and mode != 'exe':
            raise ValueError('thor run requires an executable; use thor build for other build modes')
        if options.command == 'test' and mode != 'test':
            raise ValueError('thor test requires -build-mode:test')
        if options.command not in {'build','run','test','publish'}:
            raise ValueError('-build-mode is only supported by build, run, test and publish')
        if options.command in {'test','run'} or options.command == 'publish' and mode == 'exe':
            flags[:] = [flag for flag in flags if not flag.startswith('-build-mode:')]
    if options.command == 'transpile' and any(f.startswith(('-vet','-strict-style')) for f in flags):
        raise ValueError('source checks require thor check or thor vet')
    if options.command == 'strip-semicolon' and overlays:
        raise ValueError('strip-semicolon requires saved source files without --overlay')
    for flag in flags:
        name = flag.lstrip('-').split(':')[0]
        info = next((f for f in CATALOG['native_flags'] if f['name']==name), None)
        parser_setting = options.command == 'ast' and name in {'target','collection'}
        helper_setting = options.command == 'transpile' and name not in {'out','build-mode','all-packages','print-linker-flags','keep-executable','export-dependencies','export-dependencies-file'}
        if info and not parser_setting and not helper_setting and options.command not in info['commands']:
            raise ValueError(f'-{name} is not supported by thor {options.command}; use thor help {options.command} -full for applicable options')
        if name == 'thread-count' and (not flag.split(':')[-1].isdigit() or int(flag.split(':')[-1])<1):
            raise ValueError('-thread-count requires a positive integer')


def native_source_check(name):
    info = next((flag for flag in CATALOG['native_flags'] if flag['name'] == name), None)
    return info.get('source_check', False) if info is not None else name.startswith('vet-')


def helper_flag(flag):
    return not flag.startswith(('-show-', '-export-', '-vet', '-strict-style', '-build-diagnostics', '-print-linker-flags', '-short', '-in-source-order', '-doc-format', '-all-packages', '-keep-temp-files'))


def query(flags, odin):
    if not any(f in {'-target:?', '-microarch:?', '-target-features:?'} for f in flags):
        return None
    result = subprocess.run([odin,'build','.',*[f for f in flags if f.startswith(('-target:','-microarch:','-target-features:','-subtarget:'))]], capture_output=True)
    output = result.stdout+result.stderr
    listing=any(output.startswith(prefix) for prefix in [b'All supported targets:', b'Possible -microarch values', b'Possible -target-features'])
    sys.stdout.buffer.write(result.stdout)
    (sys.stdout.buffer if listing else sys.stderr.buffer).write(result.stderr)
    return 0 if listing else result.returncode


def resolve_entry(entry, flags, odin):
    text=str(entry)
    if entry.exists() or ':' not in text or os.name=='nt' and len(text)>1 and text[1]==':':return entry.resolve()
    collection, relative=text.split(':',1)
    custom={f.removeprefix('-collection:').split('=',1)[0]:f.split('=',1)[1] for f in flags if f.startswith('-collection:') and '=' in f}
    if collection in custom:base=Path(custom[collection])
    elif collection=='thor':base=ROOT/'runtime'
    elif collection in {'core','base','vendor'}:
        result=subprocess.run([odin,'root'],capture_output=True,text=True,check=True)
        base=Path(result.stdout.strip())/collection
    else:raise ValueError(f"unknown library collection '{collection}'")
    return (base/relative).resolve()
