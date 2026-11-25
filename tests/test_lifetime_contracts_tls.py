"""New proofs retain original call ranges and invalidate on unsaved edits."""
import unittest
from tests import test_tls as base


class LifetimeContractsTLSTests(unittest.TestCase):
    setUpClass=classmethod(base.TLSTests.setUpClass.__func__)
    open=base.TLSTests.open
    position=staticmethod(base.TLSTests.position)

    def change(self,client,path,text,version=2):
        client.notifications.clear()
        client.send('textDocument/didChange',{'textDocument':{'uri':path.as_uri(),'version':version},'contentChanges':[{'text':text}]})

    def test_recursive_reader_diagnostic_is_on_the_actual_call(self):
        text='// 😀\npackage main\nNode::struct{value:int,left,right:^Node}\nsum::proc(p:^Node)->int{if p==nil{return 0};return p.value+sum(p.left)+sum(p.right)}\nmain::proc(){child:=new(Node);root:=Node{left=child};_=sum(&root);free(child);_=sum(&root)}\n'
        client,path=self.open(text,checker_args='--vet=lifetime,leak --comptime=required --unverified')
        warnings=client.diagnostic(path.as_uri(),code='thor/lifetime')
        warning=next(w for w in warnings if w.get('code')=='thor/lifetime')
        needle='free(child);_=sum(&root)';offset=len('free(child);_=')
        self.assertEqual(warning['range']['start'],self.position(text,needle,offset))
        self.assertEqual(warning['range']['end'],self.position(text,needle,offset+len('sum(&root)')))
        self.change(client,path,text.replace('free(child);_=sum(&root)','_=sum(&root);free(child)'))
        client.diagnostic(path.as_uri(),empty=True)

    def test_exclusive_reset_loses_its_proof_when_an_alias_is_added(self):
        text='package main\nmain::proc(){v:=make([]int,2);defer delete(v);for _ in 0..<10{delete(v);v=make([]int,2);_=v[0]}}\n'
        client,path=self.open(text,checker_args='--vet=lifetime,leak --comptime=required --unverified')
        client.diagnostic(path.as_uri(),empty=True)
        changed=text.replace('for _ in','alias:=v;for _ in').replace('_=v[0]','_=alias[0]')
        self.change(client,path,changed)
        warnings=client.diagnostic(path.as_uri(),code='thor/lifetime')
        self.assertTrue(any(w.get('code')=='thor/lifetime' for w in warnings),warnings)
        self.assertNotIn('__thor_',str(warnings))
        self.change(client,path,text,3)
        client.diagnostic(path.as_uri(),empty=True)

    def test_closed_file_alias_reports_source_call_and_recovers(self):
        text='package main\nimport "core:os"\nrelease::os.close\nmain::proc(){f:=os.open("file") or_panic;buffer:[8]byte;_=release(f);_,_=os.read(f,buffer[:])}\n'
        client,path=self.open(text,checker_args='--vet=lifetime,leak --comptime=required --unverified')
        warnings=client.diagnostic(path.as_uri(),code='thor/lifetime-unknown')
        warning=next(w for w in warnings if w.get('code')=='thor/lifetime-unknown')
        self.assertEqual(warning['range']['start'],self.position(text,'os.read(f,buffer[:])',0))
        self.change(client,path,text.replace('_=release(f);_,_=os.read(f,buffer[:])','_,_=os.read(f,buffer[:]);_=release(f)'))
        client.diagnostic(path.as_uri(),empty=True)

    def test_shallow_image_copies_keep_the_unloaded_pixel_lifetime(self):
        text='package main\nimport rl "vendor:raylib"\nmain::proc(){image:=rl.LoadImageFromTexture(rl.Texture2D{});copy:=image;rl.UnloadImage(image);_=rl.ExportImage(copy,"file.png")}\n'
        client,path=self.open(text,checker_args='--vet=lifetime,leak --comptime=required --unverified')
        warnings=client.diagnostic(path.as_uri(),code='thor/lifetime-unknown')
        warning=next(w for w in warnings if w.get('code')=='thor/lifetime-unknown')
        self.assertEqual(warning['range']['start'],self.position(text,'rl.ExportImage(copy,"file.png")',0))
        self.change(client,path,text.replace('rl.UnloadImage(image);_=rl.ExportImage(copy,"file.png")','_=rl.ExportImage(copy,"file.png");rl.UnloadImage(image)'))
        client.diagnostic(path.as_uri(),empty=True)


if __name__=='__main__':unittest.main()
