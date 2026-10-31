'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.resolve(__dirname, '..');
const manifest = require('../package.json');

test('Thor contributes transparent yellow file glyphs for dark and light themes', () => {
    const language = manifest.contributes.languages.find(value => value.id === 'thor');
    assert.deepEqual(language.extensions, ['.thor']);
    for (const [theme, color] of [['dark', '#fed818'], ['light', '#937a00']]) {
        const svg = fs.readFileSync(path.join(root, language.icon[theme]), 'utf8');
        assert.match(svg, /viewBox="0 0 150 150"/);
        assert.match(svg, new RegExp(`fill="${color}"`));
        assert.equal((svg.match(/<path\b/g) || []).length, 1);
        assert.doesNotMatch(svg, /<(?:rect|image|foreignObject|style)\b/,
            'File icons must contain only the glyph; the editor background shows through');
        assert.doesNotMatch(svg, /(?:href\s*=\s*["'](?:https?:|data:)|url\()/,
            'File glyphs must render offline without external images or background fills');
    }
    assert.equal(manifest.contributes.iconThemes, undefined,
        'A language icon must not replace the user’s icon theme');
});

test('The extension listing uses a supported square PNG emblem', () => {
    const png = fs.readFileSync(path.join(root, manifest.icon));
    assert.deepEqual([...png.subarray(0, 8)], [137, 80, 78, 71, 13, 10, 26, 10]);
    assert.equal(png.readUInt32BE(16), 256);
    assert.equal(png.readUInt32BE(20), 256);
});
