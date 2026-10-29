'use strict';

const RULE = 'variable.thorCompileTime:thor';
const PURPLE = '#9B8FE8';
const BACKUP = 'thor.constantColorPreview.baseline.v1';
const LAST_COLOR = 'thor.constantColorPreview.lastColor';
const own = (value, key) => Object.prototype.hasOwnProperty.call(value, key);

function object(value) {
    if (value === undefined) return {};
    if (value === null || typeof value !== 'object' || Array.isArray(value)) {
        throw new Error('Semantic color settings must be an object; existing settings were left unchanged.');
    }
    return value;
}

function snapshot(raw) {
    const config = object(raw), rules = object(config.rules);
    return {configPresent: raw !== undefined, rulesPresent: own(config, 'rules'),
        rulePresent: own(rules, RULE), rule: rules[RULE]};
}

function preview(raw, color) {
    if (typeof color !== 'string' || !/^#[0-9a-f]{6}([0-9a-f]{2})?$/i.test(color)) throw new Error('Use a color such as #9B8FE8.');
    const config = object(raw);
    const rules = object(config.rules), previous = rules[RULE];
    const style = previous && typeof previous === 'object' ? {...previous, foreground: color} : color;
    return {...config, rules: {...rules, [RULE]: style}};
}

function restore(raw, baseline) {
    const config = {...object(raw)}, rules = {...object(config.rules)};
    if (baseline.rulePresent) rules[RULE] = baseline.rule;
    else delete rules[RULE];
    if (Object.keys(rules).length || baseline.rulesPresent) config.rules = rules;
    else delete config.rules;
    return Object.keys(config).length || baseline.configPresent ? config : undefined;
}

function registerConstantColors(context, vscode) {
    // Only this new Thor-specific rule is changed. Inherited theme rules and
    // later edits to unrelated user/workspace color settings stay intact.
    const current = () => vscode.workspace.getConfiguration('editor');
    const raw = config => config.inspect('semanticTokenColorCustomizations')?.globalValue;
    let queue = Promise.resolve();
    const action = callback => (...args) => {
        const next = queue.then(() => callback(...args));
        queue = next.catch(() => {});
        return next.catch(error => vscode.window.showErrorMessage(`Thor constant colors: ${error.message}`));
    };
    context.subscriptions.push(vscode.commands.registerCommand('thor.previewConstantColor', action(async color => {
        if (color === undefined) {
            color = await vscode.window.showInputBox({title: 'Preview Thor compile-time constants',
                prompt: 'Only compile-time value names change. Restore the previous color with Thor: Restore Previous Constant Color.',
                value: context.globalState.get(LAST_COLOR, PURPLE),
                validateInput: value => /^#[0-9a-f]{6}([0-9a-f]{2})?$/i.test(value) ? undefined : 'Use #RRGGBB or #RRGGBBAA.'});
        }
        if (color === undefined) return;
        const config = current(), value = raw(config), updated = preview(value, color);
        if (context.globalState.get(BACKUP) === undefined) {
            await context.globalState.update(BACKUP, snapshot(value));
        }
        await config.update('semanticTokenColorCustomizations', updated, vscode.ConfigurationTarget.Global);
        await context.globalState.update(LAST_COLOR, color);
    })));
    context.subscriptions.push(vscode.commands.registerCommand('thor.restoreConstantColor', action(async () => {
        const baseline = context.globalState.get(BACKUP);
        if (baseline === undefined) return;
        const config = current();
        await config.update('semanticTokenColorCustomizations', restore(raw(config), baseline), vscode.ConfigurationTarget.Global);
        await context.globalState.update(BACKUP, undefined);
    })));
}

module.exports = {RULE, PURPLE, snapshot, preview, restore, registerConstantColors};
