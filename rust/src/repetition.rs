//! Native constant string repetition, following repetition.render.
use super::*;
const LIMIT: u128 = 16 * 1024 * 1024;

fn empty(value: &str, type_of: &str, default: &str, string: &str) -> String {
    let typ = format!("{type_of}({default}({value}))");
    format!("(({value})[:0] when {typ}=={string} else {typ}(\"\"))")
}
impl Lower<'_, '_> {
    pub(super) fn repeat(
        &mut self,
        id: NodeId,
        mut text: NodeId,
        mut count: NodeId,
    ) -> Result<Code> {
        if self.typ(count).is_string() {
            std::mem::swap(&mut text, &mut count);
        }
        if self.resolver.runtime_operand(self.reference(text))
            || self.resolver.runtime_operand(self.reference(count))
        {
            return Ok(self.error_token(id,"String repetition requires compile-time constant operands, like string concatenation (+)."));
        }
        let scope = self.helper_scope(id);
        let generic = scope == NodeId(0)
            && [text, count].into_iter().any(|root| {
                std::iter::once(root)
                    .chain(self.source.descendants(root))
                    .any(|node| {
                        self.resolver
                            .symbol(self.reference(node))
                            .is_some_and(|index| {
                                let symbol = &self.resolver.symbols[index];
                                let source = &self.resolver.sources[symbol.reference.file];
                                let declaration = source.node(symbol.reference.node);
                                declaration.kind == "Field"
                                    && source
                                        .group(symbol.reference.node, "names")
                                        .iter()
                                        .any(|name| source.node(*name).kind == "PolyType")
                                    || declaration.kind == "PolyType"
                                        && declaration.parent.is_some_and(|parent| {
                                            source.node(parent).kind == "Field"
                                                && source
                                                    .group(parent, "names")
                                                    .contains(&symbol.reference.node)
                                        })
                            })
                    })
            });
        self.meta = true;
        self.default = true;
        let meta = format!("{}meta", self.prefix);
        let default = format!("{}default_value", self.prefix);
        let type_of = self.native("type_of");
        let string = self.native("string");
        let len = self.native("len");
        let known = self.resolver.constant_int(self.reference(count));
        if generic {
            let Some(known) = known.filter(|known| (0..=128).contains(known)) else {
                return Ok(self.error_token(id,"String repetition in a generic signature or type expression requires a fixed nonnegative integer count through 128; place a variable-count repetition in the procedure body."));
            };
            let value = format!("({} + \"\")", self.render(text, None, true)?.finish().text);
            let zero = empty(&value, &type_of, &default, &string);
            fn concatenate(n: usize, value: &str, zero: &str) -> String {
                match n {
                    0 => zero.into(),
                    1 => value.into(),
                    _ => format!(
                        "({} + {})",
                        concatenate(n / 2, value, zero),
                        concatenate(n - n / 2, value, zero)
                    ),
                }
            }
            let amount = self.render(count, None, true)?.finish().text;
            let integer = format!("{meta}.type_is_integer({type_of}({default}({amount})))");
            let allowed = if known == 0 {
                format!("{integer} && {len}({value})>=0")
            } else {
                format!("{integer} && {len}({value})<= {}", LIMIT / (known as u128))
            };
            let error=self.error_token(id,"String repetition requires an integer count and a result within the 16 MiB compile-time limit.").finish().text;
            return Ok(self.g(
                format!(
                    "({} when ({allowed}) else {error})",
                    concatenate(known as usize, &value, &zero)
                ),
                id,
            ));
        }
        let span = self.source.node(id).span;
        let name = format!("{}repeat_{}_{}", self.prefix, span.start, span.end);
        // End position orders nested repetitions before their local consumers.
        let key = (
            scope,
            self.prefix.clone(),
            format!("repeat_{:020}_{:020}", span.end, usize::MAX - span.start),
        );
        if self.helpers.contains_key(&key) {
            return Ok(self.g(name, id));
        }
        self.helpers.insert(key.clone(), self.g("", id));
        let base = format!("{name}_base");
        let amount = format!("{name}_count");
        let wide = format!("{name}_wide");
        let text_code = self.render(text, None, true)?.finish().text;
        let count_code = self.render(count, None, true)?.finish().text;
        let mut lines = vec![
            format!("{base} :: ({text_code}) + \"\""),
            format!("{amount} :: ({count_code})"),
        ];
        let integer = format!("{meta}.type_is_integer({type_of}({default}({amount})))");
        let integer_error = self
            .error_token(
                id,
                "String repetition requires an integer compile-time count.",
            )
            .finish()
            .text;
        let negative_error = self
            .error_token(count, "String repetition requires a nonnegative count.")
            .finish()
            .text;
        let size_error = self
            .error_token(
                id,
                "String repetition result exceeds the 16 MiB compile-time limit.",
            )
            .finish()
            .text;
        let u128 = self.native("u128");
        let max = self.native("max");
        let allowed =
            format!("{len}({base})==0 || {wide}<={u128}({LIMIT})/{u128}({max}({len}({base}),1))");
        lines.extend([
            format!("#assert({integer},{})", json!(integer_error)),
            format!("when {integer} {{"),
            format!("#assert({amount}>=0,{})", json!(negative_error)),
            format!("when {amount}>=0 {{"),
            format!("{wide} :: {u128}({amount})"),
            format!("#assert({allowed},{})", json!(size_error)),
            format!("when {allowed} {{"),
        ]);
        let bits = known
            .filter(|known| *known >= 0)
            .map_or(128, |known| (128 - (known as u128).leading_zeros()).max(1));
        let zero = empty(&base, &type_of, &default, &string);
        let mut previous_chunk = base.clone();
        let mut previous_result = zero.clone();
        for bit in 0..bits {
            let chunk = format!("{name}_chunk_{bit}");
            let result = format!("{name}_result_{bit}");
            if bit == 0 {
                lines.push(format!("{chunk} :: {base}"));
            } else {
                lines.push(format!("{chunk} :: ({previous_chunk}+{previous_chunk}) when ({wide}>>{bit})!=0 else {zero}"));
            }
            lines.push(format!("{result} :: ({previous_result}+{chunk}) when ({wide}&({u128}(1)<<{bit}))!=0 else {previous_result}"));
            previous_chunk = chunk;
            previous_result = result;
        }
        lines.push(format!("{name} :: {previous_result}"));
        for _ in 0..3 {
            lines.extend(["} else {".into(), format!("{name} :: {zero}"), "}".into()]);
        }
        let mut child = id;
        for parent in self.source.ancestors(id) {
            if self.source.node(parent).kind == "WhenExpr"
                && [self.source.role(parent, "x"), self.source.role(parent, "y")]
                    .contains(&Some(child))
            {
                let cond = self
                    .render(self.role(parent, "cond")?, None, true)?
                    .finish()
                    .text;
                let cond = if self.source.role(parent, "y") == Some(child) {
                    format!("!({cond})")
                } else {
                    cond
                };
                lines.splice(0..0, [format!("when {cond} {{")]);
                lines.extend(["} else {".into(), format!("{name} :: \"\""), "}".into()]);
            } else if self.source.node(parent).kind == "CompileSwitchExpr" {
                let cases = self.source.node(parent).data["cases"]
                    .as_array()
                    .cloned()
                    .unwrap_or_default();
                if let Some(index) = cases.iter().position(|case| {
                    Span::from_wire(&case["result"]).is_some_and(|arm| arm.contains(span))
                }) {
                    let mut condition =
                        self.evaluation_case_condition(parent, index)?.finish().text;
                    let before = if cases[index]["matches"]
                        .as_array()
                        .is_some_and(|matches| !matches.is_empty())
                    {
                        index
                    } else {
                        cases.len()
                    };
                    for earlier in (0..before).rev().filter(|earlier| {
                        cases[*earlier]["matches"]
                            .as_array()
                            .is_some_and(|matches| !matches.is_empty())
                    }) {
                        let previous = self
                            .evaluation_case_condition(parent, earlier)?
                            .finish()
                            .text;
                        condition = format!("(false when ({previous}) else ({condition}))");
                    }
                    lines.splice(0..0, [format!("when {condition} {{")]);
                    lines.extend(["} else {".into(), format!("{name} :: \"\""), "}".into()]);
                }
            }
            child = parent;
        }
        let privacy = if scope == NodeId(0) {
            "@(private=\"file\")\n"
        } else {
            ""
        };
        let body = lines
            .iter()
            .map(|line| {
                format!(
                    "{}{line}",
                    if line.starts_with(&name) { privacy } else { "" }
                )
            })
            .collect::<Vec<_>>()
            .join("\n");
        self.helpers.remove(&key);
        self.helpers.insert(key, self.g(format!("\n{body}\n"), id));
        Ok(self.g(name, id).expansion(&self.source.path, span))
    }
}
