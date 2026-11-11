#!/usr/bin/env python3
"""Runtime comparisons in throwaway copies: seeded console runs and rendered GUI traces."""
import argparse,concurrent.futures,hashlib,json,os,re,shutil,signal,subprocess,time,selectors
from pathlib import Path
from audit import CORPUS,ART,ODIN,THOR,COMPILER,code_only,write_json

GUI_FUNCTIONS=['InitWindow','SetTargetFPS','WindowShouldClose','EndDrawing','GetFrameTime','GetTime','GetFPS','DrawFPS','GetKeyPressed','GetCharPressed','IsKeyPressed','IsKeyReleased','IsKeyDown','GetMouseX','GetMouseY','GetMousePosition','GetMouseDelta','GetMouseWheelMove','IsMouseButtonPressed','IsMouseButtonReleased','IsMouseButtonDown']
GUI_HELPER=r'''
import audit_rl "vendor:raylib"
import audit_fmt "core:fmt"
import audit_rand "core:math/rand"
audit_frame: int
audit_key_taken: bool
audit_interactions :: AUDIT_INTERACTIONS

audit_init :: proc(){audit_rand.reset(12345);audit_rl.SetRandomSeed(12345)}
audit_InitWindow :: proc(w,h:i32,title:cstring){audit_rl.InitWindow(w,h,title);audit_rl.SetRandomSeed(12345);audit_rl.SetTraceLogLevel(.NONE)}
audit_SetTargetFPS :: proc(fps:i32){_=fps}
audit_WindowShouldClose :: proc()->bool{return audit_frame>=64}
audit_GetFrameTime :: proc()->f32{return 1.0/60.0}
audit_GetTime :: proc()->f64{return f64(audit_frame)/60.0}
audit_GetFPS :: proc()->i32{return 60}
audit_DrawFPS :: proc(x,y:i32){audit_rl.DrawText("60 FPS",x,y,20,audit_rl.LIME)}
audit_GetKeyPressed :: proc()->audit_rl.KeyboardKey{
 if !audit_interactions||audit_key_taken{return audit_rl.KeyboardKey(0)}
 audit_key_taken=true
 switch audit_frame{case 5:return .RIGHT;case 12:return .DOWN;case 20:return .LEFT;case 28:return .UP;case 40:return .R;case:return audit_rl.KeyboardKey(0)}
}
audit_GetCharPressed :: proc()->i32{return 0}
audit_IsKeyPressed :: proc(key:audit_rl.KeyboardKey)->bool{
 if !audit_interactions{return false}
 switch audit_frame{case 5:return key==.RIGHT;case 12:return key==.DOWN;case 20:return key==.LEFT;case 28:return key==.UP;case 40:return key==.R;case:return false}
}
audit_IsKeyReleased :: proc(key:audit_rl.KeyboardKey)->bool{_=key;return false}
audit_IsKeyDown :: proc(key:audit_rl.KeyboardKey)->bool{return audit_IsKeyPressed(key)}
audit_GetMouseX :: proc()->i32{return (audit_rl.GetScreenWidth()/3)+(i32(audit_frame/8)%3)*20}
audit_GetMouseY :: proc()->i32{return (audit_rl.GetScreenHeight()/3)+(i32(audit_frame/8)%3)*20}
audit_GetMousePosition :: proc()->audit_rl.Vector2{return {f32(audit_GetMouseX()),f32(audit_GetMouseY())}}
audit_GetMouseDelta :: proc()->audit_rl.Vector2{return {0,0}}
audit_GetMouseWheelMove :: proc()->f32{return 0}
audit_IsMouseButtonPressed :: proc(button:audit_rl.MouseButton)->bool{return audit_interactions&&button==.LEFT&&(audit_frame==8||audit_frame==24||audit_frame==48)}
audit_IsMouseButtonReleased :: proc(button:audit_rl.MouseButton)->bool{_=button;return false}
audit_IsMouseButtonDown :: proc(button:audit_rl.MouseButton)->bool{return audit_interactions&&button==.LEFT&&((audit_frame>=8&&audit_frame<11)||(audit_frame>=24&&audit_frame<27))}
audit_EndDrawing :: proc(){
 audit_rl.EndDrawing()
 if audit_frame==0||audit_frame==3||audit_frame==8||audit_frame==15||audit_frame==31||audit_frame==63{
  image:=audit_rl.LoadImageFromScreen()
  colors:=audit_rl.LoadImageColors(image)
  bytes:=(cast([^]u8)colors)[:int(image.width)*int(image.height)*size_of(audit_rl.Color)]
  hash:u64=14695981039346656037
  for value in bytes{hash=(hash~u64(value))*1099511628211}
  audit_fmt.printfln("AUDIT_FRAME %v %v %v %016x",audit_frame,image.width,image.height,hash)
  name_buffer:[128]byte
  _=audit_fmt.bprintf(name_buffer[:],"audit-frame-%v.png",audit_frame)
  saved:=audit_rl.ExportImage(image,cstring(&name_buffer[0]))
  audit_rl.UnloadImageColors(colors)
  audit_rl.UnloadImage(image)
  if !saved{panic("Cannot save audit frame")}
 }
 audit_frame+=1;audit_key_taken=false
}
'''

def replace_reset_arguments(text,aliases):
 clean=code_only(text);edits=[]
 for alias in aliases:
  for match in re.finditer(r'\b'+re.escape(alias)+r'\.reset\s*\(',clean):
   start=match.end();end=start;depth=1
   while end<len(clean) and depth:
    depth+= (clean[end]=='(')-(clean[end]==')');end+=1
   if depth==0:edits.append((start,end-1,'12345'))
 for a,b,value in reversed(edits):text=text[:a]+value+text[b:]
 return text

def instrument(source,gui,interactions):
 files=sorted(source.glob('*.odin'))+sorted(source.glob('*.thor'));main_file=None
 changes=[]
 for p in files:
  raw=p.read_text();text=raw;clean=code_only(text)
  if re.search(r'^\s*#\+build\s+ignore\b',text,re.M):continue
  if re.search(r'\bmain\s*::\s*(?:proc|fn)\s*\(\s*\)',clean):main_file=p
  rand_aliases=[m[1] or 'rand' for m in re.finditer(r'\bimport\s+(?:(\w+)\s+)?"core:math/rand"',text)]
  text=replace_reset_arguments(text,rand_aliases)
  if any(name in text for name in ['run_trials :: proc','run_trials::proc']):
   text=re.sub(r'(\brun_trials\s*::\s*proc\([^)]*\)\s*\{)',r'\1\n audit_seed_worker()\n',text)
  # Seed task callbacks once, instead of resetting state in inner helpers.
  pattern=r'(\b\w+\s*::\s*proc\([^)]*\bthread\.Task\b[^)]*\)\s*\{)'
  text=re.sub(pattern,r'\1\n audit_seed_worker()\n',text)
  text=text.replace('os.get_processor_core_count()','1')
  text=re.sub(r'_,\s*core_count\s*(?::=|:>)\s*info\.cpu_core_count\(\)\s*or_else\s*panic\([^\n]*\)', 'core_count :: 1',text)
  if gui:
   aliases=[m[1] or 'raylib' for m in re.finditer(r'\bimport\s+(?:(\w+)\s+)?"vendor:raylib"',text)]
   # Qualifying calls and calls exposed by native using/aliases are equivalent.
   names='|'.join(GUI_FUNCTIONS)
   clean=code_only(text);edits=[]
   for m in re.finditer(r'(?<![\w.])(?:(\w+)\.)?('+names+r')\s*(?=\()',clean):
    if m[1] is None or m[1] in aliases:edits.append((m.start(),m.end(),'audit_'+m[2]))
   for a,b,value in reversed(edits):text=text[:a]+value+text[b:]
   text=text.replace('"/Users/davidludwik/Desktop/"','"./"')
  if text!=raw:changes.append(str(p.name));p.write_text(text)
 if main_file is None:raise RuntimeError('No selected main procedure')
 text=main_file.read_text()
 matches=list(re.finditer(r'\bmain\s*::\s*(?:proc|fn)\s*\(\s*\)\s*\{',code_only(text)))
 for match in reversed(matches):text=text[:match.end()]+'\n audit_init()\n'+text[match.end():]
 if gui:text+='\n'+GUI_HELPER.replace('AUDIT_INTERACTIONS','true' if interactions else 'false')
 else:text+='\nimport audit_rand "core:math/rand"\naudit_init::proc(){audit_rand.reset(12345)}\n'
 text+='\naudit_seed_worker::proc(){audit_rand.reset(12345)}\n'
 main_file.write_text(text)
 return {'main':main_file.name,'changed_files':changes,'rng_seed':12345,'thread_count':1,'gui_frames':64 if gui else None,'interactions':interactions if gui else None}

SDL_HELPER=r'''
audit_frame:int
audit_sdl_events::proc(ctx:^ui.Context){
 if audit_frame>=8{running=false;return}
 AUDIT_SDL_INPUT
}
audit_sdl_present::proc(renderer:^SDL.Renderer)->bool{
 if audit_frame==0||audit_frame==1||audit_frame==2||audit_frame==3||audit_frame==5||audit_frame==7{
  image:=SDL.RenderReadPixels(renderer,nil)
  if image==nil{panic("Cannot read SDL renderer")}
  bytes:[]u8=(cast([^]u8)image.pixels)[:int(image.pitch)*int(image.h)]
  hash:u64=14695981039346656037
  for value in bytes{hash=(hash~u64(value))*1099511628211}
  fmt.printfln("AUDIT_FRAME %v %v %v %016x",audit_frame,image.w,image.h,hash)
  name_buffer:[128]byte
  _=fmt.bprintf(name_buffer[:],"audit-frame-%v.bmp",audit_frame)
  saved:=SDL.SaveBMP(image,cstring(&name_buffer[0]))
  SDL.DestroySurface(image)
  if !saved{panic("Cannot save SDL frame")}
 }
 audit_frame+=1
 return SDL.RenderPresent(renderer)
}
'''
def instrument_sdl(source,interactions):
 details=instrument(source,False,False);p=source/details['main'];s=p.read_text()
 s=s.replace('handle_events(ctx)\n\t\tupdate_scale','audit_sdl_events(ctx)\n\t\tif !running{break}\n\t\tupdate_scale')
 s=s.replace('SDL.RenderPresent(sdl_renderer)','audit_sdl_present(sdl_renderer)').replace('SDL.Delay(8)','SDL.Delay(0)').replace('SDL.SetRenderVSync(sdl_renderer, 1)','SDL.SetRenderVSync(sdl_renderer, 0)')
 actions='''ui.input_mouse_move(ctx,150,225)
 if audit_frame==2{ui.input_mouse_down(ctx,150,225,.LEFT)}
 if audit_frame==3{ui.input_mouse_up(ctx,150,225,.LEFT)}''' if interactions else ''
 s+='\n'+SDL_HELPER.replace('AUDIT_SDL_INPUT',actions);p.write_text(s)
 details.update(gui_frames=8,interactions=interactions,renderer='SDL3/TTF')
 return details

def prepare(case,mode,gui,interactions):
 original=CORPUS/mode/case['relative'];directory=original.parent if case['file'] else original
 destination=ART/'runtime-source'/('input' if interactions else 'startup')/f"{case['id']:03}"/mode
 if destination.exists():shutil.rmtree(destination)
 shutil.copytree(directory,destination,ignore=shutil.ignore_patterns('.thor-*','.vscode'))
 # A standalone version must not discover neighboring main files.
 if case['file']:
  selected=Path(case['relative']).name
  if mode!='odin':selected=Path(selected).with_suffix('.thor').name
  for p in list(destination.glob('*.odin'))+list(destination.glob('*.thor')):
   if p.name!=selected:p.unlink()
 details=instrument_sdl(destination,interactions) if case['id']==68 and gui else instrument(destination,gui,interactions)
 write_json(destination/'audit-instrumentation.json',details)
 return destination

def compile_case(case,mode,source,unchecked=False):
 binary=source/'audit-program'
 flags=['-o:speed']+(['-disable-assert','-no-bounds-check','-no-type-assert'] if unchecked else [])
 if mode=='odin':args=[str(ODIN),'build',str(source),'-out:'+str(binary),*flags]
 else:args=[str(THOR),'build',str(source),'--output-dir',str(source/'generated'),'--cache-dir',str(ART/'runtime-cache'/mode/str(case['id'])),'-out:'+str(binary),*flags]
 start=time.monotonic();result=subprocess.run(args,cwd=source,capture_output=True,text=True,timeout=90)
 (source/'build.log').write_text(result.stdout+result.stderr)
 return binary,{'status':result.returncode,'seconds':time.monotonic()-start,'log':str((source/'build.log').relative_to(CORPUS))}

def run(binary,cwd,arguments=(),input_data=b'',timeout=12):
 start=time.monotonic();p=subprocess.Popen([str(binary),*arguments],cwd=cwd,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True)
 timed_out=False
 try:out,err=p.communicate(input_data,timeout=timeout)
 except subprocess.TimeoutExpired:
  timed_out=True;os.killpg(p.pid,signal.SIGTERM)
  try:out,err=p.communicate(timeout=3)
  except subprocess.TimeoutExpired:os.killpg(p.pid,signal.SIGKILL);out,err=p.communicate(timeout=3)
 return {'status':p.returncode,'timeout':timed_out,'seconds':time.monotonic()-start,'stdout':out.decode(errors='replace'),'stderr':err.decode(errors='replace')}

def normalize(text,case):
 text=re.sub(r'0x[0-9a-fA-F]+','<address>',text)
 text=re.sub(r'(duration: )[^\n]*( ms)',r'\1<elapsed>\2',text,flags=re.IGNORECASE)
 text=re.sub(r'(Time taken:|Time:|Elapsed:)[^\n]*',r'\1 <elapsed>',text,flags=re.IGNORECASE)
 text=re.sub(r'/Users/[^\s:()]+\.(odin|thor)','<source>',text)
 if case['id']==67:
  text=re.sub(r'Thread \d+ processing', 'Thread <id> processing',text)
  text='\n'.join(sorted(text.splitlines()))+'\n'
 if case['id']==12:
  text=re.sub(r'/Users/[^\s]+/audit-program','<executable>',text)
  lines=text.splitlines()
  for pattern in [r'[ABC]',r'[ABC] [149]']:
   indices=[i for i,line in enumerate(lines)if re.fullmatch(pattern,line)]
   values=sorted(lines[i]for i in indices)
   for i,value in zip(indices,values):lines[i]=value
  text='\n'.join(lines)+'\n'
  text=re.sub(r'(?ms)^# threading_example\n.*?(?=^# |\Z)','# threading_example\n<concurrent scheduling trace>\n',text)
 if '/thread_test' in case['label']:text='\n'.join(sorted(text.splitlines()))+'\n'
 return text

def huffman_result(source,result):
 # Equal-frequency symbols can receive different prefix codes on each native
 # run. Verify the actual saved bitstream decodes with that run's dictionary.
 pairs=re.findall(r"^'(.*?)': ([01]+)$",result['stdout'],re.M|re.S)
 codes={bits:char for char,bits in pairs if len(char)==1}
 data=(source/'encoded.huff').read_bytes();length=int.from_bytes(data[:8],'little')
 bits=''.join(str((int.from_bytes(data[i:i+8],'little')>>shift)&1)for i in range(8,len(data),8)for shift in range(64))[:length]
 decoded=[];pending=''
 for bit in bits:
  pending+=bit
  if pending in codes:decoded.append(codes[pending]);pending=''
 text=''.join(decoded);expected=(source/'dune.txt').read_text()
 if pending or text!=expected:raise RuntimeError('Saved Huffman bitstream does not round-trip')
 return {'decoded_sha256':hashlib.sha256(text.encode()).hexdigest(),'encoded_bit_count':length,'symbol_count':len(codes)}

def scenario(case,mode,gui,interactions=False):
 source=prepare(case,mode,gui,interactions);binary,build=compile_case(case,mode,source)
 row={'build':build,'source':str(source.relative_to(CORPUS))}
 if build['status']!=0:return row
 arguments=[];input_data=b''
 label=case['label']
 if label=='Odin/collatz':arguments=['10000']
 elif label=='Odin/md5':input_data=b'Thor/Odin compatibility\x00\n'
 elif 'prime_factors_cli' in label:input_data=b'q\n'
 elif label=='Odin/eq_parser':input_data=b'q\n'
 elif label=='Odin/script_pg':input_data=b'12\n' # A separate dialogue test covers multiple prompts.
 elif label=='Odin/kv':arguments=['get','audit_missing']
 elif label=='Odin/guess':input_data=b'50\n'
 elif label=='Odin/brc':
  (source/'test_weather_stations.csv').write_text('A;1.0\nB;2.0\nA;-3.0\n')
 elif label=='Odin/huffman':(source/'dune.txt').write_text('the spice must flow\n'*40)
 first=dialogue(binary,source,label) if label in DIALOGUES else run(binary,source,arguments,input_data)
 row['run']=first
 if label=='Odin/huffman' and first['status']==0:row['semantic_result']=huffman_result(source,first)
 if gui:
  row['frames']=re.findall(r'^AUDIT_FRAME .*$',first['stdout'],re.MULTILINE)
  second=run(binary,source,arguments,input_data)
  row['reference_repeat_stable']=row['frames']==re.findall(r'^AUDIT_FRAME .*$',second['stdout'],re.MULTILINE) and first['status']==second['status']
 elif True:
  second=dialogue(binary,source,label) if label in DIALOGUES else run(binary,source,arguments,input_data)
  row['reference_repeat_stable']=normalize(first['stdout'],case)==normalize(second['stdout'],case) and first['status']==second['status']
 row['normalized_stdout']=normalize(first['stdout'],case)
 row['output_files']={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in source.iterdir() if p.is_file() and p.name in ['encoded.huff','factorial.txt']}
 (source/'run.stdout').write_text(first['stdout']);(source/'run.stderr').write_text(first['stderr'])
 return row

DIALOGUES={'Odin/guess','Odin/script_pg','Odin/eq_parser','Odin/prime_factors_cli','Odin/prime_factors_cli_refactor','Odin/tic-tac-toe'}
def dialogue(binary,cwd,label):
 start=time.monotonic();p=subprocess.Popen([str(binary)],cwd=cwd,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True)
 selector=selectors.DefaultSelector();selector.register(p.stdout,selectors.EVENT_READ,'stdout');selector.register(p.stderr,selectors.EVENT_READ,'stderr')
 outputs={'stdout':bytearray(),'stderr':bytearray()};cursor=0;step=0;actions=[];timed_out=False
 if label=='Odin/script_pg':steps=[(b'a: ',b'12\n'),(b'b: ',b'34\n')]
 elif 'prime_factors_cli' in label:steps=[(b'Factor: ',v)for v in [b'360\n',b'not_an_integer\n',b'1\n',b'q\n']]
 elif label=='Odin/eq_parser':steps=[(b'> ',v)for v in [b'2+3*4\n',b'(2+3)*4\n',b'f\n',b'10000+1\n',b'q\n']]
 else:steps=[]
 moves=iter([f'{x}{y}\n'.encode() for y in range(1,4) for x in 'abc'])
 while selector.get_map() and time.monotonic()-start<12:
  for key,_ in selector.select(.05):
   chunk=os.read(key.fileobj.fileno(),65536)
   if not chunk:selector.unregister(key.fileobj);continue
   outputs[key.data].extend(chunk)
  out=bytes(outputs['stdout']);tail=out[cursor:];message=None
  if step<len(steps) and steps[step][0] in tail:
   message=steps[step][1];cursor=len(out);step+=1
  elif label=='Odin/guess' and (step==0 and b'Enter a number' in tail or step>0 and (b'Too low' in tail or b'Too high' in tail)):
   step+=1;message=f'{step}\n'.encode();cursor=len(out)
  elif label=='Odin/tic-tac-toe':
   if b'User piece (x or o): ' in tail:message=b'x\n';cursor=len(out)
   elif b'First move (x or o): ' in tail:message=b'o\n';cursor=len(out)
   elif b'Play again? (y/n): ' in tail:message=b'n\n';cursor=len(out)
   elif re.search(rb'Move \d+ - Player \(X\): ',tail):message=next(moves,b'a1\n');cursor=len(out)
  if message is not None:
   try:p.stdin.write(message);p.stdin.flush();actions.append(message.decode().strip())
   except BrokenPipeError:pass
  if sum(map(len,outputs.values()))>8*1024*1024:break
 if p.poll() is None:
  timed_out=True;os.killpg(p.pid,signal.SIGTERM)
  try:p.wait(timeout=2)
  except subprocess.TimeoutExpired:os.killpg(p.pid,signal.SIGKILL);p.wait()
 selector.close()
 return {'status':p.wait(),'timeout':timed_out,'seconds':time.monotonic()-start,**{key:value.decode(errors='replace')for key,value in outputs.items()},'inputs':actions}

def main():
 p=argparse.ArgumentParser();p.add_argument('--ids');p.add_argument('--modes',default='odin,thor,idiomatic');p.add_argument('--report',default=None);p.add_argument('--gui',action='store_true');p.add_argument('--interactions',action='store_true');args=p.parse_args()
 cases=json.loads((ART/'inventory.json').read_text())['cases']
 ids={int(x) for x in args.ids.split(',')} if args.ids else set()
 output=ART/(args.report or ('runtime-gui-input.json' if args.gui and args.interactions else 'runtime-gui.json' if args.gui else 'runtime-console.json'))
 rows=json.loads(output.read_text()) if output.exists() else []
 previous={r['id']:r for r in rows}
 rows=[r for r in rows if r['id'] not in ids]
 for case in cases:
  if case['id'] not in ids:continue
  row={**previous.get(case['id'],{}),'id':case['id'],'label':case['label'],'gui':args.gui,'interactions':args.interactions}
  for mode in args.modes.split(','):
   row[mode]=scenario(case,mode,args.gui,args.interactions)
   print(case['label'],mode,'build',row[mode]['build']['status'],'run',row[mode].get('run',{}).get('status'),flush=True)
  if all(r.get('run',{}).get('status')==0 for r in [row[m] for m in ['odin','thor','idiomatic']]):
   key='frames' if args.gui else 'normalized_stdout'
   row['match']=row['odin'].get(key)==row['thor'].get(key)==row['idiomatic'].get(key)
   if args.gui:row['match']=row['match'] and len(row['odin']['frames'])==6
   if case['id']==25:row['match']=row['odin'].get('semantic_result')==row['thor'].get('semantic_result')==row['idiomatic'].get('semantic_result')
   else:row['match']=row['match'] and row['odin']['output_files']==row['thor']['output_files']==row['idiomatic']['output_files']
  else:row['match']=False
  rows.append(row);write_json(output,sorted(rows,key=lambda r:r['id']))
if __name__=='__main__':main()
