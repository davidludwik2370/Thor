'use strict';
const assert=require('node:assert/strict');
const test=require('node:test');
const {DARK,LIGHT,preset,matches,registerRecommendedColors}=require('../recommended-colors');

test('preset changes only Thor semantic colors and preserves the selected theme',()=>{
    const raw={enabled:true,rules:{'variable:python':'#123456'}};
    const result=preset(raw,{},DARK,true);
    assert.deepEqual(raw,{enabled:true,rules:{'variable:python':'#123456'}});
    assert.equal(result.value.rules['variable:python'],'#123456');
    assert.deepEqual(result.owned,DARK);
    assert.ok(Object.keys(result.owned).every(k=>k.endsWith(':thor')));
    assert.equal(result.value['workbench.colorTheme'],undefined);
});
test('explicit selectors and theme-specific user overrides are preserved',()=>{
    const raw={rules:{'variable.thorCompileTime:thor':'#abcdef','*.readonly:thor':'#112233'},
        '[Dark Modern]':{rules:{'label:thor':'#444444'}}};
    const result=preset(raw,{},DARK,true);
    assert.deepEqual(result.value,raw);
    assert.deepEqual(result.owned,{});
});
test('restore removes only owned unchanged rules and retains later user edits',()=>{
    const initial={rules:{'function:thor':'#abcdef'}};
    const first=preset(initial,{},DARK,true);
    first.value.rules['label:thor']='#ff00ff';
    first.value.rules['type:thor']='#112233';
    const restored=preset(first.value,first.owned,DARK,false);
    assert.deepEqual(restored.value.rules,{'function:thor':'#abcdef','label:thor':'#ff00ff','type:thor':'#112233'});
    assert.deepEqual(restored.owned,{});
});
test('light themes use a readable palette without replacing user preferences',()=>{
    const first=preset(undefined,{},DARK,true);
    const light=preset(first.value,first.owned,LIGHT,true);
    assert.deepEqual(light.value.rules,LIGHT);
    assert.deepEqual(light.owned,LIGHT);
    assert.deepEqual(preset(light.value,light.owned,LIGHT,true),light);
});
test('broad readonly customizations cover compile-time values too',()=>{
    const initial={rules:{'variable.readonly:thor':'#abcdef'}};
    const result=preset(initial,{},DARK,true);
    assert.equal(result.value.rules['variable.thorCompileTime:thor'],undefined);
    assert.equal(result.value.rules['variable.readonly:thor'],'#abcdef');
});
test('a broader user style added later takes precedence over an owned preset',()=>{
    const first=preset(undefined,{},DARK,true);
    first.value.rules['*.readonly:thor']='#abcdef';
    const updated=preset(first.value,first.owned,DARK,true);
    assert.equal(updated.value.rules['variable.thorCompileTime:thor'],undefined);
    assert.equal(updated.value.rules['property.readonly:thor'],undefined);
    assert.equal(updated.value.rules['*.readonly:thor'],'#abcdef');
});
test('disabled preset and malformed settings never replace existing preferences',()=>{
    assert.deepEqual(preset(undefined,{},DARK,false),{value:undefined,owned:{}});
    for (const value of [null,[],false,{rules:[]}]) assert.throws(()=>preset(value,{},DARK,true));
    assert.equal(matches('*.readonly:thor','property.readonly:thor'),true);
    assert.equal(matches('variable:python','variable.thorCompileTime:thor'),false);
});
test('extension registration is opt-in and does not write settings on ordinary startup',async()=>{
    const commands=new Map(),updates=[],state=new Map();let enabled=false,value;
    const context={subscriptions:[],globalState:{get:(k,f)=>state.get(k)||f,update:async(k,v)=>{if(v===undefined)state.delete(k);else state.set(k,v);}}};
    const vscode={ConfigurationTarget:{Global:1},ColorThemeKind:{Light:1,HighContrastLight:4},
        workspace:{getConfiguration:section=>section==='thor'
            ? {get:()=>enabled,update:async(_k,v)=>{enabled=v;}}
            : {inspect:()=>({globalValue:value}),update:async(k,v)=>{updates.push(k);value=v;}},
            onDidChangeConfiguration:()=>({dispose(){}})},
        window:{activeColorTheme:{kind:2},onDidChangeActiveColorTheme:()=>({dispose(){}}),showErrorMessage:message=>assert.fail(message)},
        commands:{registerCommand:(k,fn)=>{commands.set(k,fn);return {dispose(){}};}}};
    const api=registerRecommendedColors(context,vscode);
    await api.reconcile();assert.equal(updates.length,0);
    await commands.get('thor.applyRecommendedColors')();assert.deepEqual(value.rules,DARK);
    await commands.get('thor.restoreRecommendedColors')();assert.deepEqual(value.rules,{});
    assert.ok(updates.every(k=>k==='semanticTokenColorCustomizations'));
});
