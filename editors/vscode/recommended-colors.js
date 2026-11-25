'use strict';

const STATE = 'thor.recommendedColors.v1';
const DARK = {'variable.thorCompileTime:thor':'#8582DE', 'label:thor':'#8582DE',
    'parameter.readonly:thor':'#4FC1FF', 'property.readonly:thor':'#4FC1FF'};
const LIGHT = {'variable.thorCompileTime:thor':'#6F42C1', 'label:thor':'#6F42C1',
    'parameter.readonly:thor':'#005FAD', 'property.readonly:thor':'#005FAD'};

function object(value) {
    if (value === undefined) return {};
    if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('Existing color settings were left unchanged: expected an object.');
    return value;
}
function matches(selector, target) {
    const pattern = /^(\*|\w+)((?:\.\w+)*)(?::(\w+))?$/;
    const a=pattern.exec(selector), b=pattern.exec(target);
    if (!a || !b) return selector === target;
    return (a[1] === '*' || a[1] === b[1]) && (!a[3] || a[3] === b[3])
        && a[2].split('.').filter(Boolean).every(m => b[2].split('.').includes(m));
}
function customized(config, target) {
    // Compile-time value tokens also carry readonly. Honor a user's broader
    // readonly style rather than hiding it with a more specific preset rule.
    if (target === 'variable.thorCompileTime:thor') target='variable.readonly.thorCompileTime:thor';
    const blocks=[config, ...Object.entries(config).filter(([k])=>k.startsWith('[')).map(([,v])=>v)];
    return blocks.some(block => Object.keys(object(object(block).rules)).some(key => matches(key,target)));
}
function preset(raw, owned, palette, enabled) {
    const config=object(raw), rules={...object(config.rules)}, next={};
    const externalRules={...rules};
    for (const [key,color] of Object.entries(owned || {})) if (externalRules[key]===color) delete externalRules[key];
    const external={...config,rules:externalRules};
    for (const [key,color] of Object.entries(owned || {})) {
        if (rules[key] !== color) continue; // A subsequent user edit owns this rule.
        if (enabled && !customized(external,key)) {rules[key]=palette[key]; next[key]=palette[key];}
        else delete rules[key];
    }
    if (enabled) for (const [key,color] of Object.entries(palette)) {
        if (Object.hasOwn(next,key) || customized(external,key)) continue;
        rules[key]=color;next[key]=color;
    }
    const result={...config};
    if (Object.keys(rules).length || Object.hasOwn(config,'rules')) result.rules=rules;
    else delete result.rules;
    return {value:Object.keys(result).length ? result : undefined, owned:next};
}

function registerRecommendedColors(context,vscode) {
    let queue=Promise.resolve();
    const reconcile=()=>{
        const work=queue.then(async()=>{
            const enabled=vscode.workspace.getConfiguration('thor').get('colors.recommended',false);
            const old=context.globalState.get(STATE,{});
            if (!enabled && !Object.keys(old).length) return;
            const editor=vscode.workspace.getConfiguration('editor');
            const raw=editor.inspect('semanticTokenColorCustomizations')?.globalValue;
            const kind=vscode.window.activeColorTheme?.kind;
            const palette=kind === vscode.ColorThemeKind.Light || kind === vscode.ColorThemeKind.HighContrastLight ? LIGHT : DARK;
            const result=preset(raw,old,palette,enabled);
            if (JSON.stringify(result.value) !== JSON.stringify(raw)) {
                await editor.update('semanticTokenColorCustomizations',result.value,vscode.ConfigurationTarget.Global);
            }
            await context.globalState.update(STATE,Object.keys(result.owned).length ? result.owned : undefined);
        });
        queue=work.catch(error=>vscode.window.showErrorMessage(`Thor colors: ${error.message}`));
        return queue;
    };
    for (const [command,enabled] of [['thor.applyRecommendedColors',true],['thor.restoreRecommendedColors',false]]) {
        context.subscriptions.push(vscode.commands.registerCommand(command,async()=>{
            await vscode.workspace.getConfiguration('thor').update('colors.recommended',enabled,vscode.ConfigurationTarget.Global);
            await reconcile();
        }));
    }
    context.subscriptions.push(vscode.workspace.onDidChangeConfiguration(event=>{
        if (event.affectsConfiguration('thor.colors.recommended') || event.affectsConfiguration('editor.semanticTokenColorCustomizations')) void reconcile();
    }));
    context.subscriptions.push(vscode.window.onDidChangeActiveColorTheme(()=>void reconcile()));
    void reconcile();
    return {reconcile};
}

module.exports={DARK,LIGHT,matches,preset,registerRecommendedColors};
