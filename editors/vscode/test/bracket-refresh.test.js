'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const {changesCommentDelimiter, registerBracketRefresh} = require('../bracket-refresh');

function changed(source, offset, length, text) {
    return changesCommentDelimiter(source, [{rangeOffset: offset, rangeLength: length, text}]);
}

test('comment delimiters are detected on insert, removal and split single-character edits', () => {
    assert.ok(changed('print(a)', 0, 0, '// '));
    assert.ok(changed('  // print(a)', 2, 3, ''));
    assert.ok(changed('/ print(a)', 1, 0, '/'));
    assert.ok(changed('// print(a)', 0, 1, ''));
    for (const [before, offset, length, text] of [
        ['value', 0, 0, '/*'], ['value', 5, 0, '*/'],
        ['/* value */', 0, 2, ''], ['/* value */', 9, 2, ''],
        ['/ value', 1, 0, '*'], ['* value', 0, 0, '/'],
        ['/* value */', 1, 1, ''], ['/* value */', 10, 1, ''],
    ]) assert.ok(changed(before, offset, length, text), JSON.stringify([before, offset, length, text]));
});

test('ordinary code and text edits inside existing comments do not refresh bracket caches', () => {
    for (const [source, at, length, text] of [
        ['value := 1', 9, 1, '2'], ['// text (value)', 3, 4, 'comment'],
        ['/* text (value) */', 3, 4, 'comment'], ['x / y', 2, 0, ' '],
        ['a+b', 1, 1, '*'], ['// text', 7, 0, ' more'],
    ]) assert.equal(changed(source, at, length, text), false, source);
    assert.equal(changesCommentDelimiter('// unchanged', []), false);
});

function environment() {
    const callbacks = {}, commands = new Map(), overlays = [];
    const document = {languageId:'thor', uri:{toString:()=> 'file:///demo.thor'}, text:'main::proc(){}',
        getText() { return this.text; }};
    const listen = name => callback => {
        callbacks[name] = callback;
        return {dispose() { delete callbacks[name]; }};
    };
    const context = {subscriptions:[]};
    const vscode = {
        workspace:{textDocuments:[document], onDidOpenTextDocument:listen('open'),
            onDidCloseTextDocument:listen('close'), onDidChangeTextDocument:listen('change')},
        commands:{registerCommand(name, callback) { commands.set(name, callback); return {dispose() {commands.delete(name);}}; }},
        languages:{setLanguageConfiguration(language, configuration) {
            assert.equal(language, 'thor');
            assert.deepEqual(configuration, {}, 'The overlay must not replace any existing language rules');
            const overlay = {disposed:false, dispose() {this.disposed=true;}};
            overlays.push(overlay);
            return overlay;
        }},
    };
    registerBracketRefresh(context, vscode);
    return {document, callbacks, commands, overlays, dispose() {context.subscriptions.forEach(item=>item.dispose());},
        change(offset, length, text) {
            document.text = document.text.slice(0, offset) + text + document.text.slice(offset + length);
            callbacks.change({document, contentChanges:[{rangeOffset:offset, rangeLength:length, text}]});
        }};
}

test('rapid toggle and undo edits share a delayed refresh; normal edits add none', t => {
    t.mock.timers.enable({apis:['setTimeout']});
    const env = environment();
    try {
        env.change(0, 0, '// ');
        t.mock.timers.tick(60);
        env.change(0, 3, '');
        t.mock.timers.tick(60);
        assert.equal(env.overlays.length, 0);
        t.mock.timers.tick(40);
        assert.equal(env.overlays.length, 1);
        env.change(0, 0, ' ');
        t.mock.timers.tick(500);
        assert.equal(env.overlays.length, 1);
        env.change(0, 0, '// ');
        t.mock.timers.tick(100);
        assert.equal(env.overlays.length, 2);
        assert.equal(env.overlays[0].disposed, true, 'Old overlays must be released');
    } finally {env.dispose();}
    assert.ok(env.overlays.every(overlay=>overlay.disposed));
});

test('manual recovery works without an edit and cancels pending refresh; disposal releases timers', t => {
    t.mock.timers.enable({apis:['setTimeout']});
    const env = environment();
    env.change(0, 0, '// ');
    env.commands.get('thor.refreshBracketHighlighting')();
    t.mock.timers.tick(200);
    assert.equal(env.overlays.length, 1);
    env.change(0, 3, '');
    env.dispose();
    t.mock.timers.tick(200);
    assert.equal(env.overlays.length, 1);
    assert.equal(env.overlays[0].disposed, true);
});

test('document open/close and non-Thor files do not leave stale source snapshots', t => {
    t.mock.timers.enable({apis:['setTimeout']});
    const env = environment();
    try {
        env.document.languageId = 'odin';
        env.change(0, 0, '// ');
        t.mock.timers.tick(200);
        assert.equal(env.overlays.length, 0);
        env.callbacks.close(env.document);
        env.document.languageId = 'thor';
        env.callbacks.open(env.document);
        env.change(0, 3, '');
        t.mock.timers.tick(100);
        assert.equal(env.overlays.length, 1);
    } finally {env.dispose();}
});
