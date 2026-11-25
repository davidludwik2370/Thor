//! Preserve source-sensitive Odin expressions before moving them into helpers.
use super::*;

impl Lower<'_, '_> {
    pub(super) fn source_mode(&self) -> &str {
        self.resolver
            .flags
            .iter()
            .rev()
            .find_map(|f| f.strip_prefix("-source-code-locations:"))
            .unwrap_or("normal")
    }
    pub(super) fn source_string(&self, value: &str, prefix: &str) -> String {
        // Odin source locations use forward slashes on Windows too. Normalize
        // before filename selection and hashing, without changing procedure
        // names or literal backslashes on POSIX hosts.
        let path = (cfg!(windows) && matches!(prefix, "F" | "D"))
            .then(|| value.replace('\\', "/"));
        let value = path.as_deref().unwrap_or(value);
        match self.source_mode() {
            "none" => String::new(),
            "filename" if prefix != "P" => value
                .rsplit('/')
                .next()
                .unwrap_or("")
                .into(),
            "obfuscated" if !value.is_empty() => {
                format!("{prefix}x{:x}", source_hash(value.as_bytes()))
            }
            _ => value.into(),
        }
    }
    pub(super) fn source_number(&self, value: usize) -> i32 {
        match self.source_mode() {
            "none" => 0,
            "obfuscated" => {
                let n = source_hash(&(value as i32).to_ne_bytes()) as i32;
                if n < 0 {
                    1i32.wrapping_sub(n)
                } else {
                    n
                }
            }
            _ => value as i32,
        }
    }
    fn lexical_procedure(source: &Source, id: NodeId) -> String {
        let Some(proc) = active_procedure(source, id) else {
            return String::new();
        };
        let mut value = proc;
        while let Some(parent) = source
            .node(value)
            .parent
            .filter(|p| source.node(*p).kind == "Paren")
        {
            value = parent;
        }
        if let Some(decl) = source
            .node(value)
            .parent
            .filter(|p| source.node(*p).kind == "Declaration" && !source.node(*p).mutable)
        {
            if let Some(index) = source
                .group(decl, "values")
                .iter()
                .position(|v| *v == value)
            {
                if let Some(name) = source.group(decl, "names").get(index) {
                    return source.text(*name).into();
                }
            }
        }
        "(anonymous-procedure)".into()
    }
    fn original_location(&mut self, target: Ref, site: NodeId) -> Code {
        let source = &self.resolver.sources[target.file];
        let offset = source.node(target.node).span.start;
        let (line, _) = source.position(offset);
        let column = source.raw[..offset]
            .rsplit('\n')
            .next()
            .unwrap_or("")
            .chars()
            .count()
            + 1;
        let file = self.source_string(&source.path.to_string_lossy(), "F");
        let proc = self.source_string(&location_procedure(source, target.node), "P");
        let alias = format!("{}source_runtime", self.prefix);
        self.modules.insert(alias.clone(), "base:runtime".into());
        self.g(
            format!(
                "{alias}.Source_Code_Location{{{}, {}, {}, {}}}",
                json!(file),
                self.source_number(line),
                self.source_number(column),
                json!(proc)
            ),
            site,
        )
    }
    pub(super) fn source_directive(&mut self, id: NodeId) -> Option<Code> {
        let node = self.source.node(id);
        if node.kind == "Directive" {
            let name = self.source.names.get(node.name);
            if name == "branch_location" {
                return self.branch_location(id);
            }
            let value = match name {
                "file" => {
                    json!(self.source_string(&self.source.path.to_string_lossy(), "F")).to_string()
                }
                "directory" => {
                    let dir = format!(
                        "{}{sep}",
                        self.source.path.parent()?.display(),
                        sep = std::path::MAIN_SEPARATOR
                    );
                    json!(self.source_string(&dir, "D")).to_string()
                }
                "line" => self
                    .source_number(self.source.position(node.span.start).0)
                    .to_string(),
                "procedure" if active_procedure(self.source, id).is_some() => {
                    json!(self.source_string(&Self::lexical_procedure(self.source, id), "P"))
                        .to_string()
                }
                _ => return None,
            };
            return Some(self.g(value, id));
        }
        if node.kind == "Call"
            && self
                .source
                .role(id, "callee")
                .is_some_and(|c| self.source.text(c) == "#location")
        {
            let args = self.source.group(id, "args");
            let target = if args.is_empty() {
                self.reference(self.source.role(id, "callee")?)
            } else {
                let symbol = self.resolver.symbol(self.reference(args[0]))?;
                let symbol = &self.resolver.symbols[symbol];
                Ref {
                    file: symbol.reference.file,
                    node: symbol.name_node,
                }
            };
            return Some(self.original_location(target, id));
        }
        None
    }
    pub(super) fn resource_argument(&self, id: NodeId) -> bool {
        self.source.node(id).parent.is_some_and(|parent| {
            self.source.node(parent).kind == "Call"
                && self.source.group(parent, "args").first() == Some(&id)
                && self.source.role(parent, "callee").is_some_and(|c| {
                    matches!(
                        self.source.text(c),
                        "#load" | "#load_hash" | "#load_directory" | "#exists"
                    )
                })
        })
    }
    pub(super) fn original_resource(&self, id: NodeId, code: Code) -> Code {
        // Keep evaluation in Odin: paths can depend on #config and generic constants.
        let p = format!("({})", code.finish().text);
        let absolute = if cfg!(windows) {
            format!("(({p}[0]=='/' || {p}[0]=='\\\\') when len({p})>0 else false) || ({p}[1]==':' when len({p})>1 else false)")
        } else {
            format!("({p}[0]=='/' when len({p})>0 else false)")
        };
        let dir = format!(
            "{}{sep}",
            self.source.path.parent().unwrap().display(),
            sep = std::path::MAIN_SEPARATOR
        );
        let mut relative = format!("({} + {p})", json!(dir));
        let mut roots = BTreeMap::new();
        if let Some(root) = self.source.info["odin_root"].as_str() {
            for name in ["base", "core", "vendor"] {
                roots.insert(name.to_string(), std::path::Path::new(root).join(name));
            }
        }
        roots.extend(self.source.collection_roots.clone());
        for (name, root) in roots {
            let prefix = format!("{name}:");
            let len = prefix.len();
            let root = format!("{}{sep}", root.display(), sep = std::path::MAIN_SEPARATOR);
            relative = format!("(({} + {p}[{len}:]) when ({p}[:{len}]=={} when len({p})>={len} else false) else {relative})",json!(root),json!(prefix));
        }
        self.g(format!("({p} when ({absolute}) else {relative})"), id)
    }
    pub(super) fn caller_arguments(&mut self, call: NodeId) -> Result<Vec<(usize, Code)>> {
        let Some(signature) = self.resolver.call_signature(self.reference(call)) else {
            return Ok(vec![]);
        };
        self.caller_arguments_for_signature(call,signature)
    }
    pub(super) fn caller_arguments_for_signature(&mut self,call:NodeId,signature:Ref)->Result<Vec<(usize,Code)>> {
        let source = &self.resolver.sources[signature.file];
        let Some(params) = source.role(signature.node, "params") else {
            return Ok(vec![]);
        };
        let fields = source
            .node(params)
            .children
            .iter()
            .flat_map(|field| {
                source
                    .group(*field, "names")
                    .iter()
                    .map(move |name| (*field, *name))
            })
            .collect::<Vec<_>>();
        if !fields.iter().any(|(f, _)| {
            source
                .role(*f, "default")
                .is_some_and(|d| source.text(d).starts_with("#caller_"))
        }) {
            return Ok(vec![]);
        }
        let mut supplied = HashMap::new();
        let callee = self.role(call, "callee")?;
        let receiver =
            self.source.node(callee).kind == "Selector" && self.source.node(callee).op == "->";
        let mut index = usize::from(receiver);
        if receiver {
            if let Some((_, name)) = fields.first() {
                supplied.insert(
                    source.text(*name).to_string(),
                    self.source.role(callee, "expr").unwrap(),
                );
            }
        }
        for arg in self.source.group(call, "args") {
            if self.source.node(*arg).kind == "FieldValue" {
                if let (Some(name), Some(value)) = (
                    self.source.role(*arg, "field"),
                    self.source.role(*arg, "value"),
                ) {
                    supplied.insert(self.source.text(name).into(), value);
                }
            } else {
                if let Some((_, name)) = fields.get(index) {
                    supplied.insert(source.text(*name).into(), *arg);
                }
                index += 1;
            }
        }
        let mut added = vec![];
        for (field, name) in &fields {
            let name = source.text(*name).to_string();
            if supplied.contains_key(&name) {
                continue;
            }
            let Some(default) = source.role(*field, "default") else {
                continue;
            };
            let text = source.text(default);
            let value = if text == "#caller_location" {
                self.original_location(self.reference(callee), call)
            } else if text.starts_with("#caller_expression") {
                let target = source
                    .group(default, "args")
                    .first()
                    .map(|n| source.text(*n));
                let text = if let Some(target) = target {
                    if let Some(arg) = supplied.get(target) {
                        source_expression(self.source, *arg)
                    } else {
                        String::new()
                    }
                } else {
                    source_expression(self.source, call)
                };
                self.g(json!(text).to_string(), call)
            } else {
                continue;
            };
            added.push(Code::concat([self.g(format!("{name}="), call), value]));
        }
        let args = self.source.group(call, "args");
        let position = args
            .last()
            .map(|a| self.source.node(*a).span.end)
            .unwrap_or(self.source.node(call).span.end - 1);
        let mut code = vec![];
        for (i, value) in added.into_iter().enumerate() {
            code.push(self.g(if i > 0 || !args.is_empty() { ", " } else { "" }, call));
            code.push(value);
        }
        Ok(if code.is_empty() {
            vec![]
        } else {
            vec![(position, Code::concat(code))]
        })
    }
}
fn source_hash(bytes: &[u8]) -> u64 {
    bytes.iter().fold(0xcbf29ce484222325u64, |h, b| {
        (h ^ u64::from(*b)).wrapping_mul(0x100000001b3)
    })
}

pub(super) fn active_procedure(s: &Source, id: NodeId) -> Option<NodeId> {
    s.ancestors(id).find(|n| {
        s.node(*n).kind == "Procedure"
            && s.role(*n, "body")
                .is_some_and(|body| s.node(body).span.contains(s.node(id).span))
    })
}

pub(super) fn location_procedure(s: &Source, id: NodeId) -> String {
    fn identity(s: &Source, proc: NodeId) -> String {
        let name = Lower::lexical_procedure(s, s.role(proc, "body").unwrap_or(proc));
        let parent = s.ancestors(proc).find(|n| s.node(*n).kind == "Procedure");
        let package = s.names.get(s.node(NodeId(0)).name);
        let prefix = parent
            .map(|p| identity(s, p))
            .unwrap_or_else(|| "_proclit".into());
        if name != "(anonymous-procedure)" {
            if parent.is_none() {
                return format!("{package}::{name}");
            }
            let mut value = proc;
            while let Some(p) = s.node(value).parent.filter(|p| s.node(*p).kind == "Paren") {
                value = p;
            }
            let offset = s
                .node(value)
                .parent
                .and_then(|d| s.group(d, "names").first())
                .map(|n| s.node(*n).span.start)
                .unwrap_or(s.node(proc).span.start);
            format!("{prefix}::{name}-{offset}")
        } else {
            format!(
                "{prefix}$anon-{package}:{}:{}",
                s.path.file_name().unwrap_or_default().to_string_lossy(),
                s.node(proc).span.start
            )
        }
    }
    let Some(proc) = active_procedure(s, id) else {
        return String::new();
    };
    let lexical = Lower::lexical_procedure(s, id);
    if lexical == "(anonymous-procedure)" {
        identity(s, proc)
    } else {
        lexical
    }
}

/// Odin's #caller_expression prints the original AST with canonical spacing.
fn source_expression(s: &Source, id: NodeId) -> String {
    let n = s.node(id);
    let r = |name| {
        s.role(id, name)
            .map(|c| source_expression(s, c))
            .unwrap_or_default()
    };
    let list = |name| {
        s.group(id, name)
            .iter()
            .map(|c| source_expression(s, *c))
            .collect::<Vec<_>>()
            .join(", ")
    };
    match n.kind.as_str() {
        "Binary" => format!("{} {} {}", r("left"), n.op, r("right")),
        "Paren" => format!("({})", r("expr")),
        "Unary" => format!("{}{}", n.op, r("expr")),
        "Deref" => format!("{}^", r("expr")),
        "Selector" => format!("{}{}{}", r("expr"), n.op, r("field")),
        "Index" => format!("{}[{}]", r("expr"), r("index")),
        "Slice" => format!("{}[{}:{}]", r("expr"), r("low"), r("high")),
        "FieldValue" => format!("{} = {}", r("field"), r("value")),
        "Call" => format!("{}({})", r("callee"), list("args")),
        "Ellipsis" => format!("..{}", r("expr")),
        "Compound" => format!("{}{{{}}}", r("type"), list("elems")),
        "Cast" => format!("{}({}){}", n.op, r("type"), r("expr")),
        "AutoCast" => format!("auto_cast {}", r("expr")),
        "OrElse" => format!("{} or_else {}", r("x"), r("y")),
        "OrReturn" => format!("{} or_return", r("expr")),
        "Ternary" if n.op == "?" => format!("{} ? {} : {}", r("cond"), r("x"), r("y")),
        "Ternary" => format!("{} if {} else {}", r("x"), r("cond"), r("y")),
        "WhenExpr" => format!("{} when {} else {}", r("x"), r("cond"), r("y")),
        "ProcedureGroup" => format!("proc{{{}}}", list("args")),
        "ArrayType" => format!(
            "{}[{}]{}",
            if n.op.is_empty() {
                String::new()
            } else {
                format!("#{}", n.op)
            },
            r("len"),
            r("elem")
        ),
        "DynamicArrayType" => format!(
            "{}[dynamic{}]{}",
            if n.op.is_empty() {
                String::new()
            } else {
                format!("#{}", n.op)
            },
            if s.role(id, "capacity").is_some() {
                format!("; {}", r("capacity"))
            } else {
                String::new()
            },
            r("elem")
        ),
        "PointerType" => format!("^{}", r("elem")),
        "MultiPointerType" => format!("[^]{}", r("elem")),
        "MapType" => format!("map[{}]{}", r("key"), r("value")),
        "Fields" => {
            let names = n
                .children
                .iter()
                .any(|f| s.group(*f, "names").iter().any(|n| s.text(*n) != "_"));
            n.children
                .iter()
                .map(|f| {
                    if names {
                        source_expression(s, *f)
                    } else {
                        s.role(*f, "type")
                            .map(|t| source_expression(s, t))
                            .unwrap_or_default()
                    }
                })
                .collect::<Vec<_>>()
                .join(", ")
        }
        "Field" => {
            let names = s.group(id, "names");
            let prefix = names
                .first()
                .map(|name| {
                    s.raw[n.span.start..s.node(*name).span.start]
                        .trim()
                        .to_string()
                })
                .unwrap_or_default();
            let typ = r("type");
            let default = r("default");
            format!(
                "{}{}{}{}{}",
                if prefix.is_empty() {
                    String::new()
                } else {
                    format!("{prefix} ")
                },
                list("names"),
                if names.is_empty() {
                    ""
                } else if typ.is_empty() {
                    " :"
                } else {
                    ": "
                },
                typ,
                if default.is_empty() {
                    String::new()
                } else {
                    format!(
                        "{}= {default}",
                        if s.role(id, "type").is_some() {
                            " "
                        } else {
                            ""
                        }
                    )
                }
            )
        }
        "ProcedureType" => {
            let results = s.role(id, "results");
            let named = results.is_some_and(|p| {
                s.node(p)
                    .children
                    .iter()
                    .any(|f| !s.group(*f, "names").is_empty())
            });
            format!(
                "proc({}){}",
                r("params"),
                if results.is_some() {
                    format!(
                        " -> {}{}{}",
                        if named { "(" } else { "" },
                        r("results"),
                        if named { ")" } else { "" }
                    )
                } else {
                    String::new()
                }
            )
        }
        "Procedure" => format!(
            "{} {} /* {}:{}!{} */",
            r("type"),
            if s.role(id, "body").is_some() {
                "{...}"
            } else {
                "---"
            },
            s.names.get(s.node(NodeId(0)).name),
            s.path.file_name().unwrap_or_default().to_string_lossy(),
            n.span.start
        ),
        _ => s.text(id).to_string(),
    }
}
