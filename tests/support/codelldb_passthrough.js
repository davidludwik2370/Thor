'use strict';
// Native control: forward DAP unchanged. No Thor maps, frames, stepping or values.
const net=require('node:net'),cp=require('node:child_process'),path=require('node:path'),crypto=require('node:crypto');
const {Framing}=require('../../editors/vscode/debug-adapter');
const root=process.argv[2],token=crypto.randomBytes(32).toString('base64');
let child,socket,closing=false;
const pending=[];
function close(){
    if(closing)return;closing=true;
    socket?.destroy();server.close();child?.kill();
}
const input=new Framing(message=>{
    const bytes=Framing.encode(message);
    if(socket)socket.write(bytes);else pending.push(bytes);
});
const server=net.createServer(connection=>{
    socket=connection;server.close();clearTimeout(timer);
    const output=new Framing(message=>process.stdout.write(Framing.encode(message)),token);
    socket.on('data',chunk=>{try{output.feed(chunk);}catch(error){process.stderr.write(error.message+'\n');close();}});
    socket.on('error',error=>{process.stderr.write(error.message+'\n');close();});
    socket.on('close',()=>{close();process.exit(0);});
    for(const bytes of pending)socket.write(bytes);
});
const timer=setTimeout(()=>{process.stderr.write('Native CodeLLDB connection timed out\n');close();process.exit(1);},15000);
server.listen(0,'127.0.0.1',()=>{
    const executable=path.join(root,'adapter',process.platform==='win32'?'codelldb.exe':'codelldb');
    const library=path.join(root,'lldb',process.platform==='win32'?'bin/liblldb.dll':process.platform==='darwin'?'lib/liblldb.dylib':'lib/liblldb.so');
    const env={...process.env};delete env.PYTHONHOME;delete env.PYTHONPATH;delete env.CODELLDB_STARTUP;
    child=cp.spawn(executable,['--liblldb',library,'--connect',String(server.address().port),'--auth-token',token],{env,stdio:['ignore','pipe','pipe']});
    child.stdout.on('data',data=>process.stderr.write(data));child.stderr.on('data',data=>process.stderr.write(data));
    child.on('error',error=>{process.stderr.write(error.message+'\n');close();process.exit(1);});
    child.on('exit',()=>{clearTimeout(timer);close();process.exit(0);});
});
process.stdin.on('data',chunk=>{try{input.feed(chunk);}catch(error){process.stderr.write(error.message+'\n');close();}});
process.stdin.on('end',close);process.on('SIGTERM',()=>{close();process.exit(0);});
