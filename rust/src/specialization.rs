//! Lexical bindings and type spelling for isolated native evaluation helpers.
//! Port of specialization.Binding/type_code and the binding-state operations;
//! execution scheduling is connected by the evaluator separately.
use crate::{
    ast::NodeId,
    digest::sha256,
    lower::unquote,
    semantics::{scope, Constant, Ref, Resolver, Type},
    values,
};
use serde_json::{json, Value};
use std::{
    collections::{BTreeMap, BTreeSet},
    fmt,
    path::PathBuf,
};

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Unresolved(pub String);
impl fmt::Display for Unresolved {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        self.0.fmt(f)
    }
}
impl std::error::Error for Unresolved {}
type Result<T> = std::result::Result<T, Unresolved>;

#[derive(Clone, Debug)]
pub struct Binding {
    pub symbol: usize,
    pub typ: Type,
    pub value: Option<Value>,
    pub canonical: String,
    pub condition: String,
}
pub type Bindings = BTreeMap<(Ref, String), Binding>;
#[derive(Clone, Debug)]
pub struct EvaluatedCase {
    pub bindings: Bindings,
    pub value: Value,
    pub skipped: Option<bool>,
}
pub type Cases = BTreeMap<Ref, BTreeMap<String, EvaluatedCase>>;

/// Native generic slots are lexical symbols, including dimensions inferred
/// from a parameter type. Explicit value slots keep their annotated type.
pub fn parameters(procedure: Ref, r: &Resolver<'_>) -> Vec<(String, usize, bool)> {
    let source = &r.sources[procedure.file];
    let Some(signature) = source.role(procedure.node, "type") else {
        return vec![];
    };
    let mut slots = BTreeMap::new();
    for node in source.descendants(signature) {
        if source.node(node).kind != "PolyType" {
            continue;
        }
        let name = source.names.get(source.node(node).name).to_string();
        let at = source
            .role(procedure.node, "body")
            .unwrap_or(procedure.node);
        let Some(symbol) = r.lookup(
            Ref {
                file: procedure.file,
                node: at,
            },
            &name,
        ) else {
            continue;
        };
        let data = &r.symbols[symbol];
        let value = if let Some(annotation) = data.annotation {
            source.node(annotation).kind != "TypeidType"
        } else {
            source.node(node).parent.is_some_and(|parent| {
                source.node(parent).kind == "ArrayType" && source.role(parent, "len") == Some(node)
            })
        };
        slots.insert(name, (symbol, value));
    }
    slots
        .into_iter()
        .map(|(name, (symbol, value))| (name, symbol, value))
        .collect()
}

/// Type spelling can add helper-only imports, just like the reference's
/// evaluation_import callback. The user's project does not acquire those imports.
pub struct TypeCode {
    pub file: usize,
    pub context: Option<Ref>,
    pub allow_imports: bool,
    pub imports: BTreeMap<String, PathBuf>,
}
impl TypeCode {
    pub fn new(file: usize, context: Option<Ref>) -> Self {
        Self {
            file,
            context,
            allow_imports: false,
            imports: BTreeMap::new(),
        }
    }
    fn access(&mut self, owner: Ref, name: &str, r: &Resolver<'_>) -> Result<String> {
        let source = &r.sources[self.file];
        let directory = r.sources[owner.file].path.parent().unwrap();
        if source.path.parent() == Some(directory) {
            return Ok(name.into());
        }
        for import in source.nodes.iter().filter(|node| node.kind == "Import") {
            let Ok(path) = unquote(&import.op) else {
                continue;
            };
            let resolved = if let Some((collection, suffix)) = path.split_once(':') {
                source
                    .collection_roots
                    .get(collection)
                    .map(|root| root.join(suffix))
            } else {
                source.path.parent().map(|root| root.join(&path))
            };
            if resolved
                .and_then(|path| std::fs::canonicalize(path).ok())
                .as_deref()
                != Some(directory)
            {
                continue;
            }
            let explicit = source.names.get(import.name);
            let alias = if explicit.is_empty() {
                path.rsplit(['/', ':']).next().unwrap_or("")
            } else {
                explicit
            };
            if !["_", "*"].contains(&alias) {
                let alias = format!(
                    "{}evaluation_type_import_{}",
                    source.prefix, import.span.start
                );
                self.imports.insert(alias.clone(), directory.into());
                return Ok(format!("{alias}.{name}"));
            }
        }
        if !self.allow_imports {
            return Err(Unresolved(
                "The generic type is not visible from this package.".into(),
            ));
        }
        let key = sha256(directory.to_string_lossy().as_bytes());
        let alias = format!("{}evaluation_dependency_{}", source.prefix, &key[..12]);
        self.imports.insert(alias.clone(), directory.into());
        Ok(format!("{alias}.{name}"))
    }
    pub fn render(&mut self, typ: &Type, r: &Resolver<'_>) -> Result<String> {
        match typ {
            Type::Constant(Constant::Integer(value)) => return Ok(value.to_string()),
            Type::Constant(Constant::Boolean(value)) => return Ok(value.to_string()),
            Type::Scalar { name, .. } | Type::String { name, .. } => return Ok(name.clone()),
            Type::Array {
                elem,
                length: Some(length),
            } => return Ok(format!("[{length}]{}", self.render(elem, r)?)),
            Type::Slice(elem) | Type::Pointer(elem) | Type::Dynamic(elem) => {
                let prefix = match typ {
                    Type::Slice(_) => "[]",
                    Type::Pointer(_) => "^",
                    _ => "[dynamic]",
                };
                return Ok(format!("{prefix}{}", self.render(elem, r)?));
            }
            _ => {}
        }
        let owner = match typ {
            Type::Nominal(owner, _)
            | Type::Struct(owner)
            | Type::Record(owner, _)
            | Type::Enum(owner)
            | Type::Union(owner) => *owner,
            _ => return Err(Unresolved("The generic type could not be resolved.".into())),
        };
        let source = &r.sources[owner.file];
        if let Some(declaration) = source
            .node(owner.node)
            .parent
            .filter(|node| source.node(*node).kind == "Declaration")
        {
            let names = source.group(declaration, "names");
            if names.len() == 1 {
                let lexical = scope(source, declaration);
                if lexical != NodeId(0)
                    && !self.context.is_some_and(|context| {
                        context.file == owner.file
                            && (context.node == lexical
                                || r.sources[context.file]
                                    .ancestors(context.node)
                                    .any(|node| node == lexical))
                    })
                {
                    return Err(Unresolved(
                        "A local generic type needs its original lexical scope.".into(),
                    ));
                }
                let mut name = self.access(owner, source.text(names[0]), r)?;
                if let Type::Record(_, bindings) = typ {
                    if let Some(params) = source.role(owner.node, "params") {
                        let mut args = vec![];
                        for field in &source.node(params).children {
                            for parameter in source.group(*field, "names") {
                                let parameter = source.text(*parameter).trim_start_matches('$');
                                let value = bindings
                                    .iter()
                                    .find(|(name, _)| name == parameter)
                                    .map(|(_, value)| value)
                                    .ok_or_else(|| {
                                        Unresolved("The generic type could not be resolved.".into())
                                    })?;
                                args.push(self.render(value, r)?);
                            }
                        }
                        name.push('(');
                        name.push_str(&args.join(","));
                        name.push(')');
                    }
                }
                return Ok(name);
            }
        }
        if matches!(
            source.node(owner.node).kind.as_str(),
            "StructType" | "EnumType" | "UnionType" | "BitSetType"
        ) {
            return Ok(source.text(owner.node).into());
        }
        Err(Unresolved("The generic type could not be resolved.".into()))
    }
}

#[derive(Default)]
pub struct Specializations {
    pub bindings: Bindings,
    pub visited: BTreeSet<(Ref, String)>,
    pub running: BTreeSet<(Ref, String)>,
    pub origin: Option<Ref>,
    pub origin_bindings: Bindings,
    pub frames: Vec<(Ref, Bindings)>,
}
pub struct Saved {
    bindings: Bindings,
    origin: Option<Ref>,
    origin_bindings: Bindings,
    frames: Vec<(Ref, Bindings)>,
}
impl Specializations {
    pub fn apply(&self, r: &mut Resolver<'_>) {
        Self::apply_bindings(&self.bindings, r);
    }
    pub fn apply_bindings(bindings: &Bindings, r: &mut Resolver<'_>) {
        let mut types = BTreeMap::new();
        let mut constants = BTreeMap::new();
        for (key, binding) in bindings {
            if let Some(value) = &binding.value {
                constants.insert(key.clone(), (binding.typ.clone(), value.clone()));
            } else {
                types.insert(key.clone(), binding.typ.clone());
            }
        }
        r.set_lexical_bindings(types, constants);
    }
    pub fn relevant(&self, at: Ref, r: &Resolver<'_>) -> Bindings {
        let owners = r.sources[at.file]
            .ancestors(at.node)
            .chain(std::iter::once(at.node))
            .collect::<BTreeSet<_>>();
        self.bindings
            .iter()
            .filter(|((declaration, name), binding)| {
                let symbol = &r.symbols[binding.symbol];
                declaration.file == at.file
                    && owners.contains(&symbol.scope)
                    && r.lookup(at, name).is_some_and(|visible| {
                        let visible = &r.symbols[visible];
                        visible.reference == *declaration && visible.name == *name
                    })
            })
            .map(|(key, binding)| (key.clone(), binding.clone()))
            .collect()
    }
    pub fn key(&self, at: Ref, r: &Resolver<'_>) -> (Ref, String) {
        (at, Self::fingerprint(&self.relevant(at, r), r))
    }
    pub fn fingerprint(bindings: &Bindings, r: &Resolver<'_>) -> String {
        if bindings.is_empty() {
            return String::new();
        }
        let mut entries = bindings
            .iter()
            .map(|((declaration, name), binding)| {
                let mut context = TypeCode::new(declaration.file, None);
                let typ = context
                    .render(&binding.typ, r)
                    .map(Value::String)
                    .unwrap_or_else(|_| shape(&binding.typ, r));
                json!([
                    r.sources[declaration.file].path,
                    r.sources[declaration.file]
                        .node(declaration.node)
                        .span
                        .start,
                    name,
                    typ,
                    binding.value
                ])
            })
            .collect::<Vec<_>>();
        entries.sort_by_key(Value::to_string);
        sha256(serde_json::to_string(&entries).unwrap().as_bytes())[..20].into()
    }
    pub fn enter(
        &mut self,
        bindings: Bindings,
        origin: Option<Ref>,
        origin_bindings: Option<Bindings>,
    ) -> Saved {
        let saved = Saved {
            bindings: std::mem::replace(&mut self.bindings, bindings),
            origin: self.origin,
            origin_bindings: self.origin_bindings.clone(),
            frames: self.frames.clone(),
        };
        if let Some(origin) = origin {
            self.origin = Some(origin);
        }
        if let Some(bindings) = origin_bindings {
            self.origin_bindings = bindings;
        }
        if let Some(origin) = origin {
            self.frames.push((origin, self.origin_bindings.clone()));
        }
        saved
    }
    pub fn restore(&mut self, saved: Saved) {
        self.bindings = saved.bindings;
        self.origin = saved.origin;
        self.origin_bindings = saved.origin_bindings;
        self.frames = saved.frames;
    }
    pub fn declarations(
        bindings: &Bindings,
        context: &mut TypeCode,
        r: &Resolver<'_>,
    ) -> Result<String> {
        let mut lines = vec![];
        for ((_, name), binding) in bindings {
            let typ = context.render(&binding.typ, r)?;
            let value = if let Some(value) = &binding.value {
                values::expression(
                    value,
                    &typ,
                    &format!("{}meta", r.sources[context.file].prefix),
                )
                .map_err(|error| Unresolved(error.to_string()))?
            } else {
                typ
            };
            lines.push(format!("{name} :: {value}"));
        }
        Ok(lines.join("\n") + "\n")
    }
    pub fn predicate(
        bindings: &Bindings,
        context: &mut TypeCode,
        r: &Resolver<'_>,
    ) -> Result<String> {
        let mut predicates = vec![];
        let meta = format!("{}meta", r.sources[context.file].prefix);
        for ((_, name), binding) in bindings {
            let typ = match context.render(&binding.typ, r) {
                Ok(typ) => typ,
                Err(error) => {
                    if binding.value.is_some() || binding.canonical.is_empty() {
                        return Err(error);
                    }
                    predicates.push(format!(
                        "({meta}.type_canonical_name({name}) == {})",
                        json!(binding.canonical)
                    ));
                    continue;
                }
            };
            let value = if let Some(value) = &binding.value {
                values::expression(value, &typ, &meta)
                    .map_err(|error| Unresolved(error.to_string()))?
            } else {
                typ
            };
            predicates.push(format!("({name} == {value})"));
        }
        Ok(predicates.join(" && "))
    }
}

fn shape(typ: &Type, r: &Resolver<'_>) -> Value {
    let origin = |owner: &Ref| {
        json!([
            r.sources[owner.file].path,
            r.sources[owner.file].node(owner.node).span.start
        ])
    };
    match typ {
        Type::Nominal(owner, base) => json!(["nominal", origin(owner), shape(base, r)]),
        Type::Struct(owner) | Type::Enum(owner) | Type::Union(owner) => {
            json!(["named", origin(owner)])
        }
        Type::Record(owner, bindings) => json!([
            "record",
            origin(owner),
            bindings
                .iter()
                .map(|(name, typ)| json!([name, shape(typ, r)]))
                .collect::<Vec<_>>()
        ]),
        Type::Array { elem, length } | Type::Fixed { elem, length } => json!([
            if matches!(typ, Type::Fixed { .. }) {
                "fixed"
            } else {
                "array"
            },
            length,
            shape(elem, r)
        ]),
        Type::Slice(elem)
        | Type::Pointer(elem)
        | Type::Dynamic(elem)
        | Type::Soa(elem)
        | Type::Set(elem) => json!([
            match typ {
                Type::Slice(_) => "slice",
                Type::Pointer(_) => "pointer",
                Type::Dynamic(_) => "dynamic",
                Type::Soa(_) => "soa",
                _ => "set",
            },
            shape(elem, r)
        ]),
        Type::Map { key, value } => json!(["map", shape(key, r), shape(value, r)]),
        Type::Anonymous(fields) => json!([
            "struct",
            fields
                .iter()
                .map(|(name, typ)| json!([name, shape(typ, r)]))
                .collect::<Vec<_>>()
        ]),
        Type::Procedure(owner) => json!(["procedure", origin(owner)]),
        _ => serde_json::to_value(typ).unwrap(),
    }
}
