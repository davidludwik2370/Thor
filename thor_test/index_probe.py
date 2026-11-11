#!/usr/bin/env python3
"""Isolate signed-index normalization and helper inlining in a copied workload.

Variants deliberately alter generated code, never compiler behavior or projects.
They are diagnostics, not candidate language semantics.
"""
import json,re,statistics,subprocess
from audit import ART,CORPUS,ODIN,write_json
from runtime import run,normalize
case=next(r for r in json.loads((ART/'runtime-console-merged.json').read_text())if r['id']==23)
generated=CORPUS/case['thor']['source']/'generated';raw=(generated/'hilo.odin').read_text();manifest=json.loads((generated/'thor-manifest.json').read_text())
variants={'control':raw,'native_bounds':re.sub(r'\b\w+_sequence\.normalize\(len\([^)]*\),(\w+),\{[^}]*\}\)',r'\1',raw),'inline_helpers':re.sub(r'(\b__thor_\w+\s*::\s*)proc\b',r'\1#force_inline proc',raw)}
rows={}
for label,text in variants.items():
 source=ART/'index-probe'/label;source.mkdir(parents=True,exist_ok=True);(source/'hilo.odin').write_text(text)
 binary=source/'program';flags=[flag for flag in manifest['odin_flags']if not flag.startswith('-out:')]
 result=subprocess.run([str(ODIN),'build',str(source),'-out:'+str(binary),*flags],capture_output=True,text=True)
 (source/'build.log').write_text(result.stdout+result.stderr)
 if result.returncode:raise RuntimeError(label+':'+result.stderr)
 rows[label]={'samples':[],'changed':text!=raw}
for attempt in range(5):
 labels=list(variants);labels=labels[attempt%3:]+labels[:attempt%3]
 for label in labels:
  source=ART/'index-probe'/label;result=run(source/'program',source)
  if result['status']!=0 or normalize(result['stdout'],case)!=case['thor']['normalized_stdout']:raise RuntimeError(result)
  rows[label]['samples'].append(result['seconds'])
for label,row in rows.items():row['median_seconds']=statistics.median(row['samples']);print(label,row['median_seconds'])
write_json(ART/'index-probe.json',rows)
