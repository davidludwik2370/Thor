'use strict';
const assert = require('node:assert/strict');
const test = require('node:test');
const path = require('node:path');
const {prepareCodeLLDB} = require('../debug-backend');
function fixture(platform = 'linux') {
    const state = {files: new Set(), activations: 0, polls: 0, time: 0, cancelled: false};
    state.backend = {extensionPath: '/extensions/universal', activate: async () => {state.activations++;}};
    state.ready = root => {state.files.add(path.join(root, 'platform.ok'));state.files.add(path.join(root, 'adapter', platform === 'win32' ? 'codelldb.exe' : 'codelldb'));};
    const vscode = {extensions: {getExtension: () => state.backend}, ProgressLocation: {Notification: 15},
        window: {withProgress: async (_options, action) => action({}, {get isCancellationRequested() {return state.cancelled;}})}};
    const options = {platform, exists: file => state.files.has(file), now: () => state.time,
        delay: async ms => {state.time += ms;state.polls++;await state.onPoll?.();}, timeout: 1000};
    return {state, vscode, options};
}
test('installed native CodeLLDB starts immediately on each host', async () => {
    for (const platform of ['linux', 'darwin', 'win32']) {
        const {state, vscode, options} = fixture(platform);state.ready(state.backend.extensionPath);
        const root = await prepareCodeLLDB(vscode, options);
        assert.equal(root, state.backend.extensionPath);assert.equal(state.activations, 0);assert.equal(state.polls, 0);
    }
});
test('first use waits for upstream native installation and a replaced extension location', async () => {
    const {state, vscode, options} = fixture();
    state.onPoll = () => {if (state.polls === 3) {state.backend = {extensionPath: '/extensions/native'};state.ready(state.backend.extensionPath);}};
    const root = await prepareCodeLLDB(vscode, options);
    assert.equal(root, '/extensions/native');assert.equal(state.activations, 1);assert.equal(state.polls, 3);
});
test('a partial download cannot start the native adapter', async () => {
    const {state, vscode, options} = fixture();state.files.add(path.join(state.backend.extensionPath, 'platform.ok'));
    await assert.rejects(prepareCodeLLDB(vscode, options), /CodeLLDB Output channel/);
    assert.equal(state.polls, 5);
});
test('missing and failed CodeLLDB activation produce actionable errors', async () => {
    const {state, vscode, options} = fixture();state.backend = undefined;
    await assert.rejects(prepareCodeLLDB(vscode, options), /Install or enable the CodeLLDB extension/);
    state.backend = {extensionPath: '/extensions/broken', activate: async () => {throw Error('upstream activation failed');}};
    await assert.rejects(prepareCodeLLDB(vscode, options), /upstream activation failed/);
});
test('cancelling first-use preparation does not start a debugger', async () => {
    const {state, vscode, options} = fixture();state.onPoll = () => {state.cancelled = true;};
    await assert.rejects(prepareCodeLLDB(vscode, options), /cancelled/);assert.equal(state.polls, 1);
});
