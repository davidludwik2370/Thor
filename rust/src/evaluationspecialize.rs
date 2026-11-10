//! Traverse user generic bodies in concrete lexical frames, following the
//! reference specialization order. Native overload ties remain unresolved.
use super::*;
use serde_json::json;
impl Scheduler<'_> {
    fn binding_condition(&mut self, typ: &Type) -> Result<String> {
        let at = match typ {
            Type::Nominal(at, _)
            | Type::Struct(at)
            | Type::Record(at, _)
            | Type::Enum(at)
            | Type::Union(at) => *at,
            _ => return Ok(String::new()),
        };
        let mut bindings = self.specializations.relevant(at, &self.resolver);
        if let Type::Record(_, captures) = typ {
            let context = captures.iter().cloned().collect();
            for (name, typ) in captures {
                let Some(index) = self.resolver.lookup(at, name) else {
                    continue;
                };
                let symbol = self.resolver.symbols[index].clone();
                if symbol.scope == at.node {
                    continue;
                }
                let (typ, value) = if let Type::Constant(value) = typ {
                    let typ = symbol
                        .annotation
                        .map(|node| {
                            self.resolver.specialized_type(
                                Ref {
                                    file: symbol.reference.file,
                                    node,
                                },
                                &context,
                            )
                        })
                        .unwrap_or(Type::scalar("int"));
                    let data = match value {
                        crate::semantics::Constant::Integer(value)
                        | crate::semantics::Constant::Enum(_, value) => {
                            json!({"kind":"integer","value":value.to_string()})
                        }
                        crate::semantics::Constant::Boolean(value) => {
                            json!({"kind":"bool","value":value})
                        }
                        crate::semantics::Constant::Text(value) => {
                            json!({"kind":"string","bytes":value.as_bytes().iter().map(|byte|format!("{byte:02x}")).collect::<String>()})
                        }
                    };
                    (typ, Some(data))
                } else {
                    (typ.clone(), None)
                };
                bindings.insert(
                    (symbol.reference, name.clone()),
                    Binding {
                        symbol: index,
                        typ,
                        value,
                        canonical: String::new(),
                        condition: String::new(),
                    },
                );
            }
        }
        if bindings.is_empty() {
            return Ok(String::new());
        }
        Specializations::predicate(
            &bindings,
            &mut TypeCode::new(at.file, Some(at)),
            &self.resolver,
        )
        .map_err(|error| self.fail(at, error.to_string()))
    }
    fn refresh_bindings(&mut self) {
        self.resolver.evaluated = self.results.clone();
        self.specializations.apply(&mut self.resolver);
        for (node, cases) in &self.cases {
            let (_, key) = self.key(*node);
            if let Some(case) = cases.get(&key).filter(|case| case.skipped.is_none()) {
                self.resolver.remember_evaluation(*node, case.value.clone());
            }
        }
    }
    pub(super) fn process_specialization(&mut self, call: Ref) -> Result<()> {
        if self.unbound_generic(call) {
            return Ok(());
        }
        let plans = self.resolver.call_plans(call, &Substitutions::new());
        let [plan] = plans.as_slice() else {
            return Ok(());
        };
        let procedure = plan.procedure;
        if self.kind(procedure) != "Procedure"
            || self.role(procedure, "body").is_none()
            || self.source(procedure).shared_native
            || self
                .source(procedure)
                .path
                .starts_with(self.project.options.root.join("runtime"))
        {
            return Ok(());
        }
        let parameters = crate::specialization::parameters(procedure, &self.resolver);
        if parameters.is_empty() || self.active(call, true)? != Some(true) {
            return Ok(());
        }
        let owners = self
            .source(procedure)
            .ancestors(procedure.node)
            .collect::<BTreeSet<_>>();
        let mut bindings: Bindings = self
            .specializations
            .bindings
            .iter()
            .filter(|((owner, _), binding)| {
                owner.file == procedure.file
                    && owners.contains(&self.resolver.symbols[binding.symbol].scope)
            })
            .map(|(key, binding)| (key.clone(), binding.clone()))
            .collect();
        let fields = self.resolver.fields(plan.signature, "params");
        for (name, index, value_slot) in parameters {
            let symbol = self.resolver.symbols[index].clone();
            let actual = plan.bindings.get(&name);
            let arg = fields
                .iter()
                .position(|(field, _)| field == &name)
                .and_then(|index| plan.arguments.get(index))
                .and_then(|args| args.first())
                .copied();
            let (typ, value) = if !value_slot {
                let Some(typ) = actual.filter(|typ| !typ.is_unknown()) else {
                    return Ok(());
                };
                (typ.clone(), None)
            } else {
                let typ = symbol
                    .annotation
                    .map(|node| {
                        self.resolver.specialized_type(
                            Ref {
                                file: procedure.file,
                                node,
                            },
                            &plan.bindings,
                        )
                    })
                    .unwrap_or_else(|| Type::Scalar {
                        name: "int".into(),
                        untyped: false,
                    });
                let data = if let Some(arg) = arg {
                    if !self.known(arg)? {
                        return Ok(());
                    }
                    if let Some(value) = self.constant_integer(arg) {
                        json!({"kind":"integer","value":value.to_string()})
                    } else {
                        match self
                            .resolver
                            .constant_value(arg, &Substitutions::new(), None)
                        {
                            Some(crate::semantics::Constant::Integer(value)) => {
                                json!({"kind":"integer","value":value.to_string()})
                            }
                            Some(crate::semantics::Constant::Boolean(value)) => {
                                json!({"kind":"bool","value":value})
                            }
                            _ => self.execute(arg, Mode::Value)?,
                        }
                    }
                } else if let Some(Type::Constant(crate::semantics::Constant::Integer(value))) =
                    actual
                {
                    json!({"kind":"integer","value":value.to_string()})
                } else {
                    return Ok(());
                };
                (typ, Some(data))
            };
            let condition = if value.is_none() {
                self.binding_condition(&typ)?
            } else {
                String::new()
            };
            bindings.insert(
                (symbol.reference, name),
                Binding {
                    symbol: index,
                    typ,
                    value,
                    canonical: String::new(),
                    condition,
                },
            );
        }
        let key = (
            procedure,
            Specializations::fingerprint(&bindings, &self.resolver),
        );
        if self.specializations.visited.contains(&key)
            || self.specializations.running.contains(&key)
        {
            return Ok(());
        }
        if self.specializations.running.len() > 128 {
            return Err(self.fail(call,"Compile-time specialization depth exceeds 128; check recursive constant parameters."));
        }
        self.specializations.running.insert(key.clone());
        let previous = self.specializations.bindings.clone();
        let saved = self
            .specializations
            .enter(bindings, Some(call), Some(previous));
        self.refresh_bindings();
        let result = (|| {
            for node in self.children(procedure) {
                if self.kind(node) == "Run" || self.automatic(node) {
                    self.evaluate(node)?;
                }
                if self.kind(node) == "Call" && !self.busy.contains(&self.key(node)) {
                    self.process_specialization(node)?;
                }
            }
            Ok(())
        })();
        self.specializations.restore(saved);
        self.refresh_bindings();
        self.specializations.running.remove(&key);
        if result.is_ok() {
            self.specializations.visited.insert(key);
        }
        result
    }
}
