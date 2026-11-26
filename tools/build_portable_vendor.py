#!/usr/bin/env python3
"""Refresh only pinned graphics libraries in an owned, disposable SDK checkout."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from install import source_tools


def run(args,**kwargs):
    print('+ '+' '.join(map(str,args)),flush=True)
    subprocess.run(list(map(str,args)),check=True,**kwargs)


def raygui_arm_binding(text):
    original='''} else when ODIN_OS == .Linux  {
\tforeign import lib {
\t\t"linux/libraygui.so" when RAYGUI_SHARED else "linux/libraygui.a",
\t}
'''
    replacement='''} else when ODIN_OS == .Linux  {
\twhen ODIN_ARCH == .arm64 {
\t\tforeign import lib {
\t\t\t"linux-arm64/libraygui.so" when RAYGUI_SHARED else "linux-arm64/libraygui.a",
\t\t}
\t} else {
\t\tforeign import lib {
\t\t\t"linux/libraygui.so" when RAYGUI_SHARED else "linux/libraygui.a",
\t\t}
\t}
'''
    if text.count(original)!=1:raise ValueError('Reviewed Raygui Linux import block changed; review the ARM64 adapter')
    return text.replace(original,replacement)


def build(sdk,work):
    config=json.loads((ROOT/'install/linux-vendor.json').read_text())
    roots={}
    for name in ('raylib','raygui'):
        item=config[name];archive=work/(name+'.tar.gz')
        source_tools.download(item['url'],archive,item['sha256'])
        roots[name]=source_tools.extract_tar(archive,work/name,item['root'],select=lambda p:not p.parts or p.parts[0] in ('src','cmake','CMakeLists.txt','LICENSE'))
        archive.unlink()
    system=platform.system();arch=platform.machine();arm=arch in ('arm64','aarch64')
    if system not in ('Linux','Darwin'):raise ValueError('Portable vendor refresh supports only Linux/macOS')
    vendor=sdk/'vendor/raylib'
    native=vendor/('linux-arm64' if arm else 'linux') if system=='Linux' else vendor/'macos'
    native.mkdir(exist_ok=True)
    env=dict(os.environ)
    if system=='Darwin':env['MACOSX_DEPLOYMENT_TARGET']=(ROOT/'release/macos-minimum').read_text().strip()
    if system=='Linux':
        for shared in (False,True):
            target=work/('raylib-shared' if shared else 'raylib-static')
            run(['cmake','-S',roots['raylib'],'-B',target,'-DCMAKE_BUILD_TYPE=Release','-DBUILD_EXAMPLES=OFF',
                 '-DBUILD_SHARED_LIBS='+('ON' if shared else 'OFF'),'-DGLFW_BUILD_X11=ON','-DGLFW_BUILD_WAYLAND=ON'],env=env)
            run(['cmake','--build',target,'--parallel','2'],env=env)
            name='libraylib.so.600' if shared else 'libraylib.a'
            produced=target/'raylib'/name
            if not produced.is_file():raise ValueError('Pinned Raylib build did not produce '+name)
            shutil.copy2(produced,native/name)
    header=roots['raygui']/'src/raygui.h'
    unit=work/'raygui.c';unit.write_text('#define RAYGUI_IMPLEMENTATION\n#include "raygui.h"\n')
    suffix='-arm64' if system=='Darwin' and arm else ''
    static='libraygui'+suffix+'.a';shared='libraygui'+suffix+('.dylib' if system=='Darwin' else '.so')
    compiler=shutil.which('clang') or 'cc'
    common=[compiler,'-O3','-I'+str(header.parent),'-I'+str(roots['raylib']/'src')]
    obj=work/'raygui-static.o';run([*common,'-c',unit,'-o',obj],env=env)
    run(['ar','rcs',native/static,obj],env=env)
    library=native/('libraylib.600.dylib' if system=='Darwin' else 'libraylib.so.600')
    flags=['-dynamiclib','-Wl,-install_name,@rpath/'+shared,'-Wl,-rpath,@loader_path'] if system=='Darwin' else ['-shared','-Wl,-soname,'+shared,'-Wl,-rpath,$ORIGIN']
    run([*common,'-fPIC',*flags,unit,library,'-lm','-o',native/shared],env=env)
    if system=='Linux' and arm:
        binding=vendor/'raygui.odin';original=binding.read_text()
        if hashlib.sha256(original.encode()).hexdigest()!=config['raygui_binding_sha256']:raise ValueError('Reviewed Raygui bindings changed')
        binding.write_text(raygui_arm_binding(original))
    notices=vendor/'portable-build-notices';notices.mkdir(exist_ok=True)
    for name,root in roots.items():
        shutil.copy2(root/'LICENSE',notices/(name+'-LICENSE'))
    shutil.copy2(header,notices/'raygui.h')
    # External headers carry their own dependency notices. Retain them verbatim.
    shutil.copytree(roots['raylib']/'src/external',notices/'raylib-external',
                    ignore=shutil.ignore_patterns('*.c','*.o','*.a','*.so','*.png','*.jpg'))
    (notices/'SOURCE.json').write_text(json.dumps(config,indent=2)+'\n')
    fixture=work/'vendor_smoke.odin'
    fixture.write_text('''package main
import rl "vendor:raylib"
main :: proc() {
    color := rl.ColorFromHSV(0, 1, 1)
    if color.r != 255 || color.g != 0 || color.b != 0 { panic("Raylib color ABI") }
    if !rl.IsFileExtension("test.thor", ".thor") { panic("Raylib string ABI") }
    if rl.GuiGetState() != 0 { panic("Raygui ABI") }
}
''')
    runtime=dict(env)
    key='DYLD_LIBRARY_PATH' if system=='Darwin' else 'LD_LIBRARY_PATH'
    runtime[key]=str(native)+(os.pathsep+runtime[key] if runtime.get(key) else '')
    for shared_mode in (False,True):
        for unchecked in (False,True):
            output=work/('vendor-smoke-'+str(shared_mode)+'-'+str(unchecked))
            flags=['-o:speed','-disable-assert','-no-bounds-check','-no-type-assert'] if unchecked else []
            run([sdk/'odin','build',fixture,'-file','-thread-count:2','-define:RAYLIB_SHARED='+str(shared_mode).lower(),
                 '-define:RAYGUI_SHARED='+str(shared_mode).lower(),'-out:'+str(output),*flags],env=env)
            run([output],env=runtime)
    print('PASS: native Raylib/Raygui static/shared FFI in checked and unchecked builds',flush=True)


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--sdk',required=True,type=Path);o=p.parse_args()
    with tempfile.TemporaryDirectory(prefix='thor-portable-vendor-') as t:build(o.sdk.resolve(),Path(t))

if __name__=='__main__':main()
