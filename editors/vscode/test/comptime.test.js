'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
test('compile-time policy is resource scoped and keeps normal as the default', () => {
    const manifest = require('../package.json');
    const setting = manifest.contributes.configuration.properties['tls.comptime'];
    assert.deepEqual(setting.enum, ['required','normal','aggressive','override']);
    assert.equal(setting.default, 'normal');
    assert.equal(setting.scope, 'resource');
    const source = fs.readFileSync(path.join(__dirname,'../extension.js'),'utf8');
    assert.match(source, /config\.get\('comptime', 'normal'\)/);
    assert.match(source, /comptime !== 'normal'/);
    assert.match(source, /--comptime=\$\{comptime\}/);
    assert.match(source, /initializationOptions\.checker_args,.*--comptime/);
});
test('all policies combine with vet without changing the default checker invocation', () => {
    const source = fs.readFileSync(path.join(__dirname,'../extension.js'),'utf8');
    const fragment = source.slice(source.indexOf("    if (config.get('vet', false))"),
        source.indexOf("    const compilerPath = config.get('compiler.path'"));
    assert.ok(fragment.includes('--comptime='));
    for (const comptime of ['required','normal','aggressive','override']) {
        for (const vet of [false,true]) {
            const options = vm.runInNewContext(`const initializationOptions = {}; ${fragment}; initializationOptions`, {
                config: { get: (key, fallback) => ({comptime,vet})[key] ?? fallback }
            });
            const expected = [vet ? '--vet' : '',comptime === 'normal' ? '' : `--comptime=${comptime}`]
                .filter(Boolean).join(' ');
            assert.equal(options.checker_args, expected || undefined);
        }
    }
});
