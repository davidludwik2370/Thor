"""Plan small AST-based Thor rewrites; apply only against the same source bytes."""
import concurrent.futures,hashlib,json,re,subprocess
from pathlib import Path
from audit import CORPUS,ART,THOR,write_json

def plan(path):
 data=path.read_bytes();done=subprocess.run([str(THOR),'ast',str(path)],capture_output=True,timeout=30)
 if done.returncode:return {'file':str(path.relative_to(CORPUS/'idiomatic')),'error':done.stderr.decode(errors='replace')}
 packet=json.loads(done.stdout)
 source=next(s for s in packet['sources'] if Path(s['path']).resolve()==path.resolve())
 nodes=source['frontend']['nodes'];index={}
 for i,n in enumerate(nodes):index.setdefault((n['start'],n['end']),[]).append(i)
 def text(n):return data[n['start']:n['end']].decode()
 # Role spans can share parents or synthetic markers. Prefer direct children.
 ids={id(n):i for i,n in enumerate(nodes)}
 def role(n,key):
  span=n.get('roles',{}).get(key,[-1,-1]);candidates=index.get(tuple(span),[])
  return next((nodes[i] for i in candidates if nodes[i].get('parent')==ids[id(n)]),nodes[candidates[0]] if candidates else None)
 def group(n,key):
  return [next((nodes[i] for i in index.get(tuple(span),[]) if nodes[i].get('parent')==ids[id(n)]),nodes[index[tuple(span)][0]]) for span in n.get('lists',{}).get(key,[]) if tuple(span) in index]
 edits=[]
 def add(n,replacement,feature):edits.append({'start':n['start'],'end':n['end'],'text':replacement,'feature':feature})
 primitive=re.compile(r'''(?:[-+]?\d[\w.]*|\.[A-Za-z_]\w*|true|false|"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')\Z''')
 for n in nodes:
  kind=n['kind']
  if kind=='Literal' and n.get('op')=='string':
   raw=text(n)
   if raw.startswith('\"') and raw.endswith('\"') and '\\' not in raw and '\n' not in raw:
    value=raw[1:-1]
    if len(value)>=6 and len(set(value))==1 and ord(value[0])<128:
     add(n,'('+json.dumps(value[0])+' * '+str(len(value))+')','string repetition')
  elif kind=='Compound':
   typ=role(n,'type');elems=group(n,'elems')
   match=re.fullmatch(r'\[(\?|\d+)\]int',text(typ).strip()) if typ else None
   if match and elems and (match[1]=='?' or int(match[1])==len(elems)) and all(re.fullmatch(r'[-+]?\d[\d_]*',text(e).strip()) for e in elems):
    add(n,'['+', '.join(text(e) for e in elems)+']','inferred fixed array')
  elif kind=='Switch':
   case_nodes=[c for c in nodes if c.get('parent')==ids[id(n)] and c['kind']=='Case']
   assignments=[];lhs=None
   for case in case_nodes:
    body=group(case,'body')
    if len(body)!=1 or body[0]['kind']!='Assignment' or body[0]['op']!='=':break
    left=group(body[0],'lhs');right=group(body[0],'rhs')
    if not left or not right or not all(x['kind']=='Ident' for x in left):break
    current=', '.join(text(x) for x in left)
    if lhs is not None and current!=lhs:break
    lhs=current;assignments.append((body[0],right))
   if assignments and len(assignments)==len(case_nodes) and len(case_nodes)>=2:
    converted=data[n['start']:n['end']]
    for assignment,right in reversed(assignments):
     a,b=assignment['start']-n['start'],assignment['end']-n['start']
     converted=converted[:a]+data[right[0]['start']:right[-1]['end']]+converted[b:]
    add(n,lhs+' = '+converted.decode(),'value switch' if len(group(assignments[0][0],'lhs'))==1 else 'multiple-result value switch')
  elif kind=='Index':
   base=role(n,'expr');ix=role(n,'index')
   if base and ix and ix['kind']=='Binary' and ix['op']=='-':
    left,right=role(ix,'left'),role(ix,'right')
    if left and right and text(right).strip()=='1' and left['kind']=='Call':
     callee=role(left,'callee');args=group(left,'args')
     if callee and text(callee)=='len' and len(args)==1 and text(args[0])==text(base) and re.fullmatch(r'[\w.]+',text(base)):
      add(ix,'-1','negative index')
  elif kind=='Call':
   callee=role(n,'callee');args=group(n,'args')
   if callee and text(callee)=='intrinsics.concatenate' and len(args)>=2:
    add(n,'('+' ++ '.join(text(a) for a in args)+')','constant concatenation')
  elif kind=='Binary' and n['op'] in ['||','&&']:
   op=n['op'];terms=[]
   def flatten(node):
    if node['kind']=='Binary' and node['op']==op:
     a,b=role(node,'left'),role(node,'right')
     if a and b:flatten(a);flatten(b)
    else:terms.append(node)
   flatten(n)
   comparisons=[]
   for term in terms:
    if term['kind']!='Binary' or term['op']!=('==' if op=='||' else '!='):break
    left,right=role(term,'left'),role(term,'right')
    if not left or not right or left['kind']!='Ident' or not primitive.fullmatch(text(right).strip()):break
    comparisons.append((text(left),text(right)))
   if len(comparisons)==len(terms) and len(terms)>=2 and len({a for a,b in comparisons})==1:
    add(n,comparisons[0][0]+(' in [' if op=='||' else ' not_in [')+', '.join(b for a,b in comparisons)+']','membership')
 # Keep outer rewrites; never apply overlapping edits against old offsets.
 chosen=[]
 for e in sorted(edits,key=lambda e:(e['start'],-e['end'])):
  if not any(e['start']<x['end'] and x['start']<e['end'] for x in chosen):chosen.append(e)
 return {'file':str(path.relative_to(CORPUS/'idiomatic')),'sha256':hashlib.sha256(data).hexdigest(),'edits':chosen}

def main():
 import sys
 if '--apply' in sys.argv:
  rows=json.loads((ART/'planned-refactors.json').read_text());count=0
  for row in rows:
   if not row.get('edits'):continue
   p=CORPUS/'idiomatic'/row['file'];data=p.read_bytes()
   if hashlib.sha256(data).hexdigest()!=row['sha256']:raise RuntimeError('Concurrent source change: '+str(p))
   for e in sorted(row['edits'],key=lambda e:e['start'],reverse=True):data=data[:e['start']]+e['text'].encode()+data[e['end']:]
   p.write_bytes(data);count+=len(row['edits'])
  print('Applied AST rewrites:',count)
 else:
  paths=sorted((CORPUS/'idiomatic').rglob('*.thor'))
  with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:rows=list(pool.map(plan,paths))
  write_json(ART/'planned-refactors.json',rows)
  print('Planned',sum(len(r.get('edits',[])) for r in rows),'edits in',sum(bool(r.get('edits')) for r in rows),'files')
if __name__=='__main__':main()
