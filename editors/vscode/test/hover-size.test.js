'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { formatHoverSize } = require('../hover-size');

class MarkdownString {
    constructor(value = '') { this.value = value; }
}
class Hover {
    constructor(contents, range) { this.contents = contents; this.range = range; }
}
const vscode = { MarkdownString, Hover };
const marker = '\n\n<!-- thor-hover-size -->\n';
const signature = '```thor\nsandbox.value: int\n```';

test('compact hover sizes are enabled by default and resource scoped', () => {
    const setting = require('../package.json').contributes.configuration.properties['tls.hoverSize'];
    assert.equal(setting.type, 'boolean');
    assert.equal(setting.default, true);
    assert.equal(setting.scope, 'resource');
});

test('a muted second line shares the declaration padding and preserves docs and range', () => {
    const range = { start: 1, end: 4 };
    const docs = '\n---\nDocumentation with `code` and <b>literal HTML</b>.';
    const source = new MarkdownString(signature + marker + '8 bytes · target dependent' + docs);
    source.isTrusted = false;
    source.supportHtml = false;
    source.supportThemeIcons = true;
    source.baseUri = { scheme: 'file' };
    const original = new Hover([source], range);
    const result = formatHoverSize(original, vscode);
    assert.equal(result.range, range);
    assert.equal(result.contents.length, 2);
    assert.equal(result.contents[0].value,
        '<div>\n\n' + signature + '\n\n</div>\n' +
        '<small><span style="color:var(--vscode-descriptionForeground);">8 bytes · target dependent</span></small>');
    assert.equal(result.contents[0].supportHtml, true);
    assert.equal(result.contents[0].isTrusted, false);
    assert.equal(result.contents[0].supportThemeIcons, true);
    assert.equal(result.contents[0].baseUri, source.baseUri);
    assert.equal(result.contents[1].value, docs);
    assert.equal(result.contents[1].supportHtml, false);
    assert.equal(source.value, signature + marker + '8 bytes · target dependent' + docs);
});

test('all generated qualifier combinations remain compact without a separate docs row', () => {
    for (const detail of ['0 bytes', '4 bytes', '8 bytes · pointer · target dependent',
        '16 bytes · slice header · target dependent', '16 bytes · string header · target dependent',
        '32 bytes · map header · target dependent', '40 bytes · dynamic array header · target dependent',
        '48 bytes · variable storage · target dependent']) {
        const result = formatHoverSize(new Hover([new MarkdownString(signature + marker + detail)]), vscode);
        assert.equal(result.contents.length, 1);
        assert.ok(result.contents[0].value.includes(detail));
    }
});

test('missing, unrelated and malformed size content stays unchanged', () => {
    const empty = formatHoverSize(undefined, vscode);
    assert.equal(empty, undefined);
    for (const value of [signature, signature + marker + '<img src="https://example.com">',
        signature + marker + '-1 bytes', signature + marker + '8 bytes · unknown',
        'A documentation paragraph' + marker + '4 bytes']) {
        const original = new Hover([new MarkdownString(value)]);
        const result = formatHoverSize(original, vscode);
        assert.equal(result, original);
    }
});

test('other hover entries are preserved and metadata never enables HTML in source documentation', () => {
    const other = new MarkdownString('Some other hover information.');
    const original = new Hover(['plain text', new MarkdownString(signature + marker + '4 bytes'), other]);
    const result = formatHoverSize(original, vscode);
    assert.equal(result.contents[0], 'plain text');
    assert.equal(result.contents[2], other);
    assert.equal(result.contents[1].supportHtml, true);
    assert.equal(other.supportHtml, undefined);
});

test('source literals cannot escape the declaration fence into generated HTML', () => {
    const declaration = 'value :: proc(text := "```\\n<img src=x>\\n````", other := "<b>text</b>")';
    const source = new MarkdownString('```odin\n' + declaration + '\n```' + marker + '8 bytes · pointer · target dependent');
    const result = formatHoverSize(new Hover([source]), vscode);
    assert.equal(result.contents.length, 1);
    assert.ok(result.contents[0].value.startsWith('<div>\n\n`````odin\n' + declaration + '\n`````\n\n</div>\n'));
    assert.equal(result.contents[0].isTrusted, false);
});
