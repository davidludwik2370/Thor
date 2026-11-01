'use strict';
const { test, before } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const textmate = require('vscode-textmate');
const oniguruma = require('vscode-oniguruma');

let grammar, themedGrammar, colorMap;
before(async () => {
    const wasm = fs.readFileSync(require.resolve('vscode-oniguruma/release/onig.wasm'));
    await oniguruma.loadWASM(wasm.buffer.slice(wasm.byteOffset, wasm.byteOffset + wasm.byteLength));
    const onigLib=Promise.resolve({createOnigScanner:sources=>new oniguruma.OnigScanner(sources),createOnigString:s=>new oniguruma.OnigString(s)});
    const registry = new textmate.Registry({
        onigLib,
        loadGrammar: async scope => scope === 'source.thor'
            ? JSON.parse(fs.readFileSync(path.join(__dirname, '../syntaxes/thor.tmLanguage.json'), 'utf8')) : null
    });
    grammar = await registry.loadGrammar('source.thor');
    const themed = new textmate.Registry({
        theme: {settings:[
            {settings:{foreground:'#D4D4D4',background:'#1E1E1E'}},
            {scope:'string',settings:{foreground:'#CE9178'}},
            {scope:'comment',settings:{foreground:'#6A9955'}},
            {scope:'meta.embedded',settings:{foreground:'#D4D4D4'}},
            {scope:'variable',settings:{foreground:'#9CDCFE'}},
            {scope:['entity.name.namespace','entity.name.type','support.type'],settings:{foreground:'#4EC9B0'}},
            {scope:'entity.name.function',settings:{foreground:'#DCDCAA'}},
            {scope:'storage.type',settings:{foreground:'#569CD6'}},
            {scope:'storage.modifier',settings:{foreground:'#569CD6'}},
            {scope:'constant.numeric',settings:{foreground:'#B5CEA8'}},
            {scope:'keyword.control',settings:{foreground:'#C586C0'}},
            {scope:'entity.name.tag',settings:{foreground:'#569CD6'}},
            {scope:'entity.name.label.thor',settings:{foreground:'#8582DE'}},
        ]},
        onigLib,
        loadGrammar:async()=>JSON.parse(fs.readFileSync(path.join(__dirname,'../syntaxes/thor.tmLanguage.json'),'utf8')),
    });
    const contribution=require('../package.json').contributes.grammars.find(g=>g.language==='thor');
    themedGrammar=await themed.loadGrammarWithConfiguration('source.thor',1,{
        balancedBracketSelectors:contribution.balancedBracketScopes||['*'],
        unbalancedBracketSelectors:contribution.unbalancedBracketScopes||[],
    });
    colorMap=themed.getColorMap();
});

function tokenize(source) {
    let state = textmate.INITIAL;
    return source.split('\n').map(line => {
        const result = grammar.tokenizeLine(line, state);
        state = result.ruleStack;
        return result.tokens.map(token => ({ text: line.slice(token.startIndex, token.endIndex), scopes: token.scopes }));
    });
}

function check(source, word, scope, line = 0) {
    const tokens = tokenize(source)[line];
    const token = tokens.find(t => t.text === word);
    assert.ok(token, `Missing token ${JSON.stringify(word)}: ${JSON.stringify(tokens)}`);
    assert.ok(token.scopes.some(s => s.startsWith(scope)), `${word}: ${token.scopes.join(', ')} lacks ${scope}`);
}

function themedTokens(source) {
    let state=textmate.INITIAL;
    return source.split('\n').map(line=>{
        const result=themedGrammar.tokenizeLine2(line,state);state=result.ruleStack;
        return Array.from({length:line.length},(_,at)=>{
            let i=0;while(i+2<result.tokens.length&&result.tokens[i+2]<=at)i+=2;
            const metadata=result.tokens[i+1];
            return {color:colorMap[(metadata>>>15)&511],type:(metadata>>>8)&3,balanced:!!(metadata&1024)};
        });
    });
}

test('arbitrary-precision contexts retain complete numeric and ordinary identifier tokens',()=>{
    const digits='1234567890'.repeat(600);
    check(`value: Integer = ${digits}`,digits,'constant.numeric');
    check('ratio: Rational = 0.10000000000000000000000000000000000001',
        '0.10000000000000000000000000000000000001','constant.numeric');
    check('Integer := 3','Integer','variable'); // Predefined type names remain shadowable.
    check('n := ratio.numerator','numerator','variable');
});

for (const [name, source, word, scope, line] of [
    ['package', 'package main', 'main', 'entity.name.namespace'],
    ['selective import', 'import "core:math" / sin, cos', 'import', 'keyword.control.import'],
    ['wildcard import', 'import * "thor:basic"', 'import', 'keyword.control.import'],
    ['named import', 'import math "core:math" / sin', 'math', 'variable.other.namespace'],
    ['multiline import ends', 'import "core:math" / sin,\n cos\nmain :: proc() {}', 'main', 'entity.name.function', 2],
    ['comma continuation', 'import "core:math" /\n sin, // comment\n cos', 'cos', 'variable', 2],
    ['Thor if', '#if true {', '#if', 'entity.name.tag'],
    ['Thor else', '} #else if false {', '#else', 'entity.name.tag'],
    ['compile switch', 'x :: #switch 1 {case:2}', '#switch', 'entity.name.tag'],
    ['pure function keyword', 'square :: fn(x:int)->int{return x*x}', 'fn', 'storage.type'],
    ['pure function name', 'square :: fn(x:int)->int{return x*x}', 'square', 'entity.name.function'],
    ['inline procedure default keyword', 'less := proc(i,j:E)->bool{return i<j}', 'proc', 'storage.type'],
    ['inline pure default keyword', 'less := fn(i,j:E)->bool{return i<j}', 'fn', 'storage.type'],
    ['procedure argument keyword', 'apply(proc(i:int)->int{return i})', 'proc', 'storage.type'],
    ['contextless procedure expression keyword', 'return proc "contextless" (i:int)->int{return i}', 'proc', 'storage.type'],
    ['ordinary function named fn', 'value := fn(data)', 'fn', 'entity.name.function'],
    ['ordinary fn call with a slice', 'value := fn(data[1:])', 'fn', 'entity.name.function'],
    ['explicit run', 'SIZE :: #run square(8)', '#run', 'entity.name.tag'],
    ['allocated pure return', 'names :: fn()->[]string{return nil}', 'fn', 'storage.type'],
    ['allocated run binding', 'names := #run names()', '#run', 'entity.name.tag'],
    ['automatic constant call', 'SIZE :: square(8)', 'square', 'entity.name.function'],
    ['generic dependent run', 'work :: proc($N:int){SIZE::#run square(N)}', '#run', 'entity.name.tag'],
    ['interpolation prefix', 's := f"hello {value}"', 'f', 'storage.modifier.interpolation'],
    ['manual interpolation prefix', 's := f!"hello {value}"', 'f!', 'storage.modifier.interpolation'],
    ['manual raw interpolation field', 's := f!`hello {value}`', 'value', 'variable'],
    ['interpolation field', 's := f"hello {value}"', 'value', 'variable'],
    ['nested interpolation string', 's := f"{echo("inner")}"', 'inner', 'string.quoted.double'],
    ['raw interpolation field', 's := f`hello {value}`', 'value', 'variable'],
    ['brace fill stays in format', 's := f"{1:\'}\'>3} after"', ' after', 'string.quoted.double'],
    ['nested switch field', 's := f"{switch x{case 1:value;case:0}}"', 'value', 'variable'],
    ['nested Set literal field', 's := f"{len(set[int]{1,2})}"', 'set', 'storage.type.set'],
    ['set literal type', 's := set[int]{1,2}', 'set', 'storage.type.set'],
    ['set annotated type', 's: set[int]', 'set', 'storage.type.set'],
    ['symmetric difference assignment', 'a ~= b', '~=', 'keyword.operator.assignment.compound'],
    ['symmetric difference', 'a ~ (b)', '~', 'keyword.operator'],
    ['pointer dereference before assignment', 'p^=value', '^', 'keyword.operator'],
    ['ordinary set variable indexed', 'set[i]', 'set', 'variable'],
    ['native when', 'when true {', 'when', 'keyword.control'],
    ['native else when', '} else when true {', 'when', 'keyword.control'],
    ['membership', 'x in [1, 2]', 'in', 'keyword.control'],
    ['range membership', 'x in 0..<10', '..<', 'keyword.operator.range'],
    ['power operator', 'x := 2**3**2', '**', 'keyword.operator.power-or-expansion'],
    ['native expansion operator', 'sum(**pair)', '**', 'keyword.operator.power-or-expansion'],
    ['interpolated power', 's := f"power {value**3}"', '**', 'keyword.operator.power-or-expansion'],
    ['inclusive range membership', 'x not_in 0..=10', '..=', 'keyword.operator.range'],
    ['negative membership', 'x not_in xs', 'not_in', 'keyword.control'],
    ['type constraints', 'f :: proc(x: $T) -> T where T in [int, f64] {}', 'where', 'keyword.control'],
    ['type list int', 'T in [int, f64]', 'int', 'support.type.primitive'],
    ['array number', 'a := [1, 2]', '1', 'constant.numeric'],
    ['array string', 'a := ["one", "two"]', 'one', 'string.quoted.double'],
    ['typed array', 'a: [2]f64 = [1, 2]', 'f64', 'support.type.primitive'],
    ['native inferred array', 'a := [?]int{1, 2}', '?', 'keyword.operator.array'],
    ['native slice', 's := a[1:]', ':', 'keyword.operator.slice'],
    ['ternary colon', 'x := true ? 1\n  : 2', ':', 'keyword.operator.ternary', 1],
    ['nested ternary', 'x := false ? 1\n : true ? 2\n : 3', ':', 'keyword.operator.ternary', 2],
    ['generic parameter', 'f :: proc(x: $T) -> T {}', 'T', 'entity.name.type.parameter'],
    ['immutable binding', 'x :> 3', 'x', 'variable.other.readonly'],
    ['immutable inferred operator', 'x :> 3', ':>', 'keyword.operator.assignment.immutable'],
    ['immutable typed binding', 'x: int > 3', 'x', 'variable.other.readonly'],
    ['immutable typed operator', 'x: int > 3', '>', 'keyword.operator.assignment.immutable'],
    ['immutable typed string', 'x: string > "orange"', 'orange', 'string.quoted.double'],
    ['immutable proc type arrow', 'x: proc()->int > impl', '->', 'storage.type.function.arrow'],
    ['future fn remains identifier', 'fn := 3', 'fn', 'variable'],
    ['future set remains identifier', 'set := 3', 'set', 'variable'],
    ['file tag', '#+build darwin', '#+build', 'entity.name.tag'],
    ['directive', '#assert(true)', '#assert', 'entity.name.tag'],
]) {
    test(name, () => check(source, word, scope, line));
}

test('comments hide Thor syntax and nested comments close correctly', () => {
    const lines = tokenize('/* outer\n /* #if x in [1,2] */\n still a comment */\n#if true {}');
    for (const token of lines[1]) assert.ok(token.scopes.some(s => s.startsWith('comment.block')));
    assert.ok(lines[3].find(t => t.text === '#if').scopes.includes('entity.name.tag.thor'));
});

test('commented array and procedure brackets have comment colors and no bracket-pair metadata',()=>{
    const procedure=[
        'drawPath :: proc(using node: Node, color: rl.Color) {',
        '    center :> [2]i32{i32(pos.x * GRID_SIZE + GRID_SIZE/2), i32(pos.y)}',
        '    parentCenter :> [2]i32{i32(parent.pos.x), i32(parent.pos.y)}',
        '    rl.DrawLine(center.x, center.y, parentCenter.x, parentCenter.y, color)',
        '    rl.DrawCircle(center.x, center.y, 2, color)',
        '}',
    ];
    for(const commented of [procedure.map(line=>'// '+line).join('\n'),
        '/* outer [\n'+procedure.join('\n')+'\n/* nested ( ] } */\nend */']){
        const source=commented+'\n'+procedure.join('\n');
        const lines=source.split('\n'),tokens=themedTokens(source);
        const commentLines=commented.split('\n').length;
        for(let line=0;line<lines.length;line++)for(let at=0;at<lines[line].length;at++){
            if(!'[](){}'.includes(lines[line][at]))continue;
            const token=tokens[line][at];
            if(line<commentLines){
                assert.equal(token.color,'#6A9955',`${line}:${at} comment color`);
                assert.equal(token.type,1,`${line}:${at} comment token type`);
                assert.equal(token.balanced,false,`${line}:${at} comment bracket pairing`);
            }else{
                assert.equal(token.type,0,`${line}:${at} live code token type`);
                assert.equal(token.balanced,true,`${line}:${at} live code bracket pairing`);
            }
        }
    }
});

test('comments inside interpolation disable only their own bracket pairing',()=>{
    const source='value := f"{( /* comment [({ */ 2 /* end })] */ )}"';
    const tokens=themedTokens(source)[0];
    for(let at=0;at<source.length;at++){
        if(!'[](){}'.includes(source[at]))continue;
        // The two block comments are inside an embedded expression. Their
        // scopes must be excluded without disabling the surrounding code.
        const inComment=(at>=source.indexOf('/* comment')&&at<source.indexOf('*/')+2)||
            (at>=source.indexOf('/* end')&&at<source.lastIndexOf('*/')+2);
        assert.equal(tokens[at].balanced,!inComment,`Bracket ${at}: ${source[at]}`);
        assert.equal(tokens[at].type,inComment?1:0,`Token type ${at}`);
    }
});

test('raw strings retain syntax-looking text across lines', () => {
    const lines = tokenize('text := `#if\nx in [1,2]\n`\n#if true {}');
    for (const token of lines[1]) assert.ok(token.scopes.some(s => s.startsWith('string.quoted.raw')));
    assert.ok(lines[3].find(t => t.text === '#if').scopes.includes('entity.name.tag.thor'));
});

test('label declarations and branch targets share their own purple scope',()=>{
    for(const source of [
        'row_check:\nfor &row in board { continue row_check; break row_check }',
        'row_check: for &row in board { continue row_check; break row_check }',
        'row_check: // next line\nfor &row in board { continue row_check }',
        'row_check: if ready { break row_check }',
        'row_check: switch selector {case: break row_check}',
        'row_check: { break row_check }',
        'row_check: for { value:=next() or_break row_check; value=next() or_continue row_check }',
    ]){
        const tokens=tokenize(source),colors=themedTokens(source),lines=source.split('\n');
        for(let line=0;line<lines.length;line++)for(const match of lines[line].matchAll(/row_check/g)){
            const token=tokens[line].find(t=>t.text==='row_check'&&t.scopes.includes('entity.name.label.thor'));
            assert.ok(token,source);
            assert.ok(colors[line].slice(match.index,match.index+match[0].length).every(t=>t.color==='#8582DE'),source);
        }
        const colon=lines[0].indexOf(':');
        assert.equal(colors[0][colon].color,'#D4D4D4','The colon remains punctuation');
    }
    for(const source of ['// row_check: for {}','text := "continue row_check"','value: for_values']){
        assert.ok(!tokenize(source).flat().some(t=>t.scopes.includes('entity.name.label.thor')),source);
    }
});

test('escaped quotes stay inside strings', () => {
    const tokens = tokenize('s := "\\\"#if in [1,2]"')[0];
    for (const token of tokens.filter(t => t.text.includes('#if'))) {
        assert.ok(token.scopes.some(s => s.startsWith('string.quoted.double')));
    }
});

test('compile-time directive colors include the if in an else-if chain',()=>{
    for(const source of ['#if true {}','#else {}','#else if condition {}','value :: #switch 1 {case:2}','value :: #run compute(3)','#partial switch x {}','#assert(true)']){
        const tokens=themedTokens(source)[0];
        for(const match of source.matchAll(/#(?:if|else|switch|run|partial|assert)\b/g)){
            assert.ok(tokens.slice(match.index,match.index+match[0].length).every(t=>t.color==='#569CD6'),source);
        }
        if(source.startsWith('#else if')){
            assert.ok(tokens.slice(6,8).every(t=>t.color==='#569CD6'));
            assert.ok(tokens.slice(9,18).every(t=>t.color==='#9CDCFE'),'Condition keeps its own color');
        }
    }
    const source='if x {} else if y {} switch z {}';
    for(const match of source.matchAll(/\b(if|else|switch)\b/g)){
        assert.ok(themedTokens(source)[0].slice(match.index,match.index+match[0].length).every(t=>t.color==='#C586C0'));
    }
});

test('compile-time else-if handles trivia without coloring later runtime keywords',()=>{
    for(const between of [' ','\t  ','\n',' /* comment */ ',' // comment\n',' /* outer /* nested */ comment */\n']){
        const source='#else'+between+'if condition {if runtime {}}';
        const tokens=tokenize(source).flat();
        const ifs=tokens.filter(t=>t.text==='if');
        assert.equal(ifs.length,2,source);
        assert.ok(ifs[0].scopes.includes('entity.name.tag.thor'),source);
        assert.ok(ifs[1].scopes.includes('keyword.control.thor'),source);
        const condition=tokens.find(t=>t.text==='condition');
        assert.ok(!condition.scopes.includes('entity.name.tag.thor'),source);
    }
    for(const source of ['#else { if runtime {} }','#else iffy { if runtime {} }']){
        assert.ok(tokenize(source).flat().filter(t=>t.text==='if').every(t=>t.scopes.includes('keyword.control.thor')),source);
    }
    for(const source of ['text := "#else if"','text := `#else if`','// #else if','/* #else if */']){
        assert.ok(!tokenize(source).flat().some(t=>t.scopes.includes('entity.name.tag.thor')),source);
    }
});

test('whole import paths retain string scopes', () => {
    for (const [source, value] of [
        ['import "core:fmt" / println', 'core:fmt'],
        ['import math "core:math" / sin', 'core:math'],
        ['import "library" / Record', 'library'],
        ['import * "thor:basic"', 'thor:basic'],
        ['import _ "thor:basic" / print', 'thor:basic'],
    ]) {
        check(source, value, 'string.quoted.double');
    }
});

test('main demo tokenizes without illegal or unclosed string/comment scopes', () => {
    const source = fs.readFileSync(path.join(__dirname, '../../../demo/main.thor'), 'utf8');
    const lines = tokenize(source);
    const all = lines.flat();
    assert.ok(!all.some(t => t.scopes.some(s => s.startsWith('invalid.illegal'))));
    const last = lines.findLast(line => line.some(t => t.text.trim()));
    assert.ok(last.every(t => !t.scopes.some(s => s.startsWith('string.') || s.startsWith('comment.'))));
});

test('nested interpolations retain string colors and ordinary expression colors',()=>{
    const colors=source=>themedTokens(source)[0].map(t=>t.color);
    const source='text := f"outer {f"inner {mark(3)}"} {mark(1)} {mark(2)}"';
    const actual=colors(source);
    for(const [word,color] of [['outer ','#CE9178'],['inner ','#CE9178'],['mark','#DCDCAA'],['3','#B5CEA8'],['1','#B5CEA8'],['2','#B5CEA8']]){
        const start=source.indexOf(word);
        assert.ok(actual.slice(start,start+word.length).every(c=>c===color),`${word}: ${actual.slice(start,start+word.length)}`);
    }
    for(const source of ['assert(text == "{fields} 1 2 0001,234,567")','case 1: "first"','case: `raw {not_a_field}`']){
        const actual=colors(source),start=source.search(/["`]/);
        assert.ok(actual.slice(start,source.lastIndexOf(source[start])+1).every(c=>c==='#CE9178'));
    }
});

test('reported ternary interpolation has ordinary expressions and nested string text',()=>{
    const source='outer := f"{(allocated ? f"nested {7}" : borrowed)}"';
    const tokens=themedTokens(source)[0];
    for(const [word,color,type] of [['allocated','#9CDCFE',0],['nested ','#CE9178',2],['7','#B5CEA8',0],['borrowed','#9CDCFE',0]]){
        const start=source.indexOf(word);
        for(const token of tokens.slice(start,start+word.length))assert.deepEqual({color:token.color,type:token.type},{color,type},word);
    }
    for(let at=0;at<source.length;at++){
        if('{}()'.includes(source[at])){
            assert.equal(tokens[at].type,0,`Expression bracket at ${at} treated as string text`);
            assert.ok(tokens[at].balanced);
        }
        if(source[at]==='f')assert.equal(tokens[at].type,0,'Interpolation prefix is syntax');
    }
});

test('manual interpolation nests with automatic interpolation without leaking string scopes',()=>{
    const source='show(f!"outer {f"inner {7}"} {value}"); next := 3';
    const tokens=themedTokens(source)[0];
    for(const word of ['outer ','inner ']){
        const start=source.indexOf(word);
        assert.ok(tokens.slice(start,start+word.length).every(t=>t.color==='#CE9178'));
    }
    for(const word of ['f!','value','next']){
        const start=source.indexOf(word);
        assert.ok(tokens.slice(start,start+word.length).every(t=>t.type===0),word);
    }
});

test('aggregate return braces and interpolation fields are code, escaped braces are text',()=>{
    const source='return {f"report {text}",rows,values}';
    const tokens=themedTokens(source)[0];
    for(let at=0;at<source.length;at++)if('{}'.includes(source[at])){
        assert.equal(tokens[at].type,0,`Code brace at ${at} treated as string text`);
        assert.ok(tokens[at].balanced);
    }
    const escaped='text := f"{{fields}}"';
    for(const [at,ch] of [...escaped].entries())if('{}'.includes(ch))assert.equal(themedTokens(escaped)[0][at].type,2);
});

test('declared package names are green, namespace references blue, and set/map syntax dark blue',()=>{
    assert.ok(themedTokens('package main')[0].slice('package '.length).every(t=>t.color==='#4EC9B0'));
    for(const [source,word] of [['import sets "thor:sets"','sets'],['names: sets.Set(string)','sets']]){
        const start=source.indexOf(word);
        assert.ok(themedTokens(source)[0].slice(start,start+word.length).every(t=>t.color==='#9CDCFE'));
    }
    for(const [source,word] of [['a := set[int]{1}','set'],['a: set[int]','set'],['a: map[int]int','map']]){
        const start=source.indexOf(word);
        assert.ok(themedTokens(source)[0].slice(start,start+word.length).every(t=>t.color==='#569CD6'),source);
    }
    const manifest=require('../package.json');
    assert.deepEqual(manifest.contributes.semanticTokenScopes,[{language:'thor',scopes:{
        namespace:['variable.other.namespace.thor'],
        'parameter.readonly':['variable.other.constant.thor'],
        label:['entity.name.label.thor']
    }}]);
});

test('inline comparator procedure keywords are dark blue in a multiline generic signature',()=>{
    const source='sorted :: proc(\n    a: $T/[]$E,\n    less := proc(i,j:E)->bool{return i<j},\n    allocator := context.allocator\n)->[]E { return a }';
    const lines=source.split('\n'),tokens=themedTokens(source);
    for(const [line,word] of [[0,'proc'],[2,'proc']]){
        const start=lines[line].indexOf(word);
        assert.ok(tokens[line].slice(start,start+word.length).every(t=>t.color==='#569CD6'));
        assert.ok(!tokenize(source)[line].find(t=>t.text===word).scopes.includes('entity.name.function.thor'));
    }
    const allocator=tokenize(source)[3].find(t=>t.text==='allocator');
    assert.ok(allocator.scopes.includes('meta.parameters.thor'),'Inner callback parentheses must not close the outer parameter list');
});

test('format fills are rune literals, widths are numbers, format punctuation stays neutral',()=>{
    for(const source of [
        'text := f`raw \\n {"é😀":\'界\'^5}`',
        'text := f"{1234567:\'0\'>12,}"',
        'text := f"{1:\'}\'>3} after"',
        'text := f`{1:\'`\'<3}`',
        String.raw`text := f"{1:'\u754c'^5}"`,
    ]){
        const tokens=themedTokens(source)[0],format=source.indexOf(':',source.indexOf('{'));
        const open=source.indexOf("'",format),close=source.indexOf("'",open+1);
        for(const token of tokens.slice(open,close+1))assert.equal(token.type,2,'Fill is a rune literal');
        assert.equal(tokens[open].color,'#CE9178');
        for(let at=close+1;at<source.indexOf('}',close+1);at++){
            if(/\d/.test(source[at]))assert.deepEqual({color:tokens[at].color,type:tokens[at].type},{color:'#B5CEA8',type:0});
            if(/[<>^,]/.test(source[at])){
                assert.equal(tokens[at].type,0);
                assert.equal(tokens[at].color,'#D4D4D4');
            }
        }
        assert.equal(tokens[format].color,'#D4D4D4');
    }
});

test('reported colors and bracket metadata survive the full demo context',()=>{
    const source=fs.readFileSync(path.join(__dirname,'../../../demo/main.thor'),'utf8');
    const lines=source.split('\n'),tokens=themedTokens(source);
    for(const [fragment,word,color,type] of [
        ['return {f"report','{',undefined,0],
        ['outer := f"{(allocated','allocated','#9CDCFE',0],
        ['outer := f"{(allocated','nested ','#CE9178',2],
        ['names: sets.Set','sets','#9CDCFE',0],
        ['a := set[int]{1}','set','#569CD6',0],
    ]){
        const line=lines.findIndex(line=>line.includes(fragment));assert.ok(line>=0,fragment);
        const start=lines[line].indexOf(word);
        for(const token of tokens[line].slice(start,start+word.length)){
            assert.equal(token.type,type,word);
            if(color)assert.equal(token.color,color,word);
        }
    }
});

test('demo regression expressions retain complete identifier and literal scopes',()=>{
    check('case 3..<5: [.Red, .Blue]','Red','variable');
    check('either := fruit ~ more_fruit','fruit','variable');
    check('assert(len(combined) == 3 && len(either) == 2 && fruit != more_fruit)','combined','variable');
    check('text := f"{{fields}} {mark(1)}"','fields','string.quoted');
    check('text := f"{switch n{case 1:"first";case:"second"}}"','first','string.quoted');
});

test('commented walkthrough keeps strings and syntax distinct across topic sections',()=>{
    const source=fs.readFileSync(path.join(__dirname,'../../../examples/demo/demo.thor'),'utf8');
    const lines=source.split('\n'),tokens=themedTokens(source);
    for(const [fragment,word,color,type] of [
        ['message := f"Meet','place','#9CDCFE',0],
        ['message := f"Meet',"'0'",'#CE9178',2],
        ['message := f"Meet','9','#B5CEA8',0],
        ['nested := f"outside','inside ','#CE9178',2],
        ['outer := f"{(allocate_inner','allocate_inner','#9CDCFE',0],
        ['outer := f"{(allocate_inner','fresh ','#CE9178',2],
        ['report := f"last','ready','#CE9178',2],
        ['staff: set[string]','set','#569CD6',0],
        ['title=f"journey','label','#9CDCFE',0],
    ]){
        const line=lines.findIndex(line=>line.includes(fragment));assert.ok(line>=0,fragment);
        const start=lines[line].indexOf(word);assert.ok(start>=0,word);
        for(const token of tokens[line].slice(start,start+word.length)){
            assert.equal(token.type,type,`${fragment}: ${word}`);
            assert.equal(token.color,color,`${fragment}: ${word}`);
        }
    }
});

test('manifest owns Thor without replacing Odin', () => {
    const manifest = require('../package.json');
    assert.deepEqual(manifest.contributes.languages[0].extensions, ['.thor']);
    assert.equal(manifest.contributes.languages[0].id, 'thor');
    assert.equal(manifest.contributes.grammars[0].scopeName, 'source.thor');
});
