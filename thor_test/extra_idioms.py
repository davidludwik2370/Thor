#!/usr/bin/env python3
"""Focused comprehensions, selected imports, and static-data examples."""
import json,re
from pathlib import Path
from audit import CORPUS,ART,write_json
changes=[]
def edit(relative,old,new,reason):
 p=CORPUS/'idiomatic'/relative;s=p.read_text()
 if old not in s:raise RuntimeError(f'Missing expected source: {p}: {old[:70]}')
 s=s.replace(old,new,1);p.write_text(s);changes.append({'file':relative,'reason':reason,'before':old,'after':new})
edit('Odin/filter/filter.thor','evens := filter(nums, is_even)','evens := [n for n in nums if is_even(n)]\n    defer delete(evens)','Filtered slice comprehension with explicit ownership')
edit('Odin/script_pg/script_pg.thor','cube_nums :> fmap(nums, cube)','cube_nums :> [cube(n) for n in nums]\n    defer delete(cube_nums)','Mapping comprehension with explicit ownership')
for relative,names,collections in [('Odin_Projects/Cricles_on_dots/cricles_on_dots.thor',['loadCircles','loadPoints'],['startingCircles','startingPoints']),('Odin_Projects/zzz_refactors/Cricles_on_dots_refactor/cricles_on_dots.thor',['load_circles','load_points'],['starting_circles','starting_points'])]:
 p=CORPUS/'idiomatic'/relative
 for name,collection in zip(names,collections):
  s=p.read_text();pattern=rf'{name} :: proc\(\) -> \[dynamic\]Pos \{{.*?\n\}}';m=re.search(pattern,s,re.S)
  if m is None:raise RuntimeError(name)
  item='circle' if 'ircle' in name else 'point'
  edit(relative,m[0],f'{name} :: proc() -> [dynamic]Pos {{\n\treturn [dynamic]Pos{{{item} for {item} in {collection}}}\n}}','Dynamic-array comprehension preserves independent mutable result')
# Keep per-file imports per-file: package-wide convenience aliases are left intact.
for relative in ['Odin_Projects/Cricles_on_dots/cricles_on_dots.thor']:
 p=CORPUS/'idiomatic'/relative;s=p.read_text()
 for collection in ['startingCircles','startingPoints']:
  edit(relative,'@rodata\n'+collection+' :=',collection+' :>','Immutable fixed array replaces read-only global annotation')
# Comprehension keeps the same views and capacity requirements as the original loop.
p=CORPUS/'idiomatic/Odin/brc/brc.thor';s=p.read_text();m=re.search(r'    str_chunks := make\(\[\]string, cores\).*?\n    \}',s,re.S)
if m:
 edit('Odin/brc/brc.thor',m[0],'    str_chunks :> [text[start:((i == len(start_positions)-1) ? len(text) : start_positions[i+1])] for start, i in start_positions]\n    defer delete(str_chunks)','Indexed comprehension of borrowed string chunks')
write_json(ART/'extra-idioms.json',changes)
print('Applied',len(changes),'focused idioms')
