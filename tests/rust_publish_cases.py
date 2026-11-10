"""Portable publishing cases shared with the Rust protocol comparison suite.

The reference executes only in this test harness; the native publisher is standalone.
"""

def verify_icons(BINARY):
    import os,subprocess,tempfile
    from pathlib import Path
    from PIL import Image
    from thorlang.publishing import icon_image,write_icon
    checks=0
    with tempfile.TemporaryDirectory(prefix='thor-publish-icon-parity-') as tmp:
     root=Path(tmp)
     cases=[]
     for name,size,background,box in [
      ('square',(8,8),(20,30,40,255),None),
      ('visible_crop',(12,8),(20,30,40,255),None),
      ('transparent_shift',(12,8),(50,60,70,0),(7,2,11,6)),
      ('transparent_rgb',(12,8),(50,60,70,0),None),
      ('portrait_shift',(8,12),(50,60,70,0),(2,7,6,11)),
      ('oversized',(1100,1100),(20,30,40,255),None),
     ]:
      image=Image.new('RGBA',size,background)
      if box:image.paste((80,90,100,255),box)
      path=root/(name+'.png');image.save(path);cases.append(path)
     image=Image.new('RGBA',(12,8),(0,0,0,0));image.putpixel((0,0),(100,100,100,1));image.putpixel((11,7),(100,100,100,1))
     path=root/'nearly_transparent.png';image.save(path);cases.append(path)
     ico=root/'multi.ico';Image.new('RGBA',(256,256),(100,110,120,255)).save(ico,sizes=[(n,n) for n in [16,24,32,48,64,128,256]]);cases.append(ico)
     for path in cases:
      expected_warnings=[];expected=icon_image(path,warn=expected_warnings.append)
      output=root/(path.stem+'-normalized.png')
      result=subprocess.run([str(BINARY),str(path),str(output),'linux'],capture_output=True,text=True)
      if result.returncode:raise RuntimeError(result.stderr)
      header,*warnings=result.stdout.splitlines()
      actual=Image.open(output).convert('RGBA')
      if actual.size!=expected.size or warnings!=expected_warnings:raise RuntimeError((path,actual.size,expected.size,warnings,expected_warnings))
      if path.stem!='oversized' and actual.tobytes()!=expected.tobytes():raise RuntimeError(('pixels',path))
      checks+=1
     for system,suffix in [('darwin','.icns'),('windows','.ico')]:
      source=cases[2];output=root/('platform'+suffix)
      result=subprocess.run([str(BINARY),str(source),str(output),system],capture_output=True,text=True)
      if result.returncode:raise RuntimeError(result.stderr)
      with Image.open(output) as opened:
       if system=='darwin':
        if opened.format!='ICNS' or opened.size!=(1024,1024):raise RuntimeError((opened.format,opened.size))
        opened.load()
       else:
        if opened.ico.sizes()!={(n,n) for n in [16,24,32,48,64,128,256]}:raise RuntimeError(opened.ico.sizes())
      checks+=1
     copied=root/'preserved.ico'
     result=subprocess.run([str(BINARY),str(ico),str(copied),'windows'],capture_output=True,text=True)
     if result.returncode or ico.read_bytes()!=copied.read_bytes():raise RuntimeError(result.stderr)
     checks+=1
     bad=root/'disguised.png';Image.new('RGB',(8,8)).save(bad,format='JPEG')
     result=subprocess.run([str(BINARY),str(bad),str(root/'bad.png'),'linux'],capture_output=True,text=True)
     if result.returncode!=1 or 'expected PNG image data' not in result.stderr:raise RuntimeError(result.stderr)
     checks+=1
     print(checks,'icon parity/container checks passed')
    return checks

def verify_policy(BINARY):
    import contextlib,json,subprocess,tempfile
    from pathlib import Path
    from unittest.mock import patch
    from thorlang.publishing import Publisher
    from tests.test_publish import options
    checks=0
    with tempfile.TemporaryDirectory(prefix='thor-publisher-policy-parity-') as temporary:
     root=Path(temporary).resolve();project=root/'my project';project.mkdir();(project/'main.thor').write_text('package main\nmain::proc(){}\n')
     output=root/'output';output.mkdir();scratch=root/'scratch';scratch.mkdir()
     (project/'assets').mkdir();(project/'assets/data.txt').write_text('data');(project/'extra.txt').write_text('extra')
     def compare(system='darwin',flags=(),settings=None,config=None,native=False,fail=False,entry=None):
      nonlocal checks
      settings=settings or {};path=project/'thor.toml'
      if config is None:path.unlink(missing_ok=True)
      else:path.write_text(config)
      with contextlib.chdir(output),patch('thorlang.publishing.host_target',return_value=(system,'arm64')):
       try:
        p=Publisher.prepare(entry or project,list(flags),options(**settings))
        args=p.native_flags(list(flags),scratch) if native else None
        expected={key:getattr(p,key) for key in ['root','system','arch','name','executable_name','identifier','version','app_type','output','icon','libraries','warnings','linked_file']}
        expected.update(image=list(p.image.size) if p.image else None,resources=sorted(p.as_posix() for p in p.resources),directories=sorted(p.as_posix() for p in p.directories),native_flags=args)
        expected=json.loads(json.dumps(expected,default=str))
       except Exception as error:expected={'error':str(error)}
      request=root/'request.json';request.write_text(json.dumps(dict(entry=str(entry or project),flags=list(flags),system=system,arch='arm64',options={key:str(value) if isinstance(value,Path) else value for key,value in settings.items()},native=native,scratch=str(scratch))))
      result=subprocess.run([str(BINARY),'publisher',str(request)],cwd=output,capture_output=True,text=True)
      if result.returncode:raise RuntimeError(result.stderr)
      packet=json.loads(result.stdout)
      if 'error' in expected:
       if not fail or 'error' not in packet:raise RuntimeError((expected,packet))
      elif packet.get('ok')!=expected:raise RuntimeError((expected,packet))
      elif fail:raise RuntimeError('expected failure')
      checks+=1
     for system in ['darwin','windows','linux']:
      compare(system)
      compare(system,flags=['-out:'+str(root/'chosen'/'my_binary')])
      compare(system,settings={'app_name':'My App','app_id':'com.example.demo','app_version':'12.3','app_type':'console','app_resource':['extra.txt']},native=True)
      for flag in [[],['-o:none'],['-o:size']]:compare(system,flags=flag,native=True)
     compare(config='[publish]\nname="Configured"\nid="org.example.config"\nversion="2.4"\ntype="console"\nresources=["extra.txt"]\n',settings={'app_name':'CLI Name'},native=True)
     compare(settings={'app_version':'١.٢'})
     compare(entry=project/'main.thor')
     for config in ['publish=12','[publish]\nextra=true','[publish]\nname=3','[publish]\nresources="assets"','[publish]\nlibraries=[""]']:
      compare(config=config,fail=True)
     for settings in [{'app_name':'CON'},{'app_name':'bad/name'},{'app_version':'65536'},{'app_version':'1.2.3.4'},{'app_id':'plain'},{'app_type':'bad'},{'app_resource':['../escape']}]:
      compare(settings=settings,fail=True)
     compare(flags=['-target:linux_arm64'],fail=True)
     compare(flags=['-build-mode:obj'],fail=True)
     compare('windows',flags=['-resource:foo'],fail=True)
    print(checks,'publisher preparation/native-flag comparisons passed')
    return checks

def verify_dependencies(BINARY):
    import json,os,shutil,struct,subprocess,sys,tempfile
    from pathlib import Path
    from unittest.mock import patch
    from tests.test_publish import pe_file
    from thorlang.publish_deps import pe_imports,windows_dependencies,linux_dependencies
    checks=0

    def invoke(mode,value,env=None):
     result=subprocess.run([str(BINARY),mode,str(value)],capture_output=True,text=True,env=env)
     if result.returncode:raise RuntimeError(result.stderr)
     return json.loads(result.stdout)

    with tempfile.TemporaryDirectory(prefix='thor-publish-dependency-parity-') as tmp:
     root=Path(tmp).resolve();project=root/'project';project.mkdir()
     source=pe_file(project/'app.exe',['KERNEL32.dll','Foo.DLL'],['plugin.dll'])
     files=[source]
     x86=project/'x86.exe';data=bytearray(source.read_bytes());optional=152;directories=data[optional+112:optional+240]
     struct.pack_into('<H',data,optional,0x10b);struct.pack_into('<I',data,optional+28,0x400000);struct.pack_into('<I',data,optional+92,16)
     data[optional+96:optional+224]=directories
     # Delay-import VA mode.
     struct.pack_into('<II',data,0x400,0,0x401600);x86.write_bytes(data);files.append(x86)
     for file in files:
      expected=pe_imports(file);actual=invoke('pe',file)
      if actual.get('ok')!=expected:raise RuntimeError((file,expected,actual))
      checks+=1
     for length in [0,1,2,63,64,127,128,151,153,255,391,400,800]:
      bad=project/'truncated.exe';bad.write_bytes(source.read_bytes()[:length])
      try:expected=pe_imports(bad)
      except Exception:
       actual=invoke('pe',bad)
       if 'error' not in actual or 'Cannot inspect Windows executable' not in actual['error']:raise RuntimeError(actual)
      else:
       actual=invoke('pe',bad)
       if actual.get('ok')!=expected:raise RuntimeError((length,expected,actual))
      checks+=1
     foo=pe_file(project/'Foo.DLL',['bar.dll']);bar=pe_file(project/'bar.dll');plugin=pe_file(project/'plugin.dll')
     for declared in [[],[plugin]]:
      outputs=[]
      for engine in ['python','rust']:
       dest=root/(engine+str(checks));dest.mkdir();binary=dest/'app.exe';shutil.copy2(source,binary)
       if engine=='python':actual={'ok':windows_dependencies(source,binary,dest,project,declared)}
       else:
        request=root/'request.json';request.write_text(json.dumps(dict(system='windows',executable=str(source),binary=str(binary),destination=str(dest),project=str(project),declared=list(map(str,declared)),hints=[])))
        actual=invoke('bundle',request)
       outputs.append(actual)
       for file in [foo,bar,plugin]:
        if (dest/file.name).read_bytes()!=file.read_bytes():raise RuntimeError('library copy mismatch')
      if outputs[0]!=outputs[1]:raise RuntimeError(outputs)
      checks+=1
     tools=root/'tools';tools.mkdir();log=root/'tools.log';settings=root/'tools.json'
     shim='#!'+sys.executable+'\nimport json,os,sys\nfrom pathlib import Path\n'+f'config=json.loads(Path({str(settings)!r}).read_text())\n'+f'with open({str(log)!r},"a") as f:f.write(json.dumps([Path(sys.argv[0]).name,*sys.argv[1:]])+"\\n")\n'+\
      'name=Path(sys.argv[0]).name\nif name=="readelf":print(config["readelf"].get(sys.argv[-1],""))\nelif name=="ldd":print(config["ldd"])\n'
     for name in ['readelf','ldd','patchelf']:
      path=tools/name;path.write_text(shim);path.chmod(0o755)
     executable=project/'program';executable.write_bytes(b'original exe')
     library=project/'libexample.so.1.2';library.write_bytes(b'original library')
     settings.write_text(json.dumps({'readelf':{str(executable):'(NEEDED) Shared library: [libexample.so.1]\n(NEEDED) Shared library: [libc.so.6]\n',str(library):'(NEEDED) Shared library: [libm.so.6]\n'},'ldd':f'libexample.so.1 => {library} (0x1)\n'}))
     environment={**os.environ,'PATH':str(tools)+os.pathsep+os.environ['PATH']};outputs=[];commands=[]
     for engine in ['python','rust']:
      dest=root/('linux-'+engine);(dest/'bin').mkdir(parents=True);binary=dest/'bin/program';shutil.copy2(executable,binary);log.write_text('')
      if engine=='python':
       with patch.dict(os.environ,environment,clear=True):actual={'ok':linux_dependencies(executable,binary,dest/'lib',project,[])}
      else:
       request=root/'request.json';request.write_text(json.dumps(dict(system='linux',executable=str(executable),binary=str(binary),destination=str(dest/'lib'),project=str(project),declared=[],hints=[])))
       actual=invoke('bundle',request,environment)
      outputs.append(actual);commands.append(log.read_text().replace(str(dest),'OUTPUT'))
      if (dest/'lib/libexample.so.1.2').read_bytes()!=library.read_bytes():raise RuntimeError('linux copy')
     if outputs[0]!=outputs[1] or commands[0]!=commands[1]:raise RuntimeError((outputs,commands))
     if executable.read_bytes()!=b'original exe' or library.read_bytes()!=b'original library':raise RuntimeError('source mutation')
     checks+=1
    print(checks,'PE/DLL/Linux dependency comparisons passed')
    return checks

def verify_packages(BINARY):
    import contextlib,json,os,plistlib,shutil,subprocess,tarfile,tempfile,zipfile
    from pathlib import Path
    from unittest.mock import patch
    from thorlang.publishing import Publisher
    from tests.test_publish import options,pe_file
    checks=0
    with tempfile.TemporaryDirectory(prefix='thor-native-package-parity-') as temporary:
     root=Path(temporary).resolve();project=root/'project';project.mkdir();(project/'main.thor').write_text('package main\nmain::proc(){}\n')
     assets=project/'assets';assets.mkdir();(assets/'empty').mkdir();(assets/'data.txt').write_text('hello')
     from PIL import Image
     Image.new('RGBA',(32,32),(255,220,0,255)).save(assets/'icon.png')
     tools=root/'tools';tools.mkdir();readelf=tools/'readelf';readelf.write_text('#!/bin/sh\nexit 0\n');readelf.chmod(0o755)
     c=root/'program.c';c.write_text('#include <stdio.h>\nint main(){puts("42");return 0;}\n');native=root/'native';subprocess.run(['clang',str(c),'-o',str(native)],check=True)
     def snapshot(path,system):
      if system=='windows':
       with zipfile.ZipFile(path) as a:return {name:a.read(name) for name in a.namelist() if not name.endswith('/')}
      if system=='linux':
       with tarfile.open(path) as a:return {m.name:a.extractfile(m).read() for m in a.getmembers() if m.isfile()}
      return {p.relative_to(path).as_posix():p.read_bytes() for p in path.rglob('*') if p.is_file() and '_CodeSignature' not in str(p)}
     for system,name in [('windows','package.zip'),('linux','package.tar.gz'),('darwin','previous'),('darwin',"A & B's")]:
      for app_type in ['gui','console']:
       binary=native if system=='darwin' else pe_file(root/'empty.exe') if system=='windows' else root/'elf'
       if system=='linux':binary.write_bytes(b'fake ELF')
       expected=None
       outputs={engine:root/(engine+system+app_type+str(checks)) for engine in ['python','rust']}
       for engine,out in outputs.items():out.mkdir()
       for repetition in range(2):
        snaps={}
        for engine,out in outputs.items():
         stage=out/'stage';stage.mkdir()
         settings=dict(app_name=name,app_type=app_type,publish_dir=out)
         if engine=='python':
          with contextlib.chdir(out),patch('thorlang.publishing.host_target',return_value=(system,'arm64')),patch.dict(os.environ,PATH=str(tools)+os.pathsep+os.environ['PATH']):
           p=Publisher.prepare(project,[],options(**settings));destination=p.package(binary,stage)
         else:
          req=root/'request.json';req.write_text(json.dumps(dict(entry=str(project),flags=[],system=system,arch='arm64',options={**settings,'publish_dir':str(out)},native=False,scratch=str(stage),executable=str(binary))))
          result=subprocess.run([str(BINARY),'publisher',str(req)],cwd=out,env={**os.environ,'PATH':str(tools)+os.pathsep+os.environ['PATH']},capture_output=True,text=True)
          if result.returncode:raise RuntimeError(result.stderr)
          packet=json.loads(result.stdout)
          if 'error' in packet:raise RuntimeError(packet)
          destination=Path(packet['ok']['output'])
         snaps[engine]=snapshot(destination,system)
         shutil.rmtree(stage)
         if system=='darwin':
          info=plistlib.loads((destination/'Contents/Info.plist').read_bytes())
          if info['CFBundleName']!=name:raise RuntimeError(info)
          result=subprocess.run([str(destination/'Contents/MacOS'/out.name)],capture_output=True,text=True)
          if result.stdout!='42\n':raise RuntimeError(result)
        # Executable names derive from different engine cwd: normalize the paths.
        def normalize(data,engine):
         out=outputs[engine];normalized={key.replace(out.name,'EXEC'):value.replace(out.name.encode(),b'EXEC') if key.endswith(('.sh','.command','AppRun','Info.plist')) or key=='AppRun' else value for key,value in data.items()}
         for key in list(normalized):
          if key.endswith('thor-publish.json'):normalized[key]=json.loads(normalized[key])
          if key.endswith('Info.plist'):normalized[key]=plistlib.loads(normalized[key])
          if key.endswith(('AppIcon.icns','AppIcon.ico','AppIcon.png','assets/icon.png')):normalized.pop(key)
          if key.endswith('ThorConsole') or 'Contents/MacOS/' in key:normalized.pop(key)
         return normalized
        left,right=normalize(snaps['python'],'python'),normalize(snaps['rust'],'rust')
        if left!=right:raise RuntimeError((system,app_type,{k for k in left.keys()|right.keys() if left.get(k)!=right.get(k)}))
        checks+=1
    print(checks,'native package/replacement comparisons passed')
    return checks
