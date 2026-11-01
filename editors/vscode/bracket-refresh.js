'use strict';

// TextMate owns comment scopes; TLS supplies identifier tokens only. VS Code
// separately caches a bracket tree. Notify the language-configuration service
// after comment delimiters change so that tree is rebuilt from current tokens.
// An empty overlay preserves the grammar's bracket/comment/indentation rules.
function changesCommentDelimiter(previous, changes) {
    const markers = text => (text.match(/\/\/|\/\*|\*\//g) || []).join(',');
    return changes.some(change => {
        const start = change.rangeOffset, end = start + change.rangeLength;
        const removed = previous.slice(start, end);
        if (/\/\/|\/\*|\*\//.test(removed + '\n' + change.text)) return true;
        // Also catch typing/deleting one slash or star of a delimiter.
        const left = previous.slice(Math.max(0, start - 1), start);
        const right = previous.slice(end, end + 1);
        return markers(left + removed + right) !== markers(left + change.text + right);
    });
}

function registerBracketRefresh(context, vscode) {
    const sources = new Map();
    const remember = document => {
        if (document.languageId === 'thor') sources.set(document.uri.toString(), document.getText());
    };
    vscode.workspace.textDocuments.forEach(remember);
    let timer, overlay;
    const refresh = () => {
        clearTimeout(timer);
        timer = undefined;
        const previous = overlay;
        overlay = vscode.languages.setLanguageConfiguration('thor', {});
        previous?.dispose();
    };
    context.subscriptions.push(
        vscode.commands.registerCommand('thor.refreshBracketHighlighting', refresh),
        vscode.workspace.onDidOpenTextDocument(remember),
        vscode.workspace.onDidCloseTextDocument(document => sources.delete(document.uri.toString())),
        vscode.workspace.onDidChangeTextDocument(event => {
            if (event.document.languageId !== 'thor') return;
            const key = event.document.uri.toString(), previous = sources.get(key);
            remember(event.document);
            if (previous === undefined || !changesCommentDelimiter(previous, event.contentChanges)) return;
            clearTimeout(timer);
            timer = setTimeout(refresh, 100);
        }),
        { dispose() { clearTimeout(timer); overlay?.dispose(); sources.clear(); } },
    );
}

module.exports = { changesCommentDelimiter, registerBracketRefresh };
