"""Fn call-chain diagnostics in VS Code's actual LSP representation."""
import json
import unittest
from tests import test_tls as base


class PureCallContextTLSTests(unittest.TestCase):
    setUpClass=classmethod(base.TLSTests.setUpClass.__func__)
    open=base.TLSTests.open
    position=staticmethod(base.TLSTests.position)

    def test_local_fixed_capacity_mutator_effect_and_unsaved_proc_recovery(self):
        text='''// 😀 source positions
package main
import "core:math/rand"
Deck::[dynamic;8]int
reset_shuffled::proc(deck:^Deck){clear(deck);append(deck,1,2,3);rand.shuffle(deck[:])}
simulate_game::fn(n:int)->int{
    deck:Deck
    /* 😀 */ reset_shuffled(&deck)
    return pop(&deck)+n
}
main::proc(){n:=7;_=simulate_game(n)}
'''
        client,main=self.open(text)
        errors=client.diagnostic(main.as_uri(),contains='ambient context')
        self.assertEqual(len(errors),1,errors)
        error=errors[0]
        self.assertEqual(error['range']['start'],self.position(text,'reset_shuffled(&deck)',0))
        self.assertEqual(error['range']['end'],self.position(text,'reset_shuffled(&deck)',len('reset_shuffled(&deck)')))
        self.assertIn("Pure function 'simulate_game'",error['message'])
        self.assertNotIn('Unsupported pure value type',json.dumps(errors))
        notes=error['relatedInformation']
        self.assertTrue(any(note['location']['uri']==main.as_uri() and
                            note['location']['range']['start']==self.position(text,'rand.shuffle(deck[:])',0)
                            for note in notes),error)
        self.assertTrue(any("fn 'simulate_game' requires" in note['message'] for note in notes),error)
        fixed=text.replace('simulate_game::fn','simulate_game::proc')
        client.notifications.clear()
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':2},'contentChanges':[{'text':fixed}]})
        client.diagnostic(main.as_uri(),empty=True)
        # A deterministic reset is also valid through the fn path.
        deterministic=text.replace('rand.shuffle(deck[:])','')
        client.notifications.clear()
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':3},'contentChanges':[{'text':deterministic}]})
        client.diagnostic(main.as_uri(),empty=True)
        self.assertEqual(main.read_text(),text)

    def test_transitive_borrowed_write_context_and_root_fn_link(self):
        text='''// 😀
package main
leaf::proc(data:[]int){data[0]=9}
middle::proc(data:[]int){leaf(data)}
work::fn(input:[]int)->int{middle(input);return 0}
main::proc(){values:=[1,2];_=work(values[:])}
'''
        client,main=self.open(text)
        errors=client.diagnostic(main.as_uri(),contains='borrowed from an input')
        self.assertEqual(len(errors),1,errors)
        error=errors[0]
        self.assertEqual(error['range']['start'],self.position(text,'middle(input)',0))
        notes=error['relatedInformation']
        self.assertTrue(any(note['location']['range']['start']==self.position(text,'data[0]=9',0)
                            for note in notes if note['location']['uri']==main.as_uri()),error)
        self.assertTrue(any("fn 'work' requires" in note['message'] for note in notes),error)


if __name__=='__main__':unittest.main()
