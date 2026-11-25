"""Exercise the native installer as a process, including refusal and preservation paths."""
import hashlib
import functools
import http.server
import io
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import tarfile
import tempfile
import threading
import unittest

ROOT=Path(__file__).resolve().parents[1]
NATIVE_OLS=platform.system()=='Linux' and platform.machine() in ('arm64','aarch64')
INSTALLER=Path(os.environ.get('THOR_TEST_INSTALLER',ROOT/'install/target/release'/('thor-install.exe' if os.name=='nt' else 'thor-install')))


@unittest.skipIf(os.name=='nt','POSIX fixture executables; native Windows needs the real distribution')
class InstallerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not INSTALLER.is_file():
            subprocess.run(['cargo','build','--release','--locked','--offline','--manifest-path',str(ROOT/'install/Cargo.toml'),'-j','2'],check=True,capture_output=True)

    def setUp(self):
        temporary=tempfile.TemporaryDirectory(prefix='thor-install-test-')
        self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name).resolve()
        self.home=self.root/'home';self.home.mkdir()
        self.prefix=self.home/'Thor tools'
        self.env={**os.environ,'HOME':str(self.home),'XDG_CONFIG_HOME':str(self.home/'.config'),'SHELL':'/bin/bash'}
        self.env.pop('THOR_ROOT',None)
        self.env.pop('THOR_ODIN',None)
        self.env.pop('ODIN',None)
        self.env['OLS_TEST_BUILD_LOG']=str(self.root/'ols-builds')
        self.env['OLS_TEST_SERVER_LOG']=str(self.root/'ols-server-starts')
        self.registry=(self.home/'Library/Application Support/Thor/installation.json' if platform.system()=='Darwin' else self.home/'.config/thor/installation.json')

    @staticmethod
    def fake_odin():
        return b'''#!/bin/sh
if [ "$1" = build ]; then
    if [ -n "$OLS_TEST_BUILD_LOG" ]; then echo built >> "$OLS_TEST_BUILD_LOG"; fi
    if [ "$OLS_TEST_BUILD_FAIL" = 1 ]; then exit 19; fi
    for arg do
        case "$arg" in -out:*) output=${arg#-out:};; esac
    done
    printf '%s\\n' '#!/bin/sh' 'if [ -n "$OLS_TEST_SERVER_LOG" ]; then echo invoked >> "$OLS_TEST_SERVER_LOG"; fi' 'exit 0' > "$output"
    chmod +x "$output"
    if [ -n "$OLS_TEST_EDIT_SETTINGS" ]; then printf '{"user_edit":true}' > "$OLS_TEST_EDIT_SETTINGS"; fi
else
    echo "odin fixture"
fi
'''

    def bundle(self,*,files=None,extra=None,mutate=None,version='0.41.6',target=None):
        content={
            'thor':b'#!/bin/sh\ncase "$1" in version) echo "Thor 0.41.6";; run) echo "Thor installation OK: 42";; esac\n',
            'thor-install':INSTALLER.read_bytes(),
            'odin/odin':self.fake_odin(),
            'tls/bin/tls':b'#!/bin/sh\nexit 0\n',
            'frontend/main.odin':b'package main\n',
            'runtime/basic/basic.thor':b'package basic\n',
            'templates/main.thor':b'package main\n',
            'editors/thor-language.vsix':b'fixture VSIX',
            'ols/UPSTREAM.json':b'{"revision":"0c9438faeba846232caaa22fe9f22b30c302dba2"}',
            'ols/src/main.odin':b'package main\n',
            'ols/builtin/builtin.odin':b'package builtin\n',
            'ols/LICENSE':b'upstream license fixture',
            'install/profiles/Thor.code-profile':(ROOT/'install/profiles/Thor.code-profile').read_bytes(),
        }
        content.update(files or {})
        system={'Darwin':'macos','Linux':'linux'}[platform.system()]
        arch={'arm64':'aarch64','aarch64':'aarch64','x86_64':'x86_64'}[platform.machine()]
        manifest=dict(schema=1,product='Thor',version=version,target=target or system+'-'+arch,
                      files={k:dict(size=len(v),sha256=hashlib.sha256(v).hexdigest(),executable=k in ('thor','thor-install','odin/odin','tls/bin/tls')) for k,v in content.items()})
        if mutate:mutate(manifest,content)
        archive=self.root/f'bundle-{len(list(self.root.glob("bundle-*")))}.tar.gz'
        with tarfile.open(archive,'w:gz') as output:
            raw=json.dumps(manifest).encode();info=tarfile.TarInfo('THOR-DISTRIBUTION.json');info.size=len(raw);output.addfile(info,io.BytesIO(raw))
            for name,data in content.items():
                info=tarfile.TarInfo(name);info.size=len(data);info.mode=0o755
                output.addfile(info,io.BytesIO(data))
            for info,data in extra or []:output.addfile(info,io.BytesIO(data) if data is not None else None)
        archive.with_name(archive.name+'.sha256').write_text(hashlib.sha256(archive.read_bytes()).hexdigest()+'\n')
        return archive

    def invoke(self,bundle=None,*args,ok=True,bootstrap=False):
        command=[str(ROOT/'thor-install' if bootstrap else INSTALLER)]
        if bundle:command+=['--bundle',str(bundle)]
        command+=['--prefix',str(self.prefix),*args]
        result=subprocess.run(command,cwd=self.root,env=self.env,capture_output=True,text=True,timeout=30)
        if ok:self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        else:self.assertNotEqual(result.returncode,0,result.stdout+result.stderr)
        return result

    def test_install_relocation_reinstall_and_doctor_without_developer_tools(self):
        bundle=self.bundle()
        self.invoke(bundle,bootstrap=True)
        record=json.loads(self.registry.read_text())
        root=Path(record['root'])
        self.assertTrue((root/'frontend/main.odin').is_file())
        self.assertTrue((self.prefix/'bin/thor').is_symlink())
        self.assertTrue((self.prefix/'bin/odin').is_symlink())
        self.assertFalse((self.home/'.profile').exists())
        self.invoke(bundle)
        self.assertEqual(json.loads(self.registry.read_text())['root'],str(root))
        self.invoke(None,'--doctor')

    def test_dry_run_leaves_home_and_prefix_untouched(self):
        self.invoke(self.bundle(),'--dry-run')
        self.assertFalse(self.prefix.exists())
        self.assertFalse(self.registry.exists())

    def test_checksum_mismatch_is_rejected_before_installing(self):
        bundle=self.bundle()
        result=self.invoke(bundle,'--sha256','0'*64,ok=False)
        self.assertIn('SHA-256 mismatch',result.stderr)
        self.assertFalse(self.prefix.exists())

    def test_bootstrap_checks_checksum_before_executing_native_installer(self):
        result=self.invoke(self.bundle(),'--sha256','0'*64,ok=False,bootstrap=True)
        self.assertIn('SHA-256 mismatch',result.stderr)
        self.assertFalse(self.prefix.exists())

    def test_file_checksum_failure_is_rejected_even_with_valid_archive_checksum(self):
        bundle=self.bundle(mutate=lambda m,c:c.update({'thor':b'corrupted'}))
        result=self.invoke(bundle,ok=False)
        self.assertIn('incorrect file length',result.stderr)
        self.assertFalse(self.prefix.exists())

    def test_missing_files_and_wrong_platform_are_rejected(self):
        for mutate,target in [(lambda m,c:c.pop('templates/main.thor'),None),(None,'windows-x86_64')]:
            self.invoke(self.bundle(mutate=mutate,target=target),ok=False)
            self.assertFalse(self.prefix.exists())

    def test_archive_traversal_links_special_files_and_duplicates_are_rejected(self):
        for name,kind in [('../escape',tarfile.REGTYPE),('/absolute',tarfile.REGTYPE),('escape',tarfile.SYMTYPE),('device',tarfile.CHRTYPE),('thor',tarfile.REGTYPE),('THOR',tarfile.REGTYPE)]:
            info=tarfile.TarInfo(name);info.type=kind
            if kind==tarfile.SYMTYPE:info.linkname='../outside'
            result=self.invoke(self.bundle(extra=[(info,b'' if kind==tarfile.REGTYPE else None)]),ok=False)
            self.assertRegex(result.stderr,'archive entry|duplicate archive path|uninventoried')
            self.assertFalse(self.prefix.exists())

    def test_unmanaged_prefix_and_command_are_preserved(self):
        self.prefix.mkdir();sentinel=self.prefix/'notes';sentinel.write_text('keep')
        self.invoke(self.bundle(),ok=False)
        self.assertEqual(sentinel.read_text(),'keep')
        sentinel.unlink()
        bundle=self.bundle();self.invoke(bundle)
        command=self.prefix/'bin/thor';command.unlink();command.write_text('custom command')
        before=self.registry.read_bytes()
        self.invoke(bundle,ok=False)
        self.assertEqual(command.read_text(),'custom command')
        self.assertEqual(self.registry.read_bytes(),before)

    def test_modified_installed_files_are_not_overwritten(self):
        bundle=self.bundle();self.invoke(bundle)
        root=Path(json.loads(self.registry.read_text())['root'])
        (root/'templates/main.thor').write_text('custom template')
        self.invoke(bundle,ok=False)
        self.assertEqual((root/'templates/main.thor').read_text(),'custom template')

    def test_upgrades_retain_only_active_and_previous_unmodified_toolchains(self):
        installed=[]
        for version in ['0.41.6','0.41.7','0.41.8','0.41.9']:
            self.invoke(self.bundle(version=version))
            installed.append(Path(json.loads(self.registry.read_text())['root']))
        self.assertEqual([p.exists() for p in installed],[False,False,True,True])
        previous=json.loads((self.prefix/'previous-installation.json').read_text())
        self.assertEqual(previous['root'],str(installed[-2]))
        self.invoke(None,'--doctor')

    def test_upgrade_retention_preserves_user_edits_additions_and_links(self):
        self.invoke(self.bundle(version='0.41.6'))
        modified=Path(json.loads(self.registry.read_text())['root'])
        (modified/'templates/main.thor').write_text('user edit')
        self.invoke(self.bundle(version='0.41.7'))
        extra=Path(json.loads(self.registry.read_text())['root'])
        (extra/'notes.txt').write_text('user notes')
        self.invoke(self.bundle(version='0.41.8'))
        linked=Path(json.loads(self.registry.read_text())['root'])
        outside=self.root/'outside';outside.mkdir();sentinel=outside/'keep';sentinel.write_text('keep')
        (linked/'custom').symlink_to(outside,target_is_directory=True)
        self.invoke(self.bundle(version='0.41.9'))
        self.invoke(self.bundle(version='0.41.10'))
        self.assertEqual((modified/'templates/main.thor').read_text(),'user edit')
        self.assertEqual((extra/'notes.txt').read_text(),'user notes')
        self.assertTrue((linked/'custom').is_symlink())
        self.assertEqual(sentinel.read_text(),'keep')

    def test_upgrade_retention_accepts_compiler_created_frontends_and_runtime_views(self):
        self.invoke(self.bundle(version='0.41.6'))
        first=Path(json.loads(self.registry.read_text())['root'])
        cache=first/'.thor-cache';cache.mkdir()
        (cache/('frontend-'+'a'*20)).write_bytes(b'generated frontend')
        view=first/'lib'/('runtime-rust-'+'b'*24);view.mkdir(parents=True)
        (view/'thor-manifest.json').write_text(json.dumps({'engine':'rust','shared_runtime':'b'*64}))
        (view/'basic').symlink_to(first/'runtime/basic',target_is_directory=True)
        self.invoke(self.bundle(version='0.41.7'))
        self.invoke(self.bundle(version='0.41.8'))
        self.assertFalse(first.exists())

    def test_upgrade_retention_preserves_unknown_files_in_generated_directories(self):
        self.invoke(self.bundle(version='0.41.6'))
        first=Path(json.loads(self.registry.read_text())['root'])
        cache=first/'.thor-cache';cache.mkdir();(cache/'notes.txt').write_text('keep')
        self.invoke(self.bundle(version='0.41.7'))
        self.invoke(self.bundle(version='0.41.8'))
        self.assertEqual((cache/'notes.txt').read_text(),'keep')

    def test_failed_health_check_keeps_previous_activation(self):
        first=self.bundle();self.invoke(first)
        before=self.registry.read_bytes();link=os.readlink(self.prefix/'bin/thor')
        bad=self.bundle(files={'thor':b'#!/bin/sh\nexit 42\n'},version='0.41.7')
        self.invoke(bad,ok=False)
        self.assertEqual(self.registry.read_bytes(),before)
        self.assertEqual(os.readlink(self.prefix/'bin/thor'),link)
        self.assertFalse((self.prefix/'.install-lock').exists())

    def test_path_setup_is_optional_additive_backed_up_and_idempotent(self):
        profile=self.home/'.profile';profile.write_text('# custom shell configuration\nexport CUSTOM=kept\n')
        bundle=self.bundle();self.invoke(bundle,'--path');first=profile.read_bytes()
        self.invoke(bundle,'--path')
        self.assertEqual(profile.read_bytes(),first)
        self.assertIn('export CUSTOM=kept',first.decode())
        self.assertEqual(len(list(self.home.glob('.profile.thor-backup-*'))),1)

    def test_profile_requires_vscode_and_color_changes_require_named_new_profile(self):
        for args in [('--profile','Thor'),('--colors',),('--vscode','--profile','')]:
            self.invoke(None,*args,ok=False)
        self.assertFalse(self.prefix.exists())

    def fake_code(self,existing=False):
        data=self.root/'code data';extensions=self.root/'extensions';profile=data/'User/profiles/profile-id'
        if existing:
            profile.mkdir(parents=True)
            (profile/'settings.json').write_text('// custom comments\n{"workbench.colorTheme":"My Theme"}')
            (profile/'keybindings.json').write_text('[{"key":"cmd+r","command":"custom"}]')
            storage=data/'User/globalStorage/storage.json';storage.parent.mkdir(parents=True)
            storage.write_text(json.dumps({'userDataProfiles':[dict(location='profile-id',name='Thor')]}))
        default=data/'User/settings.json';default.parent.mkdir(parents=True,exist_ok=True);default.write_text('{"custom":true}')
        code=self.root/'code'
        # The fake only represents editor CLI behavior; real VS Code is exercised
        # separately in the clean Ubuntu integration runner.
        code.write_text('''#!/usr/bin/env python3
import json,os,sys
from pathlib import Path
a=sys.argv[1:];d=Path(a[a.index('--user-data-dir')+1]);log=d/'commands.jsonl'
with log.open('a') as f:f.write(json.dumps(a)+'\\n')
with (d/'environment.jsonl').open('a') as f:f.write(json.dumps({'PATH':os.environ.get('PATH','')})+'\\n')
if '--install-extension' in a and os.environ.get('FAIL_CODE_EXTENSION')==a[a.index('--install-extension')+1]:sys.exit(37)
if '--new-window' in a:
 p=d/'User/globalStorage/storage.json';p.parent.mkdir(parents=True,exist_ok=True)
 p.write_text(json.dumps({'userDataProfiles':[{'location':'profile-id','name':a[a.index('--profile')+1]}]}))
''')
        code.chmod(0o755)
        return code,data,extensions,profile

    def test_new_profile_uses_template_and_installs_all_three_extensions(self):
        code,data,extensions,profile=self.fake_code()
        self.invoke(self.bundle(),'--vscode','--profile','Thor','--code',str(code),'--code-user-data',str(data),'--extensions-dir',str(extensions))
        settings=json.loads((profile/'settings.json').read_text())
        expected={'workbench.colorTheme':'Dark Modern','thor.colors.recommended':True}
        if NATIVE_OLS:expected['ols.server.path']=str(next((self.prefix/'editor/ols').glob('*/ols')))
        self.assertEqual(settings,expected)
        self.assertEqual((data/'User/settings.json').read_text(),'{"custom":true}')
        commands=[json.loads(s) for s in (data/'commands.jsonl').read_text().splitlines()]
        self.assertEqual(len(commands),4)
        installed=[c[c.index('--install-extension')+1] for c in commands if '--install-extension' in c]
        self.assertEqual([Path(installed[0]).name,*installed[1:]],['thor-language.vsix','DanielGavin.ols','vadimcn.vscode-lldb'])
        for command in commands:
            self.assertEqual(command[command.index('--profile')+1],'Thor')
            self.assertEqual(command[command.index('--extensions-dir')+1],str(extensions))
        environments=[json.loads(s) for s in (data/'environment.jsonl').read_text().splitlines()]
        for environment in environments:
            entries=environment['PATH'].split(os.pathsep)
            self.assertEqual(entries[0],str(self.prefix/'bin'))
            self.assertEqual(entries[1:],self.env['PATH'].split(os.pathsep))

    def test_existing_profile_settings_and_keybindings_are_preserved_byte_for_byte(self):
        code,data,extensions,profile=self.fake_code(existing=True)
        before={p.name:p.read_bytes() for p in profile.iterdir()}
        self.invoke(self.bundle(),'--vscode','--profile','Thor','--colors','--code',str(code),'--code-user-data',str(data),'--extensions-dir',str(extensions))
        if NATIVE_OLS:
            self.assertEqual((profile/'keybindings.json').read_bytes(),before['keybindings.json'])
            self.assertIn(before['settings.json'].decode().rstrip('}'),(profile/'settings.json').read_text())
            self.assertIn('ols.server.path',(profile/'settings.json').read_text())
        else:self.assertEqual({p.name:p.read_bytes() for p in profile.iterdir()},before)
        self.assertEqual(len((data/'commands.jsonl').read_text().splitlines()),3)

    def test_new_profile_can_disable_colors(self):
        code,data,extensions,profile=self.fake_code()
        self.invoke(self.bundle(),'--vscode','--profile','Thor','--no-colors','--code',str(code),'--code-user-data',str(data))
        self.assertFalse(json.loads((profile/'settings.json').read_text())['thor.colors.recommended'])

    def test_default_profile_install_does_not_change_settings(self):
        code,data,extensions,profile=self.fake_code()
        self.invoke(self.bundle(),'--vscode','--code',str(code),'--code-user-data',str(data))
        self.assertFalse(profile.exists())
        if NATIVE_OLS:
            self.assertTrue(json.loads((data/'User/settings.json').read_text())['custom'])
            self.assertIn('ols.server.path',(data/'User/settings.json').read_text())
        else:self.assertEqual((data/'User/settings.json').read_text(),'{"custom":true}')
        commands=[json.loads(s) for s in (data/'commands.jsonl').read_text().splitlines()]
        self.assertEqual(len(commands),3)
        self.assertTrue(all('--profile' not in c for c in commands))
        self.assertTrue(any('DanielGavin.ols' in c for c in commands))

    def test_ols_extension_failure_keeps_toolchain_and_can_be_retried(self):
        code,data,extensions,profile=self.fake_code()
        self.env['FAIL_CODE_EXTENSION']='DanielGavin.ols'
        flags=('--vscode','--profile','Thor','--code',str(code),'--code-user-data',str(data))
        result=self.invoke(self.bundle(),*flags,ok=False)
        self.assertIn('editor setup is incomplete',result.stderr)
        self.assertTrue((self.prefix/'bin/thor').is_file())
        self.invoke(None,'--doctor')
        before=(profile/'settings.json').read_bytes()
        self.env.pop('FAIL_CODE_EXTENSION')
        self.invoke(None,*flags)
        self.assertEqual((profile/'settings.json').read_bytes(),before)

    def test_editor_launch_path_deduplicates_only_the_installed_bin(self):
        code,data,extensions,profile=self.fake_code(existing=True)
        original=self.env['PATH']
        self.env['PATH']=str(self.prefix/'bin')+os.pathsep+original
        self.invoke(self.bundle(),'--vscode','--profile','Thor','--code',str(code),'--code-user-data',str(data))
        environments=[json.loads(s) for s in (data/'environment.jsonl').read_text().splitlines()]
        self.assertEqual(len(environments),3)
        self.assertTrue(all(e['PATH']==self.env['PATH'] for e in environments))

    def test_editor_setup_can_be_added_to_an_existing_install_without_reacquiring_bundle(self):
        self.invoke(self.bundle())
        code,data,extensions,profile=self.fake_code()
        self.invoke(None,'--vscode','--profile','Thor','--code',str(code),'--code-user-data',str(data))
        self.assertTrue((profile/'settings.json').exists())

    @unittest.skipUnless(NATIVE_OLS,'native OLS fallback targets ARM64 Linux')
    def test_native_ols_build_is_cached_and_settings_keep_comments(self):
        code,data,extensions,profile=self.fake_code(existing=True)
        flags=('--vscode','--profile','Thor','--code',str(code),'--code-user-data',str(data))
        bundle=self.bundle();self.invoke(bundle,*flags)
        first=(profile/'settings.json').read_bytes()
        self.invoke(None,*flags)
        self.assertEqual((profile/'settings.json').read_bytes(),first)
        self.assertEqual((self.root/'ols-builds').read_text().splitlines(),['built'])
        self.assertFalse((self.root/'ols-server-starts').exists(),'Installer must never start an LSP server as a version probe')
        self.assertIn('// custom comments',first.decode())
        server=next((self.prefix/'editor/ols').glob('*/ols'))
        self.assertTrue((server.parent/'builtin/builtin.odin').is_file())
        config=json.loads((server.parent/'ols.json').read_text())
        self.assertTrue(config['enable_semantic_tokens'])
        self.assertEqual(Path(config['odin_command']),Path(json.loads(self.registry.read_text())['root'])/'odin/odin')

    @unittest.skipUnless(NATIVE_OLS,'native OLS fallback targets ARM64 Linux')
    def test_native_ols_preserves_custom_server_without_building(self):
        code,data,extensions,profile=self.fake_code(existing=True)
        settings=profile/'settings.json';settings.write_text('// keep\n{"ols.server.path":"/custom/ols", "other":[1,],}')
        before=settings.read_bytes()
        self.invoke(self.bundle(),'--vscode','--profile','Thor','--code',str(code),'--code-user-data',str(data))
        self.assertEqual(settings.read_bytes(),before)
        self.assertFalse((self.root/'ols-builds').exists())

    @unittest.skipUnless(NATIVE_OLS,'native OLS fallback targets ARM64 Linux')
    def test_native_ols_failure_can_be_retried_without_losing_cli_or_settings(self):
        code,data,extensions,profile=self.fake_code(existing=True)
        flags=('--vscode','--profile','Thor','--code',str(code),'--code-user-data',str(data))
        before=(profile/'settings.json').read_bytes();self.env['OLS_TEST_BUILD_FAIL']='1'
        result=self.invoke(self.bundle(),*flags,ok=False)
        self.assertIn('Native OLS build failed',result.stderr)
        self.assertEqual((profile/'settings.json').read_bytes(),before)
        self.assertFalse(list((self.prefix/'editor/ols').glob('.stage-*')))
        self.invoke(None,'--doctor');self.env.pop('OLS_TEST_BUILD_FAIL')
        self.invoke(None,*flags)
        self.assertIn('ols.server.path',(profile/'settings.json').read_text())

    @unittest.skipUnless(NATIVE_OLS,'native OLS fallback targets ARM64 Linux')
    def test_native_ols_modified_cache_is_preserved_and_reported(self):
        code,data,extensions,profile=self.fake_code()
        flags=('--vscode','--profile','Thor','--code',str(code),'--code-user-data',str(data))
        self.invoke(self.bundle(),*flags)
        server=Path(json.loads((profile/'settings.json').read_text())['ols.server.path'])
        server.write_text('user changed the cached server')
        result=self.invoke(None,*flags,ok=False)
        self.assertIn('cache was modified',result.stderr)
        self.assertEqual(server.read_text(),'user changed the cached server')

    @unittest.skipUnless(NATIVE_OLS,'native OLS fallback targets ARM64 Linux')
    def test_native_ols_upgrade_updates_only_installer_owned_server(self):
        code,data,extensions,profile=self.fake_code()
        flags=('--vscode','--profile','Thor','--code',str(code),'--code-user-data',str(data))
        self.invoke(self.bundle(),*flags);settings=profile/'settings.json';first=json.loads(settings.read_text())
        self.invoke(self.bundle(version='0.41.7'),*flags);second=json.loads(settings.read_text())
        self.assertNotEqual(first.pop('ols.server.path'),second.pop('ols.server.path'))
        self.assertEqual(first,second)

    @unittest.skipUnless(NATIVE_OLS,'native OLS fallback targets ARM64 Linux')
    def test_native_ols_invalid_settings_and_old_bundles_fail_without_overwriting(self):
        code,data,extensions,profile=self.fake_code(existing=True);settings=profile/'settings.json'
        settings.write_text('{not valid');before=settings.read_bytes()
        flags=('--vscode','--profile','Thor','--code',str(code),'--code-user-data',str(data))
        result=self.invoke(self.bundle(),*flags,ok=False)
        self.assertIn('invalid editor settings',result.stderr);self.assertEqual(settings.read_bytes(),before)
        settings.write_text('{}')
        result=self.invoke(self.bundle(mutate=lambda m,c:(c.pop('ols/UPSTREAM.json'),m['files'].pop('ols/UPSTREAM.json'))),*flags,ok=False)
        self.assertIn('lacks pinned upstream OLS source',result.stderr);self.assertEqual(settings.read_text(),'{}')

    @unittest.skipUnless(NATIVE_OLS,'native OLS fallback targets ARM64 Linux')
    def test_native_ols_preserves_settings_changed_while_compiling(self):
        code,data,extensions,profile=self.fake_code(existing=True)
        flags=('--vscode','--profile','Thor','--code',str(code),'--code-user-data',str(data))
        settings=profile/'settings.json';self.env['OLS_TEST_EDIT_SETTINGS']=str(settings)
        result=self.invoke(self.bundle(),*flags,ok=False)
        self.assertIn('settings changed during the OLS build',result.stderr)
        self.assertEqual(settings.read_text(),'{"user_edit":true}')
        self.env.pop('OLS_TEST_EDIT_SETTINGS');self.invoke(None,*flags)
        self.assertTrue(json.loads(settings.read_text())['user_edit'])
        self.assertEqual((self.root/'ols-builds').read_text().splitlines(),['built'])

    def test_unsupported_odin_paths_are_rejected_before_installation(self):
        self.prefix=self.home/"Thor O'Brien $(touch PWNED)"
        result=self.invoke(self.bundle(),'--path',bootstrap=True,ok=False)
        self.assertIn('Odin cannot build inside this installation path',result.stderr)
        self.assertFalse((self.root/'PWNED').exists())
        self.assertFalse(self.prefix.exists())

    def test_supported_spaces_and_unicode_in_installation_paths(self):
        self.prefix=self.home/'Thor café tools'
        self.invoke(self.bundle(),'--path',bootstrap=True)
        self.assertTrue((self.prefix/'bin/thor').exists())

    def test_activation_record_failure_does_not_replace_existing_commands(self):
        self.invoke(self.bundle())
        before=os.readlink(self.prefix/'bin/thor')
        self.registry.unlink();self.registry.parent.rmdir();self.registry.parent.write_text('not a directory')
        self.invoke(self.bundle(files={'templates/main.thor':b'new template'}),ok=False)
        self.assertEqual(os.readlink(self.prefix/'bin/thor'),before)

    def test_release_download_bootstrap_works_before_github_publication(self):
        bundle=self.bundle()
        with tarfile.open(bundle) as source:
            target=json.load(source.extractfile('THOR-DISTRIBUTION.json'))['target']
        releases=self.root/'releases';releases.mkdir()
        name='thor-0.41.6-'+target+'.tar.gz'
        shutil.copy2(bundle,releases/name)
        shutil.copy2(bundle.with_name(bundle.name+'.sha256'),releases/(name+'.sha256'))
        class QuietHandler(http.server.SimpleHTTPRequestHandler):
            def log_message(self,*args):pass
        server=http.server.ThreadingHTTPServer(('127.0.0.1',0),functools.partial(QuietHandler,directory=str(releases)))
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
        url=f'http://127.0.0.1:{server.server_port}'
        result=self.invoke(None,'--release-base',url,'--dry-run',bootstrap=True)
        self.assertIn('Dry run',result.stdout)
        self.assertFalse(self.prefix.exists())

    def test_unsupported_download_schemes_and_conflicting_doctor_options_are_rejected(self):
        result=self.invoke(None,'--release-base','file:///tmp/bad',ok=False,bootstrap=True)
        self.assertIn('HTTPS',result.stderr)
        for flags in [('--doctor','--dry-run'),('--vscode','--profile','Default')]:
            self.invoke(None,*flags,ok=False)


if __name__=='__main__':unittest.main()
