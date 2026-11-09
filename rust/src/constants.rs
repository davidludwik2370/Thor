//! Compile-time arguments carry values, rather than just their scalar types.
use super::*;
impl Type {
    pub fn resolved(&self) -> bool {
        match self {
            Self::Unknown | Self::Generic => false,
            Self::Record(_, args) => args.iter().all(|(_, typ)| typ.resolved()),
            Self::Array { elem, length } | Self::Fixed { elem, length } => {
                length.is_some() && elem.resolved()
            }
            Self::Nominal(_, elem)
            | Self::Slice(elem)
            | Self::Dynamic(elem)
            | Self::Pointer(elem)
            | Self::Soa(elem)
            | Self::Set(elem) => elem.resolved(),
            Self::Map { key, value } => key.resolved() && value.resolved(),
            _ => true,
        }
    }
}
impl Resolver<'_> {
    pub fn context_integer(&self, r: Ref, bindings: &Substitutions) -> Option<i128> {
        match self.constant_value(r, bindings, None)? {
            Constant::Integer(value) => Some(value),
            _ => None,
        }
    }
    pub fn constant_value(
        &self,
        r: Ref,
        bindings: &Substitutions,
        expected: Option<&Type>,
    ) -> Option<Constant> {
        self.constant_value_seen(r, bindings, expected, &mut HashSet::new())
    }
    fn constant_value_seen(
        &self,
        r: Ref,
        bindings: &Substitutions,
        expected: Option<&Type>,
        seen: &mut HashSet<Ref>,
    ) -> Option<Constant> {
        if !seen.insert(r) || seen.len() > 64 {
            return None;
        }
        let s = &self.sources[r.file];
        let n = s.node(r.node);
        let role = |name| s.role(r.node, name).map(|node| Ref { file: r.file, node });
        if matches!(n.kind.as_str(), "Ident" | "PolyType") {
            let name = if n.kind == "PolyType" {
                s.names.get(n.name)
            } else {
                s.text(r.node)
            };
            if let Some(Type::Constant(value)) = bindings.get(name.trim_start_matches('$')) {
                return Some(value.clone());
            }
            if let Some(index) = self.symbol(r) {
                let symbol = &self.symbols[index];
                if symbol.constant {
                    if let Some(value) = symbol.value {
                        return self.constant_value_seen(
                            Ref {
                                file: symbol.reference.file,
                                node: value,
                            },
                            bindings,
                            expected,
                            seen,
                        );
                    }
                }
            }
        }
        if let Some(value) = self.constant_int(r) {
            return Some(Constant::Integer(value));
        }
        match n.kind.as_str() {
            "Call"
                if role("callee").is_some_and(|callee| self.native_builtin(callee, "size_of")) =>
            {
                let args = s.group(r.node, "args");
                if args.len() != 1 {
                    return None;
                }
                let arg = Ref {
                    file: r.file,
                    node: args[0],
                };
                let typ = bindings
                    .get(s.text(args[0]))
                    .cloned()
                    .or_else(|| self.primitive(arg))?;
                self.simple_size(&typ).map(Constant::Integer)
            }
            "Ident" if s.text(r.node) == "true" => Some(Constant::Boolean(true)),
            "Ident" if s.text(r.node) == "false" => Some(Constant::Boolean(false)),
            "Literal" if s.text(r.node).starts_with('"') => crate::lower::unquote(s.text(r.node))
                .ok()
                .map(Constant::Text),
            "Paren" => self.constant_value_seen(role("expr")?, bindings, expected, seen),
            "Unary" => {
                let value = self.constant_value_seen(role("expr")?, bindings, expected, seen)?;
                match (n.op.as_str(), value) {
                    ("-", Constant::Integer(v)) => v.checked_neg().map(Constant::Integer),
                    ("+", Constant::Integer(v)) => Some(Constant::Integer(v)),
                    ("!", Constant::Boolean(v)) => Some(Constant::Boolean(!v)),
                    _ => None,
                }
            }
            "Binary" => {
                let left =
                    self.constant_value_seen(role("left")?, bindings, expected, &mut seen.clone())?;
                let right = self.constant_value_seen(
                    role("right")?,
                    bindings,
                    expected,
                    &mut seen.clone(),
                )?;
                match (left, right) {
                    (Constant::Integer(a), Constant::Integer(b)) => {
                        let result = match n.op.as_str() {
                            "+" => a.checked_add(b),
                            "-" => a.checked_sub(b),
                            "*" => a.checked_mul(b),
                            "/" => a.checked_div(b),
                            "%" => a.checked_rem(b),
                            _ => None,
                        };
                        if let Some(value) = result {
                            return Some(Constant::Integer(value));
                        }
                        let value = match n.op.as_str() {
                            "==" => a == b,
                            "!=" => a != b,
                            "<" => a < b,
                            "<=" => a <= b,
                            ">" => a > b,
                            ">=" => a >= b,
                            _ => return None,
                        };
                        Some(Constant::Boolean(value))
                    }
                    (Constant::Boolean(a), Constant::Boolean(b)) => {
                        Some(Constant::Boolean(match n.op.as_str() {
                            "&&" => a && b,
                            "||" => a || b,
                            "==" => a == b,
                            "!=" => a != b,
                            _ => return None,
                        }))
                    }
                    (a, b) if n.op == "==" || n.op == "!=" => {
                        Some(Constant::Boolean((a == b) == (n.op == "==")))
                    }
                    _ => None,
                }
            }
            "ImplicitSelector" | "Selector" => {
                let owner = match expected {
                    Some(Type::Enum(owner)) => *owner,
                    _ => return None,
                };
                if n.kind == "Selector" && !self.denotes_type(role("expr")?) {
                    return None;
                }
                let field = role("field")
                    .map(|r| self.sources[r.file].text(r.node))
                    .unwrap_or(s.text(r.node).trim_start_matches('.'));
                self.enum_ordinal(owner, field, bindings)
                    .map(|value| Constant::Enum(owner, value))
            }
            _ => None,
        }
    }
    fn simple_size(&self, typ: &Type) -> Option<i128> {
        let pointer = match self
            .flags
            .iter()
            .find_map(|flag| flag.strip_prefix("-target:"))
            .and_then(|target| target.split_once('_'))
        {
            Some((_, "i386" | "arm32" | "wasm32")) => 4,
            Some((_, "amd64" | "arm64" | "riscv64" | "wasm64" | "spirv64")) => 8,
            None => std::mem::size_of::<usize>() as i128,
            _ => return None,
        };
        match typ {
            Type::Nominal(_, base) => self.simple_size(base),
            Type::Pointer(_) | Type::Procedure(_) => Some(pointer),
            Type::Slice(_) => Some(2 * pointer),
            Type::String { name, .. } => Some(if name.starts_with("cstring") {
                pointer
            } else {
                2 * pointer
            }),
            Type::Scalar { name, .. } => match name.as_str() {
                "bool" | "byte" => Some(1),
                "rune" => Some(4),
                "int" | "uint" | "uintptr" | "rawptr" | "typeid" => Some(pointer),
                "any" => Some(2 * pointer),
                _ => name
                    .trim_start_matches(['i', 'u', 'f', 'b'])
                    .trim_end_matches("le")
                    .trim_end_matches("be")
                    .parse::<i128>()
                    .ok()
                    .filter(|bits| [8, 16, 32, 64, 128].contains(bits))
                    .map(|bits| bits / 8),
            },
            Type::Array {
                elem,
                length: Some(length),
            } => self.simple_size(elem)?.checked_mul(*length),
            _ => None,
        }
    }
    fn enum_ordinal(&self, owner: Ref, field: &str, context: &Substitutions) -> Option<i128> {
        let source = &self.sources[owner.file];
        let mut bindings = context.clone();
        let mut next = Some(0i128);
        for member in source.group(owner.node, "members") {
            let named = source.role(*member, "field").unwrap_or(*member);
            let name = source.text(named);
            let value = if let Some(value) = source.role(*member, "value") {
                self.context_integer(
                    Ref {
                        file: owner.file,
                        node: value,
                    },
                    &bindings,
                )
            } else {
                next
            };
            if name == field {
                return value;
            }
            if let Some(value) = value {
                bindings.insert(name.into(), Type::Constant(Constant::Integer(value)));
            }
            next = value.and_then(|value| value.checked_add(1));
        }
        None
    }
    pub fn constant_parameter_bindings(
        &self,
        sig: Ref,
        args: &[Ref],
        context: &Substitutions,
    ) -> Substitutions {
        let source = &self.sources[sig.file];
        let mut bindings = Substitutions::new();
        let params = source.role(sig.node, "params");
        let names = params
            .into_iter()
            .flat_map(|params| source.node(params).children.iter())
            .flat_map(|field| source.group(*field, "names"))
            .copied()
            .collect::<Vec<_>>();
        for (name, arg) in names.into_iter().zip(args) {
            if source.node(name).kind != "PolyType" {
                continue;
            }
            let key = source.text(name).trim_start_matches('$');
            let Some(field) = source.node(name).parent else {
                continue;
            };
            if source
                .role(field, "type")
                .is_some_and(|annotation| source.node(annotation).kind == "TypeidType")
            {
                continue;
            }
            let arg_source = &self.sources[arg.file];
            let value = arg_source
                .role(arg.node, "value")
                .map(|node| Ref {
                    file: arg.file,
                    node,
                })
                .unwrap_or(*arg);
            bindings.insert(
                key.into(),
                self.constant_value(value, context, None)
                    .map(Type::Constant)
                    .unwrap_or(Type::Unknown),
            );
        }
        bindings
    }
}
