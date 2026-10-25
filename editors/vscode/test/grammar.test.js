'use strict';
const { test, before } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const textmate = require('vscode-textmate');
const oniguruma = require('vscode-oniguruma');

let grammar;
before(async () => {
    const wasm = fs.readFileSync(require.resolve('vscode-oniguruma/release/onig.wasm'));
    await oniguruma.loadWASM(wasm.buffer.slice(wasm.byteOffset, wasm.byteOffset + wasm.byteLength));
    const registry = new textmate.Registry({
        onigLib: Promise.resolve({ createOnigScanner: sources => new oniguruma.OnigScanner(sources), createOnigString: s => new oniguruma.OnigString(s) }),
        loadGrammar: async scope => scope === 'source.thor'
            ? JSON.parse(fs.readFileSync(path.join(__dirname, '../syntaxes/thor.tmLanguage.json'), 'utf8')) : null
    });
    grammar = await registry.loadGrammar('source.thor');
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

for (const [name, source, word, scope, line] of [
    ['package', 'package main', 'main', 'entity.name.type.module'],
    ['selective import', 'import "core:math" / sin, cos', 'import', 'keyword.control.import'],
    ['named import', 'import math "core:math" / sin', 'math', 'entity.name.namespace'],
    ['multiline import ends', 'import "core:math" / sin,\n cos\nmain :: proc() {}', 'main', 'entity.name.function', 2],
    ['comma continuation', 'import "core:math" /\n sin, // comment\n cos', 'cos', 'variable', 2],
    ['Thor if', '#if true {', '#if', 'keyword.control.conditional'],
    ['Thor else', '} #else if false {', '#else', 'keyword.control.conditional'],
    ['compile switch', 'x :: #switch 1 {case:2}', '#switch', 'keyword.control.conditional'],
    ['pure function keyword', 'square :: fn(x:int)->int{return x*x}', 'fn', 'storage.type'],
    ['pure function name', 'square :: fn(x:int)->int{return x*x}', 'square', 'entity.name.function'],
    ['explicit run', 'SIZE :: #run square(8)', '#run', 'keyword.control.conditional'],
    ['allocated pure return', 'names :: fn()->[]string{return nil}', 'fn', 'storage.type'],
    ['allocated run binding', 'names := #run names()', '#run', 'keyword.control.conditional'],
    ['automatic constant call', 'SIZE :: square(8)', 'square', 'entity.name.function'],
    ['generic dependent run', 'work :: proc($N:int){SIZE::#run square(N)}', '#run', 'keyword.control.conditional'],
    ['interpolation prefix', 's := f"hello {value}"', 'f', 'storage.modifier.interpolation'],
    ['interpolation field', 's := f"hello {value}"', 'value', 'variable'],
    ['nested interpolation string', 's := f"{echo("inner")}"', 'inner', 'string.quoted.double'],
    ['raw interpolation field', 's := f`hello {value}`', 'value', 'variable'],
    ['brace fill stays in format', 's := f"{1:}>3} after"', ' after', 'string.quoted.double'],
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
    assert.ok(lines[3].find(t => t.text === '#if').scopes.includes('keyword.control.conditional.thor'));
});

test('raw strings retain syntax-looking text across lines', () => {
    const lines = tokenize('text := `#if\nx in [1,2]\n`\n#if true {}');
    for (const token of lines[1]) assert.ok(token.scopes.some(s => s.startsWith('string.quoted.raw')));
    assert.ok(lines[3].find(t => t.text === '#if').scopes.includes('keyword.control.conditional.thor'));
});

test('escaped quotes stay inside strings', () => {
    const tokens = tokenize('s := "\\\"#if in [1,2]"')[0];
    for (const token of tokens.filter(t => t.text.includes('#if'))) {
        assert.ok(token.scopes.some(s => s.startsWith('string.quoted.double')));
    }
});

test('whole import paths retain string scopes', () => {
    for (const [source, value] of [
        ['import "core:fmt" / println', 'core:fmt'],
        ['import math "core:math" / sin', 'core:math'],
        ['import "library" / Record', 'library'],
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

test('manifest owns Thor without replacing Odin', () => {
    const manifest = require('../package.json');
    assert.deepEqual(manifest.contributes.languages[0].extensions, ['.thor']);
    assert.equal(manifest.contributes.languages[0].id, 'thor');
    assert.equal(manifest.contributes.grammars[0].scopeName, 'source.thor');
});
