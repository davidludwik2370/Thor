'use strict';

// A DAP bridge, not an LLDB implementation. CodeLLDB retains native process,
// breakpoint, value and thread handling. This layer owns Thor source locations.
const fs = require('node:fs');
const path = require('node:path');
const net = require('node:net');
const cp = require('node:child_process');
const crypto = require('node:crypto');

function canonical(file) {
    try { return fs.realpathSync(file); } catch { return path.resolve(file); }
}
function lineStarts(bytes) {
    const starts = [0];
    for (let i = 0; i < bytes.length; i++) if (bytes[i] === 10) starts.push(i + 1);
    return starts;
}
function floorIndex(values, offset) {
    let low = 0, high = values.length;
    while (low < high) {
        const mid = (low + high) >>> 1;
        if (values[mid] <= offset) low = mid + 1; else high = mid;
    }
    return Math.max(0, low - 1);
}
function position(unit, offset, native = false) {
    const row = floorIndex(unit.lines, offset);
    const prefix=unit.bytes.subarray(unit.lines[row], offset).toString('utf8');
    return {line: row + 1, column: (native ? [...prefix].length : prefix.length) + 1};
}
function byteOffset(unit, line, column = 1, native = false) {
    const start = unit.lines[Math.max(0, Math.min(line - 1, unit.lines.length - 1))];
    const end = unit.bytes.indexOf(10, start);
    const text = unit.bytes.subarray(start, end < 0 ? undefined : end).toString('utf8');
    const prefix=native ? [...text].slice(0,Math.max(0,column-1)).join('') : text.slice(0, Math.max(0, column - 1));
    return start + Buffer.byteLength(prefix);
}
const generatedKinds = new Set(['expression-helper', 'support', 'import', 'pure-contract',
    'pure-type-validator', 'pure-procedure', 'membership-helper', 'switch-validation', 'immutable', 'run-helper', 'debug-capture', 'memory-debug']);

function typeQueryRanges(bytes) {
    // Generated type witnesses repeat source expressions without executing
    // them. Their byte ranges must never win over an arm's real runtime code.
    const code = bytes.toString('latin1'); // byte-preserving offsets
    const ranges = [], stack = [];
    let previous = '';
    for (let i = 0; i < code.length;) {
        if (code.startsWith('//', i)) { i = code.indexOf('\n', i); if (i < 0) break; continue; }
        if (code.startsWith('/*', i)) {
            i += 2; let depth = 1;
            while (i < code.length && depth) {
                if (code.startsWith('/*', i)) { depth++; i += 2; }
                else if (code.startsWith('*/', i)) { depth--; i += 2; } else i++;
            }
            continue;
        }
        if ('"\'`'.includes(code[i])) {
            const quote = code[i++];
            while (i < code.length) {
                if (code[i] === '\\' && quote !== '`') { i += 2; continue; }
                if (code[i++] === quote) break;
            }
            previous = 'literal'; continue;
        }
        const word = /^[A-Za-z_][A-Za-z_0-9]*/.exec(code.slice(i));
        if (word) { previous = word[0]; i += word[0].length; continue; }
        if (code[i] === '(') stack.push({start: i, query: ['type_of','size_of','align_of','offset_of'].includes(previous)});
        if (code[i] === ')' && stack.length) {
            const opened = stack.pop();
            if (opened.query) ranges.push([opened.start, i + 1]);
        }
        if (!/\s/.test(code[i])) previous = code[i];
        i++;
    }
    return ranges;
}

class DebugMaps {
    constructor(manifest) {
        this.files = new Map();
        this.originals = new Map();
        const directory = path.dirname(manifest);
        const listing = JSON.parse(fs.readFileSync(manifest, 'utf8'));
        if (listing.debug_map_version!==1 || !listing.odin_flags.includes('-debug')) {
            throw new Error('Rebuild with Thor 0.10 or newer using -debug -o:none before starting the Thor debugger.');
        }
        this.debugRoots = (listing.debug_roots || []).map(canonical);
        this.program=listing.debug_program;
        const mapFiles = [...listing.generated.filter(name => name.endsWith('.odin.map.json')), ...(listing.shared_maps || [])];
        for (const name of mapFiles) {
            const map = JSON.parse(fs.readFileSync(path.isAbsolute(name) ? name : path.join(directory, name), 'utf8'));
            if (map.version !== 1) throw new Error(`Unsupported Thor source-map version: ${map.version}`);
            const file = canonical(map.generated);
            const bytes = fs.readFileSync(file);
            if (map.generated_sha256!==crypto.createHash('sha256').update(bytes).digest('hex')) {
                throw new Error(`Generated source changed or maps are outdated: ${file}. Rebuild before debugging.`);
            }
            const unit = {file, bytes, lines: lineStarts(bytes), map, starts: map.segments.map(s => s[0]), queries: typeQueryRanges(bytes)};
            this.files.set(file, unit);
            for (const [original, text] of Object.entries(map.sources)) {
                const key = canonical(original), previous = this.originals.get(key);
                const contents = Buffer.from(text);
                if (previous && !previous.bytes.equals(contents)) throw new Error(`Inconsistent source snapshots: ${original}`);
                this.originals.set(key, {file: original, bytes: contents, lines: lineStarts(contents)});
                if (!fs.readFileSync(original).equals(contents)) {
                    throw new Error(`Source changed since the debug build: ${original}. Rebuild before debugging.`);
                }
            }
        }
    }
    unit(file) { return file && this.files.get(canonical(file)); }
    verifyExecutable(program) {
        if (!this.program || crypto.createHash('sha256').update(fs.readFileSync(program)).digest('hex')!==this.program.sha256) {
            throw new Error('The executable does not match this Thor debug build. Rebuild before debugging.');
        }
    }
    location(file, line, column = 1) {
        const unit = this.unit(file);
        if (!unit) return {source: {name: path.basename(file || ''), path: file}, line, column, internal: false};
        // Odin's DWARF columns count Unicode scalars; VS Code uses UTF-16.
        let offset = byteOffset(unit, line, column, true);
        let segment = unit.map.segments[floorIndex(unit.starts, offset)];
        if (!segment) return undefined;
        // LLDB commonly reports column 1 for a line indented by synthetic code.
        // Prefer the first actual token on that line over its leading whitespace.
        if (column <= 1) {
            const end = unit.bytes.indexOf(10, offset);
            const leading = unit.bytes.subarray(offset, end < 0 ? undefined : end).toString().match(/^\s*/)[0];
            offset += Buffer.byteLength(leading);
            segment = unit.map.segments[floorIndex(unit.starts, offset)];
        }
        const helper = (unit.map.regions || []).some(([a, b, kind]) => kind === 'expression-helper' && a <= offset && offset < b);
        const capture = (unit.map.regions || []).find(([a,b,kind]) => kind === 'debug-capture' && a <= offset && offset < b);
        const procedure = (unit.map.regions || []).find(([a,b,kind]) => kind === 'pure-procedure' && a <= offset && offset < b);
        if (capture) segment = [capture[0],capture[1],capture[3],capture[4],capture[5],'debug-capture',false];
        if (helper && !segment[6] && ['SwitchExpr', 'CompileSwitchExpr', 'expression-helper'].includes(segment[5])) {
            // A generated `return` belongs to the whole switch, but the code
            // following it belongs to its selected result arm. Odin may assign
            // the return's location to the first instruction of that arm.
            const first = unit.lines[line - 1], last = unit.lines[line] ?? unit.bytes.length;
            const inner = unit.map.segments.find(s => s[0] >= first && s[0] < last &&
                !generatedKinds.has(s[5]) && s[3] > segment[3] &&
                (s[6] ? /\S/.test(unit.bytes.subarray(s[0], s[1]).toString()) : ['Ident','Literal','Binary','Call','Index'].includes(s[5])));
            if (inner) { segment = inner; offset = inner[0]; }
        }
        const [a, b, original, start, end, kind, exact] = segment;
        const originalUnit = this.originals.get(canonical(original));
        if (!originalUnit) return undefined;
        const originalOffset = exact ? Math.min(end, start + Math.max(0, Math.min(offset, b) - a)) : start;
        const p = position(originalUnit, originalOffset);
        const sourceLine = originalUnit.bytes.subarray(originalUnit.lines[p.line - 1],
            originalUnit.lines[p.line] ?? originalUnit.bytes.length).toString().trim();
        const generatedLine=unit.bytes.subarray(unit.lines[line-1],unit.lines[line] ?? unit.bytes.length).toString().trim();
        return {source: {name: path.basename(original), path: original}, ...p,
            internal: generatedKinds.has(kind) || !sourceLine || /^[{};]+$/.test(sourceLine) || sourceLine.startsWith('//')
                || /^[{};]+$/.test(generatedLine) || kind==='Block' && /__thor_[a-f0-9]{12}_/.test(generatedLine),
            helper, kind, procedure: procedure?.[6], checkedVariant: procedure?.[7]};
    }
    candidates(file, line, column) {
        const original = this.originals.get(canonical(file));
        if (!original || line < 1 || line > original.lines.length) return [];
        const first = original.lines[line - 1], last = original.lines[line] ?? original.bytes.length;
        const requested = column ? byteOffset(original, line, column) : undefined;
        const found = new Map();
        for (const unit of this.files.values()) {
            for (const [a, b, source, start, end, kind, exact] of unit.map.segments) {
                if (canonical(source) !== canonical(file) || generatedKinds.has(kind) || end <= first || start >= last) continue;
                if (!exact && position(original, start).line !== line) continue;
                if (requested !== undefined && (requested < start || requested >= end)) continue;
                const offset = exact ? a + Math.max(first, start, requested ?? start) - start : a;
                if (offset >= b) continue;
                if (!/\S/.test(unit.bytes.subarray(a,b).toString()) || unit.queries.some(([a,b]) => a <= offset && offset < b)) continue;
                if ((unit.map.regions || []).some(([a,b,kind]) => kind === 'debug-capture' && a <= offset && offset < b)) continue;
                const p = position(unit, offset, true);
                const lineText = unit.bytes.subarray(unit.lines[p.line - 1], unit.lines[p.line] ?? unit.bytes.length).toString().trim();
                if (!lineText || lineText.startsWith('//') || /^[{};]+$/.test(lineText)) continue;
                const helper = (unit.map.regions || []).some(([a, b, kind]) => kind === 'expression-helper' && a <= offset && offset < b);
                const key = `${unit.file}:${p.line}`;
                const candidate = {file: unit.file, ...p, helper, exact};
                const old = found.get(key);
                if (!old || candidate.column < old.column) found.set(key, candidate);
            }
        }
        const result = [...found.values()];
        const ordinary = result.filter(p => !p.helper);
        return ordinary.length ? ordinary : result;
    }
    condition(file,line,expression) {
        if (!expression) return expression;
        const unit=this.unit(file),offset=byteOffset(unit,line);
        const region=(unit.map.regions || []).filter(([a,b,kind]) => kind==='expression-helper' && a<=offset && offset<b)
            .sort((a,b)=>(a[1]-a[0])-(b[1]-b[0]))[0];
        if (!region) return expression;
        const aliases=new Map();
        for (const [a,b,source,start,end,kind] of unit.map.segments) {
            if (a<region[0] || b>region[1] || kind!=='Ident') continue;
            const original=this.originals.get(canonical(source)).bytes.subarray(start,end).toString();
            const alias=/^\((__thor_[a-f0-9]+_capture_\d+)(\^?)\)$/.exec(unit.bytes.subarray(a,b).toString());
            if (alias && /^[A-Za-z_]\w*$/.test(original)) aliases.set(original,alias[2] ? `(*${alias[1]})` : alias[1]);
        }
        return expression.replace(/"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|\b[A-Za-z_]\w*\b/g,
            (token,offset) => /^["']/.test(token) || expression[offset-1]==='.' ? token : aliases.get(token) || token);
    }
}

class Framing {
    constructor(accept,authToken) { this.buffer = Buffer.alloc(0); this.accept = accept; this.authToken=authToken; }
    feed(chunk) {
        this.buffer = Buffer.concat([this.buffer, chunk]);
        while (true) {
            const end = this.buffer.indexOf('\r\n\r\n');
            if (end < 0) { if (this.buffer.length>16384) throw new Error('Invalid DAP header'); return; }
            const header=this.buffer.subarray(0,end).toString();
            if (this.authToken && !this.authenticated) {
                if (/^Auth-Token:\s*(\S+)/im.exec(header)?.[1]!==this.authToken) throw new Error('CodeLLDB authentication failed');
                this.authenticated=true;
            }
            const length = /^Content-Length:\s*(\d+)/im.exec(header);
            if (!length || Number(length[1]) > 64 * 1024 * 1024) throw new Error('Invalid DAP message length');
            const after = end + 4 + Number(length[1]);
            if (this.buffer.length < after) return;
            const message = JSON.parse(this.buffer.subarray(end + 4, after).toString());
            this.buffer = this.buffer.subarray(after);
            this.accept(message);
        }
    }
    static encode(message) {
        const data = Buffer.from(JSON.stringify(message));
        return Buffer.concat([Buffer.from(`Content-Length: ${data.length}\r\n\r\n`), data]);
    }
}

class ThorDebugAdapter {
    constructor(options) {
        this.options = options;
        this.listeners = new Set();
        this.onDidSendMessage = listener => { this.listeners.add(listener); return {dispose: () => this.listeners.delete(listener)}; };
        this.sequence = 0;
        this.backendSequence = 0;
        this.pending = new Map();
        this.reverse = new Map();
        this.breakpoints = new Map();
        this.sourceAliases = new Map();
        this.breakpointIDs = new Map();
        this.frames = new Map();
        this.references = new Map();
        this.reference = 0x40000000;
        this.breakpointID = 0x20000000;
        this.exited = false;
    }
    emit(message) {
        message = {...message, seq: ++this.sequence};
        for (const listener of this.listeners) listener(message);
        return message.seq;
    }
    async start() {
        if (this.starting) return this.starting;
        this.starting = new Promise((resolve, reject) => {
            const server = net.createServer(socket => {
                this.socket = socket;
                server.close();
                clearTimeout(timer);
                const framing = new Framing(message => this.receive(message),this.authToken);
                socket.on('data', chunk => {
                    try { framing.feed(chunk); } catch (error) { this.fail(error); }
                });
                socket.on('error', error => this.fail(error));
                socket.on('close', () => this.fail(new Error('CodeLLDB connection closed')));
                resolve();
            });
            this.server = server;
            const timer = setTimeout(() => { server.close(); reject(new Error('CodeLLDB startup timed out')); this.dispose(); }, 15000);
            server.on('error', error => { clearTimeout(timer); reject(error); });
            this.authToken=crypto.randomBytes(32).toString('base64');
            server.listen(0, '127.0.0.1', () => {
                const root = this.options.backend;
                const executable = path.join(root, 'adapter', process.platform === 'win32' ? 'codelldb.exe' : 'codelldb');
                const lib = process.platform === 'darwin' ? 'lib/liblldb.dylib' : process.platform === 'win32' ? 'bin/liblldb.dll' : 'lib/liblldb.so';
                const env = {...process.env};
                delete env.PYTHONHOME; delete env.PYTHONPATH; delete env.CODELLDB_STARTUP;
                this.child = cp.spawn(executable, ['--liblldb', this.options.library || path.join(root, 'lldb', lib), '--connect', String(server.address().port), '--auth-token', this.authToken],
                    {cwd: this.options.cwd, env, stdio: ['ignore', 'pipe', 'pipe']});
                this.child.on('error', error => { clearTimeout(timer); server.close(); reject(error); });
                this.child.on('exit', () => { clearTimeout(timer); server.close(); this.fail(new Error('CodeLLDB exited')); });
                for (const stream of [this.child.stdout, this.child.stderr]) stream.on('data', data => {
                    this.emit({type: 'event', event: 'output', body: {category: 'console', output: data.toString()}});
                });
            });
        });
        return this.starting;
    }
    fail(error) {
        for (const {reject} of this.pending.values()) reject(error);
        this.pending.clear();
        if (!this.exited) { this.exited = true; this.emit({type: 'event', event: 'terminated'}); }
    }
    request(command, args = {}) {
        return new Promise((resolve, reject) => {
            const seq = ++this.backendSequence;
            this.pending.set(seq, {resolve, reject});
            this.socket.write(Framing.encode({seq, type: 'request', command, arguments: args}));
        });
    }
    receive(message) {
        if (message.type === 'response') {
            const pending = this.pending.get(message.request_seq);
            this.pending.delete(message.request_seq);
            if (!pending) return;
            if (message.success) pending.resolve(message.body || {});
            else pending.reject(Object.assign(new Error(message.message || 'CodeLLDB request failed'), {body: message.body}));
        } else if (message.type === 'request') {
            const seq = this.emit(message);
            this.reverse.set(seq, message.seq);
        } else if (message.event === 'stopped') {
            this.stopped(message).catch(error => { this.step = undefined; this.emit({type: 'event', event: 'output', body: {category: 'stderr', output: error.message + '\n'}}); this.emit(message); });
        } else if (message.event === 'continued') {
            if (!this.step) this.emit(message);
        } else if (message.event === 'breakpoint') {
            if (!this.breakpointIDs.has(message.body?.breakpoint?.id)) this.emit(message);
        } else {
            if (message.event === 'terminated') this.exited = true;
            this.emit(message);
        }
    }
    async handleMessage(message) {
        if (message.type === 'response') {
            const request_seq = this.reverse.get(message.request_seq);
            this.reverse.delete(message.request_seq);
            if (request_seq !== undefined) this.socket.write(Framing.encode({...message, seq: ++this.backendSequence, request_seq}));
            return;
        }
        if (message.type !== 'request') return;
        try {
            const body = await this.handle(message.command, message.arguments || {});
            this.emit({type: 'response', command: message.command, request_seq: message.seq, success: true, body});
        } catch (error) {
            this.emit({type: 'response', command: message.command, request_seq: message.seq, success: false, message: error.message, body: error.body});
        }
    }
    async handle(command, args) {
        if (command === 'initialize') {
            if (args.linesStartAt1 === false || args.columnsStartAt1 === false || args.pathFormat === 'uri') throw new Error('Thor debugging requires one-based file-path DAP coordinates.');
            await this.start();
            const body = await this.request(command, args);
            return {...body, supportsStepInTargetsRequest: false, supportsGotoTargetsRequest: false,
                supportsRestartFrame: false, supportsBreakpointLocationsRequest: false, supportsCancelRequest: false,
                supportsRestartRequest: false, supportsDataBreakpoints: false, supportsDataBreakpointBytes: false};
        }
        if (command === 'launch') {
            this.maps = new DebugMaps(args.manifest);
            this.maps.verifyExecutable(args.program);
            this.cwd = args.cwd || this.options.cwd;
            this.manifest = args.manifest;
            const {manifest, type, preLaunchTask, name, ...native} = args;
            return this.request(command, {...native, terminal: native.terminal || 'console'});
        }
        if (command === 'setBreakpoints') return this.setBreakpoints(args);
        if (command === 'stackTrace') {
            const body = await this.request(command, {...args, startFrame: 0, levels: undefined});
            const frames = this.mapFrames(body.stackFrames || []);
            return {stackFrames: frames.slice(args.startFrame || 0, args.levels ? (args.startFrame || 0) + args.levels : undefined), totalFrames: frames.length};
        }
        if (command === 'scopes') return this.scopes(args.frameId);
        if (command === 'variables' && this.references.has(args.variablesReference)) return this.variables(args);
        if (command === 'variables') {
            const body = await this.request(command,args);
            return {...body,variables: (body.variables || []).filter(v => !/__thor_[a-f0-9]{12}_/.test(v.name))};
        }
        if (command === 'setVariable' && this.references.has(args.variablesReference)) {
            for (const reference of this.references.get(args.variablesReference)) {
                const values = await this.request('variables',{variablesReference:reference});
                if (values.variables.some(v => v.name === args.name)) return this.request(command,{...args,variablesReference:reference});
            }
            throw new Error(`Variable '${args.name}' is unavailable in this scope.`);
        }
        if (command === 'evaluate') {
            const frame = this.frames.get(args.frameId);
            try { return await this.request(command, args); }
            catch (error) {
                if (!frame?.base || frame.base.id === args.frameId) throw error;
                return this.request(command, {...args, frameId: frame.base.id});
            }
        }
        if (['next', 'stepIn', 'stepOut'].includes(command)) {
            if (args.granularity === 'instruction') { this.step=undefined; return this.request(command,args); }
            const body = await this.request('stackTrace', {threadId: args.threadId, startFrame: 0});
            const visible = this.mapFrames(body.stackFrames || []);
            const detail = visible[0] && this.frames.get(visible[0].id);
            const base = detail?.generated && detail.base;
            this.step = {command, args, start: visible[0], count: 0,
                ownerPoint: base?.source?.path && this.maps.location(base.source.path,base.line,base.column)};
        }
        if (command === 'continue' || command === 'pause') this.step = undefined;
        if (command === 'disconnect') {
            this.disconnecting=true;
            try { return await this.request(command, args); } finally { this.dispose(); }
        }
        return this.request(command, args);
    }
    async setBreakpoints(args) {
        const file = args.source.path;
        this.sourceAliases.set(canonical(file),file);
        const original = this.maps.originals.get(canonical(file));
        if (!original) return this.request('setBreakpoints', args);
        const records = (args.breakpoints || []).map(bp => ({...bp, source: file, id: ++this.breakpointID,
            candidates: this.maps.candidates(file, bp.line, bp.column), actual: []}));
        const old = this.breakpoints.get(canonical(file)) || [];
        this.breakpoints.set(canonical(file), records);
        const affected = new Set([...old, ...records].flatMap(record => record.candidates.map(p => p.file)));
        for (const generated of affected) {
            let entries = [...this.breakpoints.values()].flatMap(records => records.flatMap(record =>
                record.candidates.filter(p => p.file === generated).map(p => ({record, point: p}))));
            const configure = entries => this.request('setBreakpoints', {source: {path: generated}, breakpoints: entries.map(({record, point}) => ({
                line: point.line, column: record.column ? point.column : undefined,
                condition: this.maps.condition(generated,point.line,record.condition),
                hitCondition: record.hitCondition, logMessage: record.logMessage?.replace(/\{([^{}]+)\}/g,
                    (_,expression)=>'{'+this.maps.condition(generated,point.line,expression)+'}')}))});
            let body = await configure(entries);
            const rejected = entries.filter(({record,point},i) => {
                const bp = body.breakpoints[i];
                if (!bp.verified || !bp.line) return false;
                const actual = this.maps.location(bp.source?.path || generated,bp.line,bp.column);
                return actual && (actual.line !== record.line || canonical(actual.source.path) !== canonical(record.source));
            });
            if (rejected.length) {
                for (const {record,point} of rejected) record.candidates = record.candidates.filter(p => p !== point);
                entries = entries.filter(entry => !rejected.includes(entry));
                // Remove breakpoints LLDB moved onto a different statement.
                // A compile-time-only declaration must never stop in main.
                body = await configure(entries);
            }
            entries.forEach(({record}, i) => {
                const bp = body.breakpoints[i];
                if (bp.id !== undefined) this.breakpointIDs.set(bp.id, record.id);
                record.actual.push(bp);
            });
        }
        return {breakpoints: records.map(record => {
            const bp = record.actual.find(bp => bp.verified);
            return {id: record.id, verified: !!bp, source: args.source, line: record.line, column: record.column,
                message: bp ? undefined : record.actual.map(bp => bp.message).filter(Boolean).join('; ') ||
                    'No executable runtime location on this Thor line (comments, inactive branches and compile-time-only code cannot stop at runtime).'};
        })};
    }
    mapFrames(raw) {
        const result = [];
        const checked = (frame, point) => point?.checkedVariant &&
            frame.name.split('(')[0].replace(/:proc$/, '').trim().endsWith(point.checkedVariant);
        const generatedFrame = frame => {
            const point = frame.source?.path && this.maps?.location(frame.source.path, frame.line, frame.column);
            return /__thor_[a-f0-9]+_/.test(frame.name) && !checked(frame, point);
        };
        for (let i = 0; i < raw.length; i++) {
            const frame = raw[i];
            const point = frame.source?.path && this.maps?.location(frame.source.path, frame.line, frame.column);
            const generated = /__thor_[a-f0-9]+_/.test(frame.name) && !checked(frame, point);
            if (generated && i > 0) continue;
            const base = generated ? raw.slice(i + 1).find(f => !generatedFrame(f)) : frame;
            const basePoint = base?.source?.path && this.maps?.location(base.source.path, base.line, base.column);
            const mapped = {...frame, ...(point ? {source: this.presentSource(point.source), line: point.line, column: point.column} : {}),
                name: checked(frame, point) ? point.procedure : generated && base ? (basePoint?.procedure || base.name) : frame.name};
            this.frames.set(frame.id, {raw: frame, base, point, generated});
            if (generated && base) {
                // Keep the current frame's id/locals while presenting one user
                // frame for the expression helper and its owning procedure.
                const baseIndex = raw.indexOf(base);
                i = baseIndex;
            }
            result.push(mapped);
        }
        return result;
    }
    presentSource(source) {
        if (!source?.path) return source;
        const file = canonical(source.path);
        let alias = this.sourceAliases.get(file);
        if (!alias && this.cwd && file.startsWith(canonical(this.cwd)+path.sep)) {
            alias = path.join(this.cwd,path.relative(canonical(this.cwd),file));
        }
        return alias ? {...source,path:alias,name:path.basename(alias)} : source;
    }
    async scopes(frameId) {
        const frame = this.frames.get(frameId);
        if (!frame?.generated || !frame.base) return this.request('scopes', {frameId});
        const own = await this.request('scopes', {frameId});
        const outer = await this.request('scopes', {frameId: frame.base.id});
        const groups=new Map();
        for (const scope of [...own.scopes,...outer.scopes]) {
            const key=scope.name+'\0'+(scope.presentationHint || '');
            if (!groups.has(key)) groups.set(key,{scope,references:[]});
            groups.get(key).references.push(scope.variablesReference);
        }
        return {scopes:[...groups.values()].map(({scope,references})=>{
            const ref=++this.reference;this.references.set(ref,references);
            return {...scope,variablesReference:ref,namedVariables:undefined,indexedVariables:undefined};
        })};
    }
    async variables(args) {
        const variables = new Map();
        for (const reference of this.references.get(args.variablesReference)) {
            const body = await this.request('variables', {variablesReference: reference});
            for (const variable of body.variables || []) {
                if (/__thor_[a-f0-9]{12}_/.test(variable.name) || variables.has(variable.name)) continue;
                variables.set(variable.name, variable);
            }
        }
        return {variables: [...variables.values()].slice(args.start || 0, args.count ? (args.start || 0) + args.count : undefined)};
    }
    async stopped(message) {
        const step = this.step;
        const reason = message.body.reason;
        if (step && reason === 'step') {
            const body = await this.request('stackTrace', {threadId: message.body.threadId, startFrame: 0});
            if (this.step !== step) { this.emit(message); return; }
            const raw = body.stackFrames || [], visible = this.mapFrames(raw), top = visible[0];
            const detail = top && this.frames.get(top.id);
            const same = top && step.start && top.source?.path === step.start.source?.path && top.line === step.start.line;
            const sourcePath = detail?.point?.source?.path || raw[0]?.source?.path;
            const runtimeLibrary = sourcePath && this.maps.debugRoots.some(root => canonical(sourcePath).startsWith(root+path.sep));
            const internal = detail?.point?.internal || runtimeLibrary;
            const sameFunction = top && step.start && top.name === step.start.name && top.source?.path === step.start.source?.path;
            const returnedToOwner = sameFunction && step.ownerPoint && top.line === step.ownerPoint.line && !detail.generated;
            if (top && (internal || same || returnedToOwner || step.command === 'stepOut' && sameFunction) && ++step.count < 10000) {
                const command = runtimeLibrary && !detail.generated ? 'stepOut' : step.command === 'stepOut' ? 'stepOut'
                    : detail?.point?.kind === 'pure-contract' && !detail.generated ? 'next' : step.command;
                await this.request(command, {...step.args, threadId: message.body.threadId});
                return;
            }
        }
        this.step = undefined;
        this.references.clear();
        this.frames.clear();
        if (message.body.hitBreakpointIds) message = {...message, body: {...message.body,
            hitBreakpointIds: [...new Set(message.body.hitBreakpointIds.map(id => this.breakpointIDs.get(id) ?? id))]}};
        this.emit(message);
    }
    dispose() {
        if (this.closing) return this.closing;
        this.closing=new Promise(resolve=>{
            let done=false;
            const finish=()=>{
                if (done) return; done=true;
                this.exited=true;
                this.socket?.destroy();this.server?.close();this.child?.kill();
                this.fail(new Error('Thor debug session closed'));
                resolve();
            };
            if (this.socket && !this.socket.destroyed && !this.exited && !this.disconnecting) {
                const timer=setTimeout(finish,2000);
                this.request('disconnect',{terminateDebuggee:true}).catch(()=>{}).finally(()=>{clearTimeout(timer);finish();});
            } else finish();
        });
        return this.closing;
    }
}

module.exports = {DebugMaps, Framing, ThorDebugAdapter};

// Also expose stdio DAP for integration tests and clients outside VS Code.
if (require.main === module) {
    const adapter = new ThorDebugAdapter({backend: process.argv[2], cwd: process.cwd()});
    adapter.onDidSendMessage(message => process.stdout.write(Framing.encode(message)));
    const framing = new Framing(message => adapter.handleMessage(message));
    process.stdin.on('data', chunk => { try { framing.feed(chunk); } catch (error) { process.stderr.write(error.message + '\n'); adapter.dispose(); } });
    process.stdin.on('end', () => adapter.dispose());
    process.on('SIGTERM', () => { adapter.dispose().finally(()=>process.exit(0)); });
}
