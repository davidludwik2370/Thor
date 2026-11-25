import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

from tools import reset_thor_test_vm as reset


class VMResetTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.home=Path(temporary.name);self.repo=self.home/'Thor';self.repo.mkdir()
        self.prefix=self.home/'.local/share/thor'
        self.write('.config/thor-validation/test-vm.json',{'schema':1,'purpose':'Thor install reset test','home':str(self.home)})
        self.write('.local/share/thor/.thor-installation.json',{'schema':1,'managed_by':'thor-install'})
        self.write('.config/thor/installation.json',{'schema':1,'managed_by':'thor-install','prefix':str(self.prefix)})
        self.write('Thor/release/version','0.41.6')
        self.bundle=self.repo/'dist/thor-0.41.6-linux-aarch64.tar.gz';self.bundle.parent.mkdir()
        manifest={'product':'Thor','target':'linux-aarch64','files':{'ols/src/main.odin':{'size':12}}}
        with tarfile.open(self.bundle,'w:gz') as tar:
            data=json.dumps(manifest).encode();info=tarfile.TarInfo('THOR-DISTRIBUTION.json');info.size=len(data)
            tar.addfile(info,io.BytesIO(data))
        Path(str(self.bundle)+'.sha256').write_text(hashlib.sha256(self.bundle.read_bytes()).hexdigest())
    def write(self,name,value):
        path=self.home/name;path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps(value) if not isinstance(value,str) else value)
        return path
    def validate(self):
        with patch.object(reset.platform,'machine',return_value='aarch64'):
            return reset.validate(self.home,self.repo)
    def test_preflight_validates_authorization_and_bundle_without_mutating(self):
        original={str(p.relative_to(self.home)):p.read_bytes() for p in self.home.rglob('*') if p.is_file()}
        value=self.validate();self.assertEqual(value,self.bundle)
        self.assertEqual({str(p.relative_to(self.home)):p.read_bytes() for p in self.home.rglob('*') if p.is_file()},original)
        self.write('.config/thor-validation/test-vm.json',{})
        with self.assertRaisesRegex(RuntimeError,'authorized'):self.validate()
    def test_preflight_rejects_modified_bundle_and_unknown_prefix(self):
        self.write('.config/thor/installation.json',{'schema':1,'managed_by':'thor-install','prefix':str(self.home/'other')})
        with self.assertRaisesRegex(RuntimeError,'owner/prefix'):self.validate()
        self.write('.config/thor/installation.json',{'schema':1,'managed_by':'thor-install','prefix':str(self.prefix)})
        self.bundle.write_bytes(self.bundle.read_bytes()+b'changed')
        with self.assertRaisesRegex(RuntimeError,'checksum'):self.validate()
        self.assertTrue(self.prefix.exists())
    def test_reset_archives_only_managed_data_and_preserves_projects_and_idle_preferences(self):
        self.write('.config/Code/User/globalStorage/storage.json',{
            'userDataProfiles':[{'name':'Thor','location':'thor-id'},{'name':'Work','location':'work-id'}],
            'profileAssociations':{'workspaces':{'project':'thor-id','other':'work-id'}},'keep':123})
        self.write('.config/Code/User/profiles/thor-id/settings.json','original Thor settings')
        other=self.write('.config/Code/User/profiles/work-id/settings.json','unrelated settings')
        self.write('.vscode/extensions/extensions.json',[{'identifier':{'id':'thor-local.thor-language'}},{'identifier':{'id':'example.python'}}])
        self.write('.vscode/extensions/.obsolete',{'thor-local.thor-language-0.1':True,'example.python-1':True})
        self.write('.vscode/extensions/thor-local.thor-language-0.1/package.json','Thor extension')
        extension=self.write('.vscode/extensions/example.python-1/package.json','unrelated extension')
        project=self.write('Thor/demo/main.thor','user project')
        idle=self.write('.config/thor-validation/vm-idle-settings-before.json','idle preferences')
        self.write('.profile','before\n# >>> thor-install PATH >>>\nexport PATH=somewhere\n# <<< thor-install PATH <<<\nafter\n')
        backup=reset.Backup(self.home);reset.reset(self.home,backup)
        self.assertFalse(self.prefix.exists());self.assertFalse((self.home/'.config/thor').exists())
        self.assertEqual(project.read_text(),'user project');self.assertEqual(idle.read_text(),'idle preferences')
        self.assertEqual(other.read_text(),'unrelated settings');self.assertEqual(extension.read_text(),'unrelated extension')
        self.assertEqual((self.home/'.profile').read_text(),'before\nafter\n')
        storage=reset.read(self.home/'.config/Code/User/globalStorage/storage.json')
        self.assertEqual(storage['userDataProfiles'],[{'name':'Work','location':'work-id'}])
        self.assertEqual(storage['profileAssociations']['workspaces'],{'other':'work-id'});self.assertEqual(storage['keep'],123)
        self.assertEqual((backup.directory/'home/.config/Code/User/profiles/thor-id/settings.json').read_text(),'original Thor settings')
        self.assertEqual(reset.read(self.home/'.vscode/extensions/extensions.json'),[{'identifier':{'id':'example.python'}}])
    def test_string_encoded_profiles_remain_string_encoded(self):
        self.write('.config/Code/User/globalStorage/storage.json',{'userDataProfiles':json.dumps([{'name':'Thor','location':'123'}])})
        backup=reset.Backup(self.home);reset.reset(self.home,backup)
        value=reset.read(self.home/'.config/Code/User/globalStorage/storage.json')['userDataProfiles']
        self.assertIsInstance(value,str);self.assertEqual(json.loads(value),[])
    def test_malformed_path_block_fails_before_reset(self):
        self.write('.profile','# >>> thor-install PATH >>>\nunfinished\n')
        with self.assertRaisesRegex(RuntimeError,'Incomplete'):self.validate()
        self.assertTrue(self.prefix.exists())
    def test_symlinked_prefix_is_preserved(self):
        original=self.home/'original-prefix';self.prefix.rename(original);self.prefix.symlink_to(original,target_is_directory=True)
        with self.assertRaisesRegex(RuntimeError,'symlink'):self.validate()
        self.assertTrue(original.exists())
    def test_local_release_server_only_exposes_bundle_and_checksum(self):
        log=io.StringIO()
        with reset.local_release(self.bundle,log) as url:
            with urllib.request.urlopen(url+'/'+self.bundle.name) as response:data=response.read()
            self.assertEqual(data,self.bundle.read_bytes())
            with self.assertRaises(urllib.error.HTTPError):urllib.request.urlopen(url+'/../../.profile')
            with self.assertRaises(urllib.error.HTTPError):urllib.request.urlopen(url+'/')
    def test_execute_keeps_exact_installer_arguments(self):
        script=self.write('Thor/thor-install','#!/bin/sh\nprintf "%s\\n" "$@" > args.txt\nprintf "%s" "$THOR_RELEASE_BASE" > mirror.txt\n')
        script.chmod(0o755);log=io.StringIO()
        reset.execute(['./thor-install','--vscode','--profile','Thor'],self.repo,{'THOR_RELEASE_BASE':'http://127.0.0.1:1234','PATH':'/usr/bin:/bin'},log)
        self.assertEqual((self.repo/'args.txt').read_text().splitlines(),['--vscode','--profile','Thor'])
        self.assertEqual((self.repo/'mirror.txt').read_text(),'http://127.0.0.1:1234')


if __name__=='__main__':unittest.main()
