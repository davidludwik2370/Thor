"""Native resources have explicit owned origins, matching cleanup and views."""
import unittest
from tests import test_lifetime_vet as base


class ResourceContractTests(unittest.TestCase):
    setUpClass=classmethod(base.LifetimeVetTests.setUpClass.__func__)
    setUp=base.LifetimeVetTests.setUp
    invoke=base.LifetimeVetTests.invoke
    vet=base.LifetimeVetTests.vet
    codes=base.LifetimeVetTests.codes

    def issue(self,codes):
        self.assertTrue(any(c.startswith('thor/lifetime') for c in codes),codes)

    def test_open_create_aliases_and_explicit_cleanup(self):
        for name in ['open','create']:
            self.assertEqual(self.codes('acquire::os.'+name+'\nrelease::os.close\nmain::proc(){f:=acquire("lifetime-contract-test.tmp") or_panic;defer release(f);buffer:[16]byte;_,_=os.read(f,buffer[:])}',prefix='import "core:os"'),[])

    def test_closed_files_and_double_closes_still_warn(self):
        for use in ['_=os.close(f)','buffer:[16]byte;_,_=os.read(f,buffer[:])']:
            self.issue(self.codes('main::proc(){f:=os.open("file") or_panic;_=os.close(f);'+use+'}',prefix='import "core:os"'))

    def test_file_owner_cannot_be_dropped_or_freed_with_memory_delete(self):
        self.assertIn('thor/leak-unknown',self.codes('main::proc(){f:=os.open("file") or_panic;_=f}',prefix='import "core:os"'))
        self.issue(self.codes('main::proc(){f:=os.open("file") or_panic;free(f)}',prefix='import "core:os"'))

    def test_file_return_transfers_the_same_obligation(self):
        self.assertEqual(self.codes('acquire::proc()->^os.File{return os.open("file") or_panic}\nmain::proc(){f:=acquire();_=os.close(f)}',prefix='import "core:os"'),[])
        self.assertTrue(any(c.startswith('thor/leak') for c in self.codes('acquire::proc()->^os.File{return os.open("file") or_panic}\nmain::proc(){_=acquire()}',prefix='import "core:os"')))

    def test_custom_stream_and_copied_file_do_not_gain_native_cleanup_contracts(self):
        self.assertIn('thor/lifetime-unknown',self.codes('work::proc(f:^os.File){_=os.close(f)}\nmain::proc(){}',prefix='import "core:os"'))
        self.assertIn('thor/lifetime-unknown',self.codes('main::proc(){f:=os.open("file") or_panic;f.stream.procedure=nil;_=os.close(f)}',prefix='import "core:os"'))
        self.assertIn('thor/lifetime-unknown',self.codes('main::proc(){f:=os.open("file") or_panic;copy:=f^;_=os.close(&copy);_=os.close(f)}',prefix='import "core:os"'))

    def test_file_path_lifetime_is_checked(self):
        self.issue(self.codes('main::proc(){path:=strings.clone("file");delete(path);f:=os.open(path) or_panic;_=os.close(f)}',prefix='import "core:os"\nimport "core:strings"'))

    def test_standard_streams_have_borrowed_storage(self):
        self.assertEqual(self.codes('main::proc(){buffer:[8]byte;_,_=os.read(os.stdin,buffer[:]);_,_=os.write(os.stdout,buffer[:])}',prefix='import "core:os"'),[])
        self.issue(self.codes('main::proc(){free(os.stdin)}',prefix='import "core:os"'))

    def test_native_process_arguments_are_borrowed_and_reassignments_stay_tracked(self):
        self.assertEqual(self.codes('main::proc(){if len(os.args)>1{fmt.println(os.args[1])}}',prefix='import "core:os"\nimport "core:fmt"'),[])
        self.issue(self.codes('main::proc(){v:=make([]string,2);os.args=v;delete(v);_=os.args[0]}',prefix='import "core:os"'))
        self.issue(self.codes('main::proc(){delete(os.args)}',prefix='import "core:os"'))

    def test_qualified_globals_keep_retained_aliases_and_integer_addresses(self):
        files={'state.thor':'package main\nsaved:[]int\nbits:uintptr\n'}
        package=self.app/'other'
        package.mkdir(exist_ok=True)
        (package/'other.thor').write_text('package other\nsaved:[]int\nbits:uintptr\n')
        self.issue(self.codes('main::proc(){v:=make([]int,2);other.saved=v;delete(v);_=other.saved[0]}',prefix='import "other"',files=files))
        self.issue(self.codes('main::proc(){p:=new(int);other.bits=uintptr(p);free(p);_=((^int)(other.bits))^}',prefix='import "other"',files=files))

    def test_image_acquisition_export_and_cleanup(self):
        self.assertEqual(self.codes('acquire::rl.LoadImageFromTexture\nrelease::rl.UnloadImage\nmain::proc(){image:=acquire(rl.Texture2D{});rl.ImageFlipVertical(&image);_=rl.ExportImage(image,"file.png");release(image)}',prefix='import rl "vendor:raylib"'),[])

    def test_image_owner_and_matching_unload_are_required(self):
        self.assertIn('thor/leak-unknown',self.codes('main::proc(){image:=rl.LoadImageFromTexture(rl.Texture2D{});_=image}',prefix='import rl "vendor:raylib"'))
        self.issue(self.codes('main::proc(){image:=rl.LoadImageFromTexture(rl.Texture2D{});free(image.data);rl.UnloadImage(image)}',prefix='import rl "vendor:raylib"'))
        findings=self.vet('main::proc(){image:=rl.LoadImageFromTexture(rl.Texture2D{});free(image.data);rl.UnloadImage(image)}',prefix='import rl "vendor:raylib"')
        self.assertTrue(all(f['certainty']=='unverified' for f in findings),findings)

    def test_shallow_image_copies_still_share_pixel_lifetime(self):
        self.issue(self.codes('main::proc(){image:=rl.LoadImageFromTexture(rl.Texture2D{});alias:=image;rl.UnloadImage(image);_=rl.ExportImage(alias,"file.png")}',prefix='import rl "vendor:raylib"'))
        self.issue(self.codes('main::proc(){image:=rl.LoadImageFromTexture(rl.Texture2D{});rl.UnloadImage(image);rl.UnloadImage(image)}',prefix='import rl "vendor:raylib"'))

    def test_image_flip_does_not_hide_retained_pixel_views(self):
        self.assertIn('thor/lifetime-unknown',self.codes('main::proc(){image:=rl.LoadImageFromTexture(rl.Texture2D{});alias:=image;rl.ImageFlipVertical(&image);_=rl.ExportImage(alias,"file.png");rl.UnloadImage(image)}',prefix='import rl "vendor:raylib"'))

    def test_image_export_checks_path_and_custom_callback_boundaries(self):
        self.issue(self.codes('main::proc(){image:=rl.LoadImageFromTexture(rl.Texture2D{});path:=strings.clone_to_cstring("file.png");delete(path);_=rl.ExportImage(image,path);rl.UnloadImage(image)}',prefix='import rl "vendor:raylib"\nimport "core:strings"'))
        self.assertIn('thor/lifetime-unknown',self.codes('work::proc(callback:rl.SaveFileDataCallback){rl.SetSaveFileDataCallback(callback);image:=rl.LoadImageFromTexture(rl.Texture2D{});_=rl.ExportImage(image,"file.png");rl.UnloadImage(image)}\nmain::proc(){}',prefix='import rl "vendor:raylib"'))
        codes=self.codes('work::proc(callback:rl.SaveFileDataCallback){rl.SetSaveFileDataCallback(callback);image:=rl.LoadImageFromTexture(rl.Texture2D{});_=image}\nmain::proc(){}',prefix='import rl "vendor:raylib"')
        self.assertTrue(any(c.startswith('thor/leak') for c in codes),codes)


if __name__=='__main__':unittest.main()
