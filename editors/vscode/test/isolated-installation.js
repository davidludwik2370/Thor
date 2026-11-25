'use strict';
// Unit fixtures that expect PATH fallback must not discover the developer's
// real registered installation. Node runs each test file in its own process.
const fs=require('node:fs');
const os=require('node:os');
const path=require('node:path');
const test=require('node:test');
const directory=fs.mkdtempSync(path.join(os.tmpdir(),'thor-editor-unit-'));
for(const key of ['HOME','USERPROFILE'])process.env[key]=directory;
process.env.LOCALAPPDATA=path.join(directory,'AppData','Local');
process.env.XDG_CONFIG_HOME=path.join(directory,'.config');
delete process.env.THOR_ROOT;
test.after(()=>fs.rmSync(directory,{recursive:true,force:true}));
