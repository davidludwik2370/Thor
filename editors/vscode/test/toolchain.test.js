'use strict';
const assert = require('node:assert/strict');
const path = require('node:path');
const test = require('node:test');
const {findToolchain} = require('../toolchain');

function installed(...roots) {
    return file => roots.some(root => [path.join(root, 'thor'), path.join(root, 'tls')].includes(file));
}

test('ordinary projects use the shared Thor installation', () => {
    const root = path.resolve('/home/user/Thor');
    assert.equal(findToolchain('/projects/app', {env: {}, home: '/home/user', exists: installed(root)}), root);
});
test('explicit installation and development checkouts remain selectable', () => {
    const home = path.resolve('/home/user/Thor'), checkout = path.resolve('/projects/thor'), custom = path.resolve('/tools/thor');
    const exists = installed(home, checkout, custom);
    assert.equal(findToolchain(checkout, {env: {}, home: '/home/user', exists}), checkout);
    assert.equal(findToolchain(checkout, {env: {THOR_ROOT: custom}, home: '/home/user', exists}), custom);
});
test('missing installations are reported rather than invented', () => {
    assert.equal(findToolchain('/projects/app', {env: {}, home: '/home/user', exists: () => false}), undefined);
});
