//! Verified straight-line scalar site reuse; port of scalarcache.ScalarSites.
use super::*;
use crate::{digest::sha256, evaluationcache::canonical, evaluationinputs::Inputs};
use serde_json::json;

const PRIMITIVES: &[&str] = &[
    "bool",
    "int",
    "uint",
    "uintptr",
    "i8",
    "i16",
    "i32",
    "i64",
    "i128",
    "u8",
    "u16",
    "u32",
    "u64",
    "u128",
    "f16",
    "f32",
    "f64",
    "complex32",
    "complex64",
    "complex128",
    "quaternion64",
    "quaternion128",
    "quaternion256",
];

pub(super) struct ExecutionRecord {
    pub target: Option<String>,
    pub keep: BTreeSet<Ref>,
    pub procedures: BTreeSet<Ref>,
}
impl Scheduler<'_> {
    fn scalar_owner(&self, node: Ref) -> Option<Ref> {
        self.source(node)
            .ancestors(node.node)
            .find(|id| self.source(node).node(*id).kind == "Procedure")
            .map(|id| Ref {
                file: node.file,
                node: id,
            })
    }
    fn scalar_eligible(&self, site: Ref) -> bool {
        if self.kind(site) != "Run"
            || !self.cache.borrow().enabled
            || !self.project.options.overlays.is_empty()
            || !self
                .specializations
                .relevant(site, &self.resolver)
                .is_empty()
        {
            return false;
        }
        let owner = self.scalar_owner(site);
        let body = owner.and_then(|owner| self.role(owner, "body"));
        if scope(self.source(site), site.node) != body.map_or(NodeId(0), |body| body.node) {
            return false;
        }
        if body.is_some_and(|body| {
            self.children(body)
                .iter()
                .any(|node| ["When", "CompileSwitch", "ForeignBlock"].contains(&self.kind(*node)))
        }) {
            return false;
        }
        !self.source(site).nodes.iter().any(|node| {
            node.kind == "Using" || node.kind == "Declaration" && node.data["using_field"] == true
        })
    }
    fn scalar_key(&self, site: Ref) -> Option<String> {
        let owner = self.scalar_owner(site);
        let context = owner
            .and_then(|owner| self.source(owner).node(owner.node).parent)
            .map(|id| {
                self.source(site)
                    .group(id, "names")
                    .iter()
                    .map(|name| self.source(site).text(*name))
                    .collect::<Vec<_>>()
            })
            .unwrap_or_default();
        let native = self
            .project
            .sources
            .iter()
            .find_map(|source| source.info["odin_root"].as_str())?;
        let identity = self
            .cache
            .borrow_mut()
            .identity(&self.project.frontend.odin, &self.project.options.root)?;
        let call = self.role(site, "expr")?;
        Some(sha256(&canonical(&json!([
            self.project.options.entry,
            native,
            self.source(site).path,
            context,
            self.source(call).text(call.node),
            self.project.options.flags,
            self.project.frontend.test,
            identity
        ]))))
    }
    fn declaration_text(&self, node: Ref) -> &str {
        let source = self.source(node);
        let start = self
            .group(node, "attributes")
            .iter()
            .map(|node| source.node(node.node).span.start)
            .chain(std::iter::once(source.node(node.node).span.start))
            .min()
            .unwrap();
        &source.raw[start..source.node(node.node).span.end]
    }
    fn scalar_bindings(&self, site: Ref, names: &[Value]) -> Option<Value> {
        let mut result = vec![];
        for name in names {
            let name = name.as_str()?;
            let Some(index) = self.resolver.lookup(site, name) else {
                result.push(json!([name, null]));
                continue;
            };
            let node = self.resolver.symbols[index].reference;
            let local = self
                .source(node)
                .node(scope(self.source(node), node.node))
                .kind
                != "File";
            let declaration = if local {
                json!(self.declaration_text(node))
            } else {
                json!(self
                    .group(node, "names")
                    .iter()
                    .map(|name| self.source(*name).text(name.node))
                    .collect::<Vec<_>>())
            };
            result.push(json!([
                name,
                self.source(node).path,
                self.kind(node),
                declaration
            ]));
        }
        Some(Value::Array(result))
    }
    fn scalar_bodies(&self, descriptors: &[Value]) -> Option<Value> {
        let mut result = vec![];
        for descriptor in descriptors {
            let row = descriptor.as_array().filter(|row| row.len() == 2)?;
            let path = row[0].as_str()?;
            let names = row[1].as_array()?;
            for name in names {
                name.as_str()?;
            }
            let file = self
                .project
                .sources
                .iter()
                .position(|source| source.path == std::path::Path::new(path))?;
            let source = &self.project.sources[file];
            let texts = source
                .nodes
                .iter()
                .enumerate()
                .filter(|(index, node)| {
                    node.kind == "Declaration"
                        && source.node(scope(source, NodeId(*index))).kind == "File"
                        && json!(source
                            .group(NodeId(*index), "names")
                            .iter()
                            .map(|name| source.text(*name))
                            .collect::<Vec<_>>())
                            == row[1]
                })
                .map(|(index, _)| {
                    self.declaration_text(Ref {
                        file,
                        node: NodeId(index),
                    })
                })
                .collect::<Vec<_>>();
            result.push(json!([path, names, texts]));
        }
        Some(Value::Array(result))
    }
    pub(super) fn load_scalar(&mut self, site: Ref) -> Option<Value> {
        if !self.scalar_eligible(site) {
            return None;
        }
        let key = self.scalar_key(site)?;
        let record = self.cache.borrow().read_record("scalar-v1", Some(&key))?;
        if !PRIMITIVES.contains(&record["type"].as_str()?)
            || record["headers"].as_str() != Some(self.prepared_inputs().headers.as_str())
            || record["bindings"] != self.scalar_bindings(site, record["names"].as_array()?)?
            || record["bodies"] != self.scalar_bodies(record["descriptors"].as_array()?)?
            || !Inputs::new(Some(record.get("inputs")?.clone())).valid()
            || !["procedure", "expression"].contains(&record["witness"].as_str()?)
        {
            return None;
        }
        let target = record["target"].as_str()?;
        let value = self.cache.borrow_mut().get(Some(target));
        let call = self.role(site, "expr")?;
        if value.is_none() {
            self.failed_scalar_targets.insert(call, target.into());
            return None;
        }
        self.cache.borrow_mut().prepared_hits += 1;
        self.scalar_witnesses
            .insert(call, record["witness"].as_str()?.into());
        value
    }
    pub(super) fn save_scalar(&mut self, site: Ref) {
        if !self.scalar_eligible(site) {
            return;
        }
        let Some(call) = self.role(site, "expr") else {
            return;
        };
        let Some(record) = self.execution_records.get(&call) else {
            return;
        };
        let Some(target) = record.target.clone() else {
            return;
        };
        let keep = record.keep.clone();
        let procedures = record.procedures.clone();
        let Type::Scalar { name, .. } = self.resolver.type_of(call) else {
            return;
        };
        if !PRIMITIVES.contains(&name.as_str()) {
            return;
        }
        for mut arg in self.group(call, "args") {
            if self.kind(arg) == "FieldValue" {
                arg = self.role(arg, "value").unwrap_or(arg);
            }
            if !matches!(
                self.resolver.type_of(arg),
                Type::Scalar { .. } | Type::String { .. }
            ) || self
                .children(arg)
                .iter()
                .any(|node| self.kind(*node) == "Run")
            {
                return;
            }
        }
        if self.resolver.lookup(site, &name).is_some()
            || self
                .source(site)
                .selections
                .iter()
                .any(|selection| selection.names.contains(&name))
            || self
                .source(site)
                .nodes
                .iter()
                .filter(|node| node.kind == "Import")
                .any(|node| {
                    let explicit = self.source(site).names.get(node.name);
                    explicit == name
                        || explicit.is_empty()
                            && unquote(&node.op).ok().is_some_and(|path| {
                                path.rsplit(['/', ':']).next() == Some(name.as_str())
                            })
                })
            || procedures.iter().any(|procedure| {
                !crate::specialization::parameters(*procedure, &self.resolver).is_empty()
            })
            || std::iter::once(&call).chain(&keep).any(|root| {
                self.children(*root).iter().any(|node| {
                    self.kind(*node) == "Directive"
                        && self.source(*node).names.get(self.source(*node).node(node.node).name) == "procedure"
                })
            })
        {
            return;
        }
        let scopes = self
            .source(site)
            .ancestors(site.node)
            .collect::<BTreeSet<_>>();
        let owner = self.scalar_owner(site);
        let mut names = BTreeSet::new();
        for root in std::iter::once(&call)
            .chain(&keep)
            .filter(|node| !self.source(**node).shared_native)
        {
            for node in self
                .children(*root)
                .into_iter()
                .filter(|node| self.kind(*node) == "Ident")
            {
                let source = self.source(node);
                let reference = source.node(node.node).parent.is_none_or(|parent| {
                    !(["Declaration", "Field", "Range"]
                        .contains(&source.node(parent).kind.as_str())
                        && source.group(parent, "names").contains(&node.node)
                        || ["Selector", "FieldValue"].contains(&source.node(parent).kind.as_str())
                            && source.role(parent, "field") == Some(node.node))
                });
                if !reference {
                    continue;
                }
                let text = source.text(node.node);
                let symbol = self.resolver.lookup(node, text);
                if self.scalar_owner(node) == owner
                    || symbol.is_some_and(|index| {
                        let symbol = &self.resolver.symbols[index];
                        symbol.reference.file == site.file && scopes.contains(&symbol.scope)
                    })
                {
                    names.insert(text.to_string());
                }
            }
        }
        names.insert(name.clone());
        let names = names.into_iter().map(Value::String).collect::<Vec<_>>();
        let descriptors = keep
            .iter()
            .filter(|node| {
                !self.source(**node).shared_native
                    && self.kind(**node) == "Declaration"
                    && self
                        .source(**node)
                        .node(scope(self.source(**node), node.node))
                        .kind
                        == "File"
                    && self
                        .group(**node, "values")
                        .iter()
                        .any(|node| self.kind(*node) == "Procedure")
            })
            .map(|node| {
                json!([
                    self.source(*node).path,
                    self.group(*node, "names")
                        .iter()
                        .map(|name| self.source(*name).text(name.node))
                        .collect::<Vec<_>>()
                ])
            })
            .collect::<Vec<_>>();
        let mut inputs = Inputs::default();
        let mut native = BTreeSet::<PathBuf>::new();
        for source in &self.project.sources {
            inputs.source(source, source.shared_native);
            if source.shared_native {
                native.insert(source.path.parent().unwrap().into());
            }
        }
        for directory in native {
            // Native discovery retains selected platform files only. Observe
            // the whole package membership, including unparsed files, rather
            // than publishing an immediately invalid partial member list.
            let Ok(names) = crate::evaluationinputs::members(&directory, &[".odin".into()]) else {
                return;
            };
            let paths = names
                .iter()
                .map(|name| directory.join(name))
                .collect::<Vec<_>>();
            inputs.package(&directory, &paths, &[".odin".into()]);
            for path in paths {
                if !self
                    .project
                    .sources
                    .iter()
                    .any(|source| source.path == path)
                {
                    inputs.file(&path, None, true);
                }
            }
        }
        // Preserve the raw collection path as well as resolved resource paths.
        for flag in &self.project.options.flags {
            if let Some((_, path)) = flag
                .strip_prefix("-collection:")
                .and_then(|value| value.split_once('='))
            {
                inputs.binding(std::path::Path::new(path));
            }
        }
        if !inputs.valid() {
            return;
        }
        let Some(key) = self.scalar_key(site) else {
            return;
        };
        let Some(bindings) = self.scalar_bindings(site, &names) else {
            return;
        };
        let Some(bodies) = self.scalar_bodies(&descriptors) else {
            return;
        };
        let witness = if self.resolver.call_plans(call, &Substitutions::new()).len() == 1 {
            "procedure"
        } else {
            "expression"
        };
        self.cache.borrow().write_record("scalar-v1",Some(&key),&json!({"type":name,"target":target,
            "witness":witness,"headers":self.prepared_inputs().headers,"names":names,"bindings":bindings,
            "descriptors":descriptors,"bodies":bodies,"inputs":inputs.data}));
    }
}
