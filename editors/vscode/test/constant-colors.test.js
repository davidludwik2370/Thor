'use strict';
const assert = require('node:assert/strict');
const test = require('node:test');
const {RULE, PURPLE, snapshot, preview, restore, registerConstantColors} = require('../constant-colors');

test('preview restores the absence of an override and leaves the theme untouched', () => {
    assert.equal(restore(preview(undefined, PURPLE), snapshot(undefined)), undefined);
    for (const raw of [{}, {rules: {}}, {enabled: true}, {'[Dark Modern]': {rules: {'*.readonly': '#123456'}}}]) {
        assert.deepEqual(restore(preview(raw, PURPLE), snapshot(raw)), raw);
    }
});

test('only the compile-time Thor selector changes; old rules and later edits survive', () => {
    const raw = {enabled: true, rules: {'variable:thor': '#9CDCFE', 'variable.readonly:thor': '#4FC1FF',
        [RULE]: {foreground: '#112233', bold: true}}};
    const saved = structuredClone(raw), baseline = snapshot(raw), changed = preview(raw, PURPLE);
    assert.deepEqual(raw, saved, 'Preview mutated the original settings object');
    assert.equal(changed.rules['variable:thor'], raw.rules['variable:thor']);
    assert.equal(changed.rules['variable.readonly:thor'], raw.rules['variable.readonly:thor']);
    assert.deepEqual(changed.rules[RULE], {foreground: PURPLE, bold: true});
    changed.rules['function:thor'] = '#ffff00';
    const restored = restore(changed, baseline);
    assert.deepEqual(restored.rules[RULE], raw.rules[RULE]);
    assert.equal(restored.rules['function:thor'], '#ffff00');
});

test('invalid colors and malformed settings do not replace existing preferences', () => {
    for (const color of ['', 'purple', '#ABC', '#GGGGGG']) assert.throws(() => preview(undefined, color));
    for (const raw of [null, [], 'bad', {rules: []}]) assert.throws(() => preview(raw, PURPLE));
    assert.equal(preview(undefined, '#9b8fe8').rules[RULE], '#9b8fe8');
});

function environment(initial) {
    let value = structuredClone(initial);
    const state = new Map(), commands = new Map(), updates = [], errors = [];
    const context = {subscriptions: [], globalState: {
        get: (key, fallback) => state.has(key) ? state.get(key) : fallback,
        update: async (key, next) => next === undefined ? state.delete(key) : state.set(key, structuredClone(next))
    }};
    const config = {inspect: () => ({globalValue: value, workspaceValue: {rules: {'function': '#abcdef'}}}),
        update: async (key, next, target) => {updates.push({key, target}); value = structuredClone(next);}};
    const vscode = {ConfigurationTarget: {Global: 1},
        workspace: {getConfiguration: () => config},
        commands: {registerCommand: (name, callback) => {commands.set(name, callback); return {dispose() {}};}},
        window: {showErrorMessage: message => errors.push(message), showInputBox: async () => undefined}};
    registerConstantColors(context, vscode);
    return {context, state, commands, updates, errors, get value() {return value;}};
}

test('repeated previews preserve the first baseline and restore only the owned rule', async () => {
    const initial = {rules: {'variable.readonly:thor': '#4FC1FF'}};
    const env = environment(initial);
    await env.commands.get('thor.previewConstantColor')(PURPLE);
    await env.commands.get('thor.previewConstantColor')('#8877DD');
    env.value.rules['function:thor'] = '#eeeeaa';
    await env.commands.get('thor.restoreConstantColor')();
    assert.deepEqual(env.value, {rules: {...initial.rules, 'function:thor': '#eeeeaa'}});
    assert.ok(env.updates.every(update => update.target === 1));
    assert.deepEqual(env.errors, []);
});

test('cancel and restore without an active preview do not write settings', async () => {
    const env = environment(undefined);
    await env.commands.get('thor.previewConstantColor')();
    await env.commands.get('thor.restoreConstantColor')();
    assert.equal(env.updates.length, 0);
    assert.equal(env.state.size, 0);
});

test('baseline persists across extension reload and preserves an existing custom rule', async () => {
    const initial = {rules: {[RULE]: '#112233', 'namespace:thor': '#99ffff'}};
    const env = environment(initial);
    await env.commands.get('thor.previewConstantColor')(PURPLE);
    const commands = new Map();
    registerConstantColors({...env.context, subscriptions: []}, {
        ConfigurationTarget: {Global: 1},
        workspace: {getConfiguration: () => ({inspect: () => ({globalValue: env.value}), update: async (key, value) => {env.value.rules = value.rules;}})},
        commands: {registerCommand: (key, callback) => {commands.set(key, callback); return {dispose() {}};}},
        window: {showErrorMessage: message => assert.fail(message)}
    });
    await commands.get('thor.restoreConstantColor')();
    assert.deepEqual(env.value, initial);
});
