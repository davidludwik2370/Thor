"""Bound copied workloads and update copied examples for the installed Odin API."""
import json,re
from pathlib import Path
from audit import CORPUS,ART,code_only,write_json,verify_originals
records=[]
def change(p,before,after,reason):
 if before==after:return
 p.write_text(after);records.append({'file':str(p.relative_to(CORPUS)),'reason':reason,'before':before,'after':after})
for mode in ['odin','thor','idiomatic']:
 root=CORPUS/mode
 for p in sorted(root.rglob('*')):
  if not p.is_file() or p.suffix not in {'.odin','.thor'}:continue
  source=p.read_text();updated=source;relative=str(p.relative_to(root))
  # Equal bounded workloads in all three trees; the originals remain untouched.
  if '/poker/' in '/'+relative:
   updated=re.sub(r'(TRIAL_COUNT\s*::\s*)[\d_]+',r'\g<1>10_000',updated)
  elif '/hilo/' in '/'+relative:
   updated=re.sub(r'(NUM_TRIALS\s*::\s*)[\d_]+',r'\g<1>1_000_000',updated)
  elif relative=='Odin/pi/pi'+p.suffix:
   updated=re.sub(r'(NUM_TESTS\s*::\s*)[\d_]+',r'\g<1>1_000_000',updated)
  elif relative=='Odin/quicksort/quicksort'+p.suffix:
   updated=re.sub(r'(num_tests\s*::\s*)[\d_]+',r'\g<1>20',updated)
  elif relative.startswith('Odin/primes/higher_primes/') and p.stem=='primes':
   updated=re.sub(r'(limit\s*::\s*)[\d_]+',r'\g<1>1_000_000',updated)
  elif relative.startswith('Odin/ascending_primes/'):
   updated=updated.replace('123_456_789','1_000_000')
  elif relative.startswith('Odin/factorial/'):
   updated=re.sub(r'(limit\s*(?::=|:>)\s*)100_000',r'\g<1>1_000',updated)
  change(p,source,updated,'Bound test work; same limits for Odin, renamed Thor and idiomatic Thor')
  source=updated
  if relative.startswith('Odin/factorial/'):
   updated=updated.replace('if !success','if success != nil')
  if relative.startswith('Odin/tyringout/'):
   updated=updated.replace('os.processor_core_count()', 'os.get_processor_core_count()')
  if relative.startswith('Odin_Projects/Othello_pvai/'):
   updated=re.sub(r'(\b\w+,\s*(\w+)\s*(?::=|:>)\s*os\.read_entire_file\([^\n]*\)\s*\n\s*if\s+)!\2\b',lambda m:m[1]+m[2]+' != nil',updated)
   updated=updated.replace('if os.write_entire_file(path, data) {','if os.write_entire_file(path, data) == nil {')
  if relative.endswith('poker/ref/poker_single_thread'+p.suffix) and 'print :: fmt.print' not in updated:
   updated=updated.replace('import "core:fmt"','import "core:fmt"\nprint :: fmt.print',1)
  if relative.endswith('Odin_Projects/tic-tac-toe_gui/tic-tac-toe_test'+p.suffix) and 'printBoard ::' not in updated:
   updated=updated.replace('import "core:testing"','import "core:testing"\nimport "core:fmt"',1)
   updated+='\n// Restore the archived test helper\'s missing board printer.\nprintBoard :: proc(g: Game) { fmt.println(g.board) }\n'
  change(p,source,updated,'Adapt copied legacy examples to the current Odin API / restore missing debug helper')
  # Odin requires a feature tag that Thor inserts itself. This affects no body.
  if mode=='odin' and re.search(r'\busing\b',code_only(updated)) and '#+feature using-stmt' not in updated:
   change(p,updated,'#+feature using-stmt\n'+updated,'Enable legacy using syntax in the Odin reference')
write_json(ART/'test-preparation.json',records)
verify_originals()
print('Recorded',len(records),'copy-only source preparations')
