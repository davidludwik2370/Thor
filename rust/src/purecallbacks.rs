//! Pure callback contracts are erased only after every value boundary is checked.
//! The native ABI stays a procedure pointer. No runtime tags or adapters are used.
use crate::{
    ast::{NodeId, Source},
    diagnostic::{Error, Result},
    semantics::{Ref, Resolver, Substitutions, Type},
};
use std::collections::{BTreeMap, HashMap, HashSet};

impl Resolver<'_> {
    pub(crate) fn pure_callbacks_present(&self) -> bool {
        *self
            .pure_callback_activation
            .get_or_init(|| present(self.sources))
    }
    pub(crate) fn callable_type(&self, signature: Ref, context: &Substitutions) -> Type {
        let source = &self.sources[signature.file];
        let pure = source.node(signature.node).data["pure_type"] == true
            || source.node(signature.node).parent.is_some_and(|parent| {
                source.node(parent).kind == "Procedure" && source.node(parent).data["pure"] == true
            });
        if !pure {
            return Type::Procedure(signature);
        }
        let captures = if context.is_empty() {
            vec![]
        } else {
            source
                .descendants(signature.node)
                .filter_map(|id| {
                    let node = source.node(id);
                    if !matches!(node.kind.as_str(), "Ident" | "PolyType") {
                        return None;
                    }
                    let name = source.text(id).trim_start_matches('$');
                    context.get(name).map(|typ| (name.to_owned(), typ.clone()))
                })
                .collect::<BTreeMap<_, _>>()
                .into_iter()
                .collect()
        };
        Type::Function(signature, captures)
    }
}

pub(crate) fn callback_tag(r: &Resolver<'_>, symbol: &crate::semantics::Symbol) -> Option<String> {
    let source = &r.sources[symbol.reference.file];
    (symbol.parameter && source.node(symbol.name_node).kind == "PolyType")
        .then(|| format!("{}callback_tag_{}", source.prefix, symbol.name_node.0))
}
pub(crate) fn identity(r: &Resolver<'_>, target: Ref) -> String {
    let source = &r.sources[target.file];
    crate::digest::sha256(
        format!(
            "{}:{}",
            source.path.display(),
            source.node(target.node).span.start
        )
        .as_bytes(),
    )[..20]
        .into()
}
pub(crate) fn callback_slots(r: &mut Resolver<'_>, signature: Ref) -> Vec<(usize, String)> {
    let source = &r.sources[signature.file];
    let Some(params) = source.role(signature.node, "params") else {
        return vec![];
    };
    let fields = source
        .node(params)
        .children
        .iter()
        .flat_map(|field| {
            source
                .group(*field, "names")
                .iter()
                .map(move |name| (*name, source.role(*field, "type")))
        })
        .collect::<Vec<_>>();
    fields
        .into_iter()
        .enumerate()
        .filter_map(|(index, (name, annotation))| {
            if source.node(name).kind != "PolyType" {
                return None;
            }
            let annotation = annotation.map(|node| Ref { node, ..signature })?;
            r.type_of(annotation)
                .pure_callable()
                .then(|| (index, format!("{}callback_tag_{}", source.prefix, name.0)))
        })
        .collect()
}

pub(crate) fn present(sources: &[Source]) -> bool {
    sources.iter().any(|s| {
        !s.excluded
            && s.thor
            && s.info["features"]
                .as_array()
                .into_iter()
                .flatten()
                .any(|feature| {
                    matches!(
                        feature["kind"].as_str(),
                        Some("PureFunctionType" | "PureFunction")
                    )
                })
    })
}

pub(crate) fn validate(r: &mut Resolver<'_>, final_check: bool) -> Result<bool> {
    if !r.pure_callbacks_present() {
        return Ok(false);
    }
    let _phase = crate::profile::span("analysis.pure_callbacks");
    let limit = r
        .sources
        .iter()
        .filter(|s| !s.shared_native)
        .map(|s| s.nodes.len())
        .sum::<usize>()
        .max(1024)
        * 4;
    let roots = r
        .sources
        .iter()
        .enumerate()
        .filter(|(_, s)| !s.excluded && !s.shared_native)
        .map(|(file, _)| Ref {
            file,
            node: NodeId(0),
        })
        .collect::<Vec<_>>();
    let mut checker = Checker {
        r,
        seen: HashSet::new(),
        shapes: HashSet::new(),
        memo: HashMap::new(),
        final_check,
        deferred: false,
        remaining: limit,
    };
    for root in roots {
        checker.walk(root, &Substitutions::new())?;
    }
    Ok(checker.deferred)
}
struct Checker<'a, 'r> {
    r: &'r mut Resolver<'a>,
    seen: HashSet<(Ref, Substitutions)>,
    shapes: HashSet<(Type, Type, bool)>,
    memo: HashMap<(Type, Type, bool), bool>,
    remaining: usize,
    final_check: bool,
    deferred: bool,
}
impl Checker<'_, '_> {
    fn role(&self, at: Ref, role: &str) -> Option<Ref> {
        self.r.sources[at.file]
            .role(at.node, role)
            .map(|node| Ref { node, ..at })
    }
    fn group(&self, at: Ref, role: &str) -> Vec<Ref> {
        self.r.sources[at.file]
            .group(at.node, role)
            .iter()
            .map(|node| Ref { node: *node, ..at })
            .collect()
    }
    fn fail(&self, at: Ref, message: &str) -> Error {
        let source = &self.r.sources[at.file];
        let mut error = Error::at(source, source.node(at.node).span, message);
        error.diagnostics[0]["code"] = serde_json::json!("thor/pure-callback");
        error
    }
    fn signature(&mut self, typ: &Type) -> Option<(Ref, Substitutions)> {
        match typ {
            Type::Nominal(_, base) => self.signature(base),
            Type::Function(sig, captures) => Some((*sig, captures.iter().cloned().collect())),
            Type::Procedure(sig) => Some((*sig, Substitutions::new())),
            _ => None,
        }
    }
    // Nested callback contracts are invariant: erasing a writable container's
    // element contract would let another alias install an impure procedure.
    fn compatible(&mut self, expected: &Type, actual: &Type, invariant: bool) -> bool {
        if expected.is_unknown() || actual.is_unknown() {
            return true;
        }
        if expected == actual {
            return true;
        }
        let key = (expected.clone(), actual.clone(), invariant);
        if let Some(result) = self.memo.get(&key) {
            return *result;
        }
        if !self.shapes.insert(key.clone()) {
            return true;
        }
        let result = self.compatible_inner(expected, actual, invariant);
        self.shapes.remove(&key);
        self.memo.insert(key, result);
        result
    }
    fn has_callback(&mut self, typ: &Type, seen: &mut HashSet<Type>) -> bool {
        if !seen.insert(typ.clone()) {
            return false;
        }
        match typ {
            Type::Function(..) => true,
            Type::Procedure(sig) => ["params", "results"]
                .into_iter()
                .flat_map(|role| self.r.fields(*sig, role))
                .collect::<Vec<_>>()
                .iter()
                .filter_map(|(_, r)| *r)
                .any(|r| {
                    let typ = self.r.type_of(r);
                    self.has_callback(&typ, seen)
                }),
            Type::Nominal(_, t)
            | Type::Pointer(t)
            | Type::Slice(t)
            | Type::Dynamic(t)
            | Type::Array { elem: t, .. }
            | Type::Fixed { elem: t, .. }
            | Type::SoaStorage(t)
            | Type::Soa(t) => self.has_callback(t, seen),
            Type::Map { key, value } => {
                self.has_callback(key, seen) || self.has_callback(value, seen)
            }
            Type::Struct(_) | Type::Record(..) | Type::Anonymous(_) => self
                .r
                .record_fields(typ)
                .iter()
                .any(|(_, t)| self.has_callback(t, seen)),
            Type::Union(..) => self
                .r
                .union_variants(typ)
                .iter()
                .any(|t| self.has_callback(t, seen)),
            _ => false,
        }
    }
    fn impure_storage(&mut self, typ: &Type, seen: &mut HashSet<Type>) -> bool {
        if !seen.insert(typ.clone()) {
            return false;
        }
        match typ {
            Type::Procedure(_) => true,
            Type::Function(sig, captures) => {
                let bindings = captures.iter().cloned().collect();
                ["params", "results"]
                    .into_iter()
                    .flat_map(|role| self.r.fields(*sig, role))
                    .collect::<Vec<_>>()
                    .iter()
                    .filter_map(|(_, r)| *r)
                    .any(|r| {
                        let typ = self.r.specialized_type(r, &bindings);
                        self.impure_storage(&typ, seen)
                    })
            }
            Type::Nominal(_, t)
            | Type::Pointer(t)
            | Type::Slice(t)
            | Type::Dynamic(t)
            | Type::Array { elem: t, .. }
            | Type::Fixed { elem: t, .. }
            | Type::SoaStorage(t)
            | Type::Soa(t) => self.impure_storage(t, seen),
            Type::Struct(_) | Type::Record(..) | Type::Anonymous(_) => self
                .r
                .record_fields(typ)
                .iter()
                .any(|(name, t)| name != "_" && self.impure_storage(t, seen)),
            Type::Union(..) => self
                .r
                .union_variants(typ)
                .iter()
                .any(|t| self.impure_storage(t, seen)),
            _ => false,
        }
    }
    fn compatible_inner(&mut self, expected: &Type, actual: &Type, invariant: bool) -> bool {
        match (expected, actual) {
            (Type::Nominal(_, base), other) => return self.compatible(base, other, invariant),
            (other, Type::Nominal(_, base)) => return self.compatible(other, base, invariant),
            _ => {}
        }
        if !self.has_callback(expected, &mut HashSet::new())
            && !self.has_callback(actual, &mut HashSet::new())
        {
            return true;
        }
        if let Some((es, eb)) = self.signature(expected) {
            let Some((as_, mut ab)) = self.signature(actual) else {
                return false;
            };
            if expected.pure_callable() && !actual.pure_callable()
                || invariant && expected.pure_callable() != actual.pure_callable()
            {
                return false;
            }
            let ep = self.r.fields(es, "params");
            let ap = self.r.fields(as_, "params");
            let input_types = ep
                .iter()
                .map(|(_, r)| {
                    r.map(|r| self.r.specialized_type(r, &eb))
                        .unwrap_or(Type::Unknown)
                })
                .collect::<Vec<_>>();
            ab.extend(self.r.parameter_bindings(as_, &input_types));
            for role in ["params", "results"] {
                let ef = self.r.fields(es, role);
                let af = if role == "params" {
                    ap.clone()
                } else {
                    self.r.fields(as_, role)
                };
                if ef.len() != af.len() {
                    continue;
                } // Odin owns signature/arity diagnostics.
                for ((_, e), (_, a)) in ef.into_iter().zip(af) {
                    if let (Some(e), Some(a)) = (e, a) {
                        let e = self.r.specialized_type(e, &eb);
                        let a = self.r.specialized_type(a, &ab);
                        if !self.compatible(&e, &a, true) {
                            return false;
                        }
                    }
                }
            }
            return true;
        }
        match (expected, actual) {
            (Type::Pointer(e), Type::Pointer(a))
            | (Type::Slice(e), Type::Slice(a))
            | (Type::Dynamic(e), Type::Dynamic(a))
            | (Type::Array { elem: e, .. }, Type::Array { elem: a, .. })
            | (Type::Fixed { elem: e, .. }, Type::Fixed { elem: a, .. })
            | (Type::SoaStorage(e), Type::SoaStorage(a))
            | (Type::Soa(e), Type::Soa(a)) => self.compatible(e, a, true),
            (Type::Map { key: e, value: ev }, Type::Map { key: a, value: av }) => {
                self.compatible(e, a, true) && self.compatible(ev, av, true)
            }
            (
                Type::Struct(_) | Type::Record(..) | Type::Anonymous(_),
                Type::Struct(_) | Type::Record(..) | Type::Anonymous(_),
            ) => {
                let ef = self.r.record_fields(expected);
                let af = self.r.record_fields(actual);
                ef.len() == af.len()
                    && ef
                        .iter()
                        .zip(&af)
                        .all(|((_, e), (_, a))| self.compatible(e, a, true))
            }
            (Type::Union(..), Type::Union(..)) => {
                let e = self.r.union_variants(expected);
                let a = self.r.union_variants(actual);
                e.iter().zip(&a).all(|(e, a)| self.compatible(e, a, true))
            }
            (Type::Union(..), a) => self
                .r
                .union_variants(expected)
                .iter()
                .any(|e| self.compatible(e, a, invariant)),
            (Type::Scalar { name, .. }, Type::Function(..)) if name == "any" => true,
            _ => false,
        }
    }
    fn assign(&mut self, at: Ref, expected: &Type, context: &Substitutions) -> Result<()> {
        let source = &self.r.sources[at.file];
        let kind = source.node(at.node).kind.as_str();
        if matches!(expected,Type::Scalar{name,..} if name=="typeid") && self.r.denotes_type(at) {
            return Ok(());
        }
        if source.text(at.node) == "nil" {
            return Ok(());
        }
        if kind == "Paren" || kind == "AutoCast" {
            if let Some(expr) = self.role(at, "expr") {
                return self.assign(expr, expected, context);
            }
        }
        if kind == "Ternary" || kind == "WhenExpr" {
            if kind == "WhenExpr" {
                if let Some(value) = self
                    .role(at, "cond")
                    .and_then(|cond| self.r.context_condition(cond, context))
                {
                    if let Some(branch) = self.role(at, if value { "x" } else { "y" }) {
                        return self.assign(branch, expected, context);
                    }
                }
            }
            if kind == "WhenExpr" && !self.final_check {
                self.deferred = true;
                return Ok(());
            }
            for role in ["x", "y"] {
                if let Some(value) = self.role(at, role) {
                    self.assign(value, expected, context)?;
                }
            }
            return Ok(());
        }
        if kind == "Unsafe" && self.has_callback(expected, &mut HashSet::new()) {
            return Err(self.fail(
                at,
                "Uninitialized storage cannot establish a pure fn callback contract.",
            ));
        }
        if kind == "Compound" {
            let fields = self.r.record_fields(expected);
            for (index, elem) in self.group(at, "elems").into_iter().enumerate() {
                let (value, typ) = if let Some(field) = self.role(elem, "field") {
                    let name = self.r.sources[field.file].text(field.node);
                    (
                        self.role(elem, "value").unwrap_or(elem),
                        fields
                            .iter()
                            .find(|(n, _)| n == name)
                            .map(|(_, t)| t.clone())
                            .or_else(|| expected.element().cloned()),
                    )
                } else {
                    (
                        elem,
                        expected
                            .element()
                            .cloned()
                            .or_else(|| fields.get(index).map(|(_, t)| t.clone())),
                    )
                };
                if let Some(typ) = typ {
                    self.assign(value, &typ, context)?;
                }
            }
        }
        let actual = self.r.specialized_type(at, context);
        if matches!(actual, Type::Unknown) && self.has_callback(expected, &mut HashSet::new()) {
            if kind == "Compound" {
                return Ok(());
            } // contextual elements were checked above
            if let Some((signature, bindings)) = self.signature(expected) {
                let inputs = self
                    .r
                    .fields(signature, "params")
                    .into_iter()
                    .map(|(_, t)| {
                        t.map(|t| self.r.specialized_type(t, &bindings))
                            .unwrap_or(Type::Unknown)
                    })
                    .collect::<Vec<_>>();
                let mut selected = false;
                for candidate in self.r.candidates(at) {
                    let Some(sig) = self.role(candidate, "type") else {
                        continue;
                    };
                    let fields = self.r.fields(sig, "params");
                    if fields.len() != inputs.len() {
                        continue;
                    }
                    let mut captures = context.clone();
                    captures.extend(self.r.parameter_bindings(sig, &inputs));
                    if !fields.iter().zip(&inputs).all(|((_, annotation), input)| {
                        annotation.is_none_or(|annotation| {
                            self.r.shape_score(annotation, input, &captures).is_some()
                        })
                    }) {
                        continue;
                    }
                    selected = true;
                    let typ = self.r.callable_type(sig, &captures);
                    if !self.compatible(expected, &typ, false) {
                        return Err(self.fail(at,"Overloaded value does not preserve the pure fn callback contract; the matching implementation must be fn."));
                    }
                    self.walk(candidate, &captures)?;
                }
                if selected {
                    return Ok(());
                }
            }
            let generic = self.r.sources[at.file]
                .ancestors(at.node)
                .find(|id| self.r.sources[at.file].node(*id).kind == "Procedure")
                .is_some_and(|node| {
                    !crate::specialization::parameters(Ref { node, ..at }, self.r).is_empty()
                });
            if !generic || !context.is_empty() {
                return Err(self.fail(
                    at,
                    "Cannot verify this pure fn callback contract from an unresolved value type.",
                ));
            }
        }
        if let (Some((expected_signature, expected_context)), Some((_, captures))) =
            (self.signature(expected), self.signature(&actual))
        {
            let inputs = self
                .r
                .fields(expected_signature, "params")
                .into_iter()
                .map(|(_, t)| {
                    t.map(|t| self.r.specialized_type(t, &expected_context))
                        .unwrap_or(Type::Unknown)
                })
                .collect::<Vec<_>>();
            for procedure in self.r.candidates(at) {
                if self.r.sources[procedure.file].node(procedure.node).kind != "Procedure" {
                    continue;
                }
                if let Some(signature) = self.role(procedure, "type") {
                    let mut bindings = context.clone();
                    bindings.extend(captures.clone());
                    bindings.extend(self.r.parameter_bindings(signature, &inputs));
                    self.walk(procedure, &bindings)?;
                }
            }
        }
        if !self.compatible(expected, &actual, false) {
            return Err(self.fail(at,"Pure fn callback contract does not match: use a fn value with matching nested callback contracts. An ordinary proc value cannot be converted to fn."));
        }
        Ok(())
    }
    fn walk(&mut self, at: Ref, context: &Substitutions) -> Result<()> {
        if !self.seen.insert((at, context.clone())) {
            return Ok(());
        }
        if self.remaining == 0 {
            return Err(self.fail(
                at,
                "Pure callback verification exceeded its bounded specialization budget.",
            ));
        }
        self.remaining -= 1;
        let source = &self.r.sources[at.file];
        let node = source.node(at.node);
        let kind = node.kind.clone();
        if kind == "When" || kind == "WhenExpr" {
            if let Some(value) = self
                .role(at, "cond")
                .and_then(|cond| self.r.context_condition(cond, context))
            {
                for role in if kind == "When" {
                    [if value { "body" } else { "else" }, "cond"]
                } else {
                    [if value { "x" } else { "y" }, "cond"]
                } {
                    if let Some(branch) = self.role(at, role) {
                        self.walk(branch, context)?;
                    }
                }
                return Ok(());
            }
            if !self.final_check {
                self.deferred = true;
                return Ok(());
            }
            let generic = source
                .ancestors(at.node)
                .find(|id| source.node(*id).kind == "Procedure")
                .is_some_and(|node| {
                    !crate::specialization::parameters(Ref { node, ..at }, self.r).is_empty()
                });
            if generic && context.is_empty() {
                return Ok(());
            }
            // No native-only condition may silently erase an unchecked effect
            // contract. Concrete unresolved conditions require both arms safe.
            for role in ["cond", "body", "else", "x", "y"] {
                if let Some(branch) = self.role(at, role) {
                    self.walk(branch, context)?;
                }
            }
            return Ok(());
        }
        if kind == "StructType"
            && node.op == "raw_union"
            && self.has_callback(&Type::Struct(at), &mut HashSet::new())
        {
            return Err(self.fail(at,"Raw unions cannot preserve a pure fn callback contract; use a struct or tagged union."));
        }
        if kind == "Procedure" && node.data["pure"] != true && self.role(at, "body").is_none() {
            if let Some(sig) = self.role(at, "type") {
                let typ = self.r.type_of(sig);
                if self.has_callback(&typ, &mut HashSet::new()) {
                    return Err(self.fail(at,"Foreign or bodyless procedures cannot promise a pure fn callback contract."));
                }
            }
        }
        if kind == "ProcedureType" && node.data["pure_type"] == true {
            if !matches!(
                node.data["calling_convention"].as_str(),
                None | Some("") | Some("odin")
            ) {
                return Err(self.fail(
                    at,
                    "Pure fn callback types require the Odin calling convention.",
                ));
            }
        }
        if kind == "Procedure" && node.data["pure"] == true && !context.is_empty() {
            if let Some(signature) = self.role(at, "type") {
                for role in ["params", "results"] {
                    for (_, annotation) in self.r.fields(signature, role) {
                        if let Some(annotation) = annotation {
                            let typ = self.r.specialized_type(annotation, context);
                            if self.impure_storage(&typ, &mut HashSet::new()) {
                                return Err(self.fail(annotation,"Unsupported pure value type: ordinary procedure values are not pure inputs/results; use a fn callback type."));
                            }
                        }
                    }
                }
            }
        }
        if kind == "Declaration" || kind == "Field" {
            if let Some(annotation) = self.role(at, "type") {
                let expected = self.r.specialized_type(annotation, context);
                let values = if kind == "Declaration" {
                    self.group(at, "values")
                } else {
                    self.role(at, "default").into_iter().collect()
                };
                let names = self.group(at, "names");
                if kind == "Declaration" && names.len() > 1 && values.len() == 1 {
                    for index in 0..names.len() {
                        let actual = self.r.specialized_value_result(
                            values[0],
                            index,
                            context,
                            &mut HashSet::new(),
                        );
                        if !self.compatible(&expected, &actual, false) {
                            return Err(self.fail(values[0], "Multiple-result declaration does not preserve the pure fn callback contract."));
                        }
                    }
                } else {
                    for value in values {
                        self.assign(value, &expected, context)?;
                    }
                }
            }
        }
        if kind == "Assignment" {
            let lhs = self.group(at, "lhs");
            let rhs = self.group(at, "rhs");
            if lhs.len() == rhs.len() {
                for (l, r) in lhs.into_iter().zip(rhs) {
                    let expected = self.r.specialized_type(l, context);
                    self.assign(r, &expected, context)?;
                }
            } else if rhs.len() == 1 {
                for (index, l) in lhs.into_iter().enumerate() {
                    let expected = self.r.specialized_type(l, context);
                    let actual = self.r.specialized_value_result(
                        rhs[0],
                        index,
                        context,
                        &mut HashSet::new(),
                    );
                    if !self.compatible(&expected, &actual, false) {
                        return Err(self.fail(rhs[0],"Multiple-result assignment does not preserve the pure fn callback contract."));
                    }
                }
            }
        }
        if kind == "Return" {
            if let Some(proc) = source
                .ancestors(at.node)
                .find(|id| source.node(*id).kind == "Procedure")
            {
                if let Some(sig) = source.role(proc, "type") {
                    let fields = self.r.fields(Ref { node: sig, ..at }, "results");
                    let values = self.group(at, "values");
                    if values.len() == 1 && fields.len() > 1 {
                        for (index, (_, annotation)) in fields.into_iter().enumerate() {
                            if let Some(annotation) = annotation {
                                let expected = self.r.specialized_type(annotation, context);
                                let actual = self.r.specialized_value_result(
                                    values[0],
                                    index,
                                    context,
                                    &mut HashSet::new(),
                                );
                                if !self.compatible(&expected, &actual, false) {
                                    return Err(self.fail(values[0],"Forwarded results do not preserve the pure fn callback contract."));
                                }
                            }
                        }
                    } else {
                        for ((_, annotation), value) in fields.into_iter().zip(values) {
                            if let Some(annotation) = annotation {
                                let typ = self.r.specialized_type(annotation, context);
                                self.assign(value, &typ, context)?;
                            }
                        }
                    }
                }
            }
        }
        if kind == "Cast" || kind == "TypeAssertion" {
            if let (Some(target), Some(value)) = (self.role(at, "type"), self.role(at, "expr")) {
                let expected = self.r.specialized_type(target, context);
                self.assign(value, &expected, context)?;
            }
        }
        if kind == "Compound" {
            let typ = self.r.specialized_type(at, context);
            self.assign(at, &typ, context)?;
        }
        if kind == "Call" {
            if self.r.denotes_type(at) {
                let typ = self.r.specialized_type(at, context);
                if let Type::Struct(owner) | Type::Record(owner, _) = &typ {
                    if self.r.sources[owner.file].node(owner.node).op == "raw_union"
                        && self.has_callback(&typ, &mut HashSet::new())
                    {
                        return Err(self.fail(at, "Raw unions cannot preserve a pure fn callback contract; use a struct or tagged union."));
                    }
                }
            }
            if let Some(callee) = self.role(at, "callee") {
                if self.r.native_builtin_alias(callee, "append") {
                    let args = self.group(at, "args");
                    if let Some(target) = args.first() {
                        let mut typ = self.r.specialized_type(*target, context);
                        while let Type::Pointer(inner) = typ {
                            typ = *inner;
                        }
                        if let Some(elem) = typ.element().cloned() {
                            for arg in args.iter().skip(1) {
                                let source = &self.r.sources[arg.file];
                                let spread =
                                    source.node(at.node).spans.get("ellipsis").is_some_and(
                                        |span| source.node(arg.node).span.start >= span.end,
                                    );
                                if spread {
                                    self.assign(
                                        *arg,
                                        &Type::Slice(Box::new(elem.clone())),
                                        context,
                                    )?;
                                } else {
                                    self.assign(*arg, &elem, context)?;
                                }
                            }
                        }
                    }
                }
                if self.r.native_builtin_alias(callee, "copy") {
                    let args = self.group(at, "args");
                    if let [dst, src] = args.as_slice() {
                        let expected = self.r.specialized_type(*dst, context);
                        self.assign(*src, &expected, context)?;
                    }
                }
                if self.r.denotes_type(callee) && !self.r.denotes_type(at) {
                    let typ = self.r.specialized_type(callee, context);
                    for value in self.group(at, "args") {
                        self.assign(value, &typ, context)?;
                    }
                }
            }
            let plans = self.r.call_plans(at, context);
            if let [plan] = plans.as_slice() {
                let fields = self.r.fields(plan.signature, "params");
                for ((_, annotation), args) in fields.into_iter().zip(&plan.arguments) {
                    if let Some(annotation) = annotation {
                        let typ = self.r.specialized_type(annotation, &plan.bindings);
                        for arg in args {
                            let value = self.role(*arg, "value").unwrap_or(*arg);
                            self.assign(value, &typ, context)?;
                        }
                    }
                }
                if self.r.sources[plan.procedure.file]
                    .node(plan.procedure.node)
                    .kind
                    == "Procedure"
                    && !self.r.sources[plan.procedure.file].shared_native
                {
                    self.walk(plan.procedure, &plan.bindings)?;
                }
            }
        }
        let children = self.r.sources[at.file].node(at.node).children.to_vec();
        for node in children {
            self.walk(Ref { node, ..at }, context)?;
        }
        Ok(())
    }
}
