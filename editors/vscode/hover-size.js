'use strict';

// Keep the declaration and generated size line in one padded hover block.
// Source declarations stay fenced code; documentation keeps its own settings.
const SIZE_MARKER = '\n\n<!-- thor-hover-size -->\n';
const SIZE_TEXT = /^\d+ bytes?(?: · (?:pointer|slice header|string header|dynamic array header|map header|variable storage|target dependent))*$/;

function copyMarkdown(value, original, vscode) {
    const result = new vscode.MarkdownString(value);
    result.isTrusted = original.isTrusted;
    result.supportHtml = original.supportHtml;
    result.supportThemeIcons = original.supportThemeIcons;
    result.baseUri = original.baseUri;
    return result;
}

function formatHoverSize(hover, vscode) {
    if (!hover) return hover;
    let changed = false;
    const contents = [];
    for (const content of hover.contents) {
        if (!(content instanceof vscode.MarkdownString)) {
            contents.push(content);
            continue;
        }
        const index = content.value.indexOf(SIZE_MARKER);
        if (index < 0) {
            contents.push(content);
            continue;
        }
        const start = index + SIZE_MARKER.length;
        const newline = content.value.indexOf('\n', start);
        const end = newline < 0 ? content.value.length : newline;
        const detail = content.value.slice(start, end);
        const signature = content.value.slice(0, index);
        if (!SIZE_TEXT.test(detail) || !/^```(?:thor|odin)\n/.test(signature) || !signature.endsWith('\n```')) {
            contents.push(content);
            continue;
        }
        const opening = signature.indexOf('\n');
        const language = signature.slice(3, opening);
        const declaration = signature.slice(opening + 1, -4);
        // A longer fence keeps even backticks in source literals inside code
        // when HTML is enabled for the generated size line.
        const longest = Math.max(2, ...Array.from(declaration.matchAll(/`+/g), match => match[0].length));
        const fence = '`'.repeat(longest + 1);
        const formatted = copyMarkdown(
            '<div>\n\n' + fence + language + '\n' + declaration + '\n' + fence +
            '\n\n</div>\n<small><span style="color:var(--vscode-descriptionForeground);">' + detail + '</span></small>',
            content, vscode);
        formatted.isTrusted = false;
        formatted.supportHtml = true;
        contents.push(formatted);
        const documentation = content.value.slice(end);
        if (documentation.trim()) contents.push(copyMarkdown(documentation, content, vscode));
        changed = true;
    }
    return changed ? new vscode.Hover(contents, hover.range) : hover;
}

module.exports = { formatHoverSize };
