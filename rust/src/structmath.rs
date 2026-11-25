//! Fixed, self-contained struct arithmetic. Runtime operands are evaluated once;
//! native constant expressions retain Odin's constant and overflow semantics.
use super::*;

#[derive(Clone)]
enum Shape {
    Leaf,
    Deferred(String, String),
    Record(Vec<(String, Shape)>),
    Array(usize, Box<Shape>),
}
fn base(mut typ: &Type) -> &Type {
    while let Type::Nominal(_, inner) = typ {
        typ = inner;
    }
    typ
}
fn unary(op: &str) -> bool {
    op == "~unary"
}
fn display_op(op: &str) -> &str {
    if unary(op) {
        "~"
    } else {
        op
    }
}
fn scalar(a: &str, b: &str, op: &str, power: &str) -> String {
    if unary(op) {
        format!("(~({a}))")
    } else if op == "**" {
        format!("{power}.power({a},{b})")
    } else {
        format!("(({a}) {op} ({b}))")
    }
}
fn record(typ: &Type) -> bool {
    typ.record_like()
}
impl Lower<'_, '_> {
    fn struct_error(&mut self, site: NodeId, message: &str) -> String {
        let token = self.register_error(site, message);
        self.expression_error_sites
            .insert(token.clone(), self.reference(site));
        token
    }
    fn struct_dispatch_condition(&mut self, typ: &str) -> String {
        let mut condition = format!("{}meta.type_is_struct({typ})", self.prefix);
        if self.big_enabled() {
            let big = self.module("big");
            condition.push_str(&format!(
                " && {typ}!={big}.Integer && {typ}!={big}.Rational"
            ));
        }
        // Map-backed Set values retain their existing logical operator dispatch.
        // Other records with that storage are ineligible too. Inspect the type
        // instead of importing the set runtime into otherwise native programs.
        let meta = format!("{}meta", self.prefix);
        condition.push_str(&format!(
            " && !({meta}.type_is_map({meta}.type_field_type({typ},\"table\")) when {meta}.type_has_field({typ},\"table\") else false)"
        ));
        condition
    }
    fn generic_struct_helper(&mut self, op: &str) -> String {
        let suffix = match op {
            "+" => "add",
            "-" => "sub",
            "*" => "mul",
            "/" => "div",
            "&" => "and",
            "|" => "or",
            "~" => "xor",
            "&~" => "and_not",
            "<<" => "shl",
            ">>" => "shr",
            "**" => "power",
            "~unary" => "complement",
            _ => unreachable!(),
        };
        let name = format!("{}struct_generic_{suffix}", self.prefix);
        let key = (NodeId(0), self.prefix.clone(), name.clone());
        if self.helpers.contains_key(&key) {
            return name;
        }
        let names = self
            .resolver
            .sources
            .iter()
            .flat_map(|s| {
                s.nodes
                    .iter()
                    .enumerate()
                    .filter(|(_, n)| n.kind == "StructType")
                    .filter_map(|(i, _)| s.role(NodeId(i), "fields"))
                    .flat_map(|fields| s.node(fields).children.iter())
                    .filter(|field| s.node(**field).kind == "Field")
                    .flat_map(|field| s.group(*field, "names"))
                    .map(|field| s.text(*field).to_string())
            })
            .filter(|name| name != "_")
            .collect::<std::collections::BTreeSet<_>>()
            .into_iter()
            .collect::<Vec<_>>();
        self.meta = true;
        self.builtin = true;
        let meta = format!("{}meta", self.prefix);
        let builtin = format!("{}builtin", self.prefix);
        let raw_layouts = self
            .resolver
            .sources
            .iter()
            .flat_map(|source| {
                source
                    .nodes
                    .iter()
                    .enumerate()
                    .filter(|(_, node)| node.kind == "StructType" && node.op == "raw_union")
                    .filter_map(|(index, _)| source.role(NodeId(index), "fields"))
                    .map(|fields| {
                        source
                            .node(fields)
                            .children
                            .iter()
                            .filter(|field| source.node(**field).kind == "Field")
                            .flat_map(|field| source.group(*field, "names"))
                            .map(|field| source.text(*field).to_string())
                            .collect::<Vec<_>>()
                    })
            })
            .filter(|fields| fields.iter().all(|field| field != "_"))
            .collect::<std::collections::BTreeSet<_>>();
        // Offset overlap detects ordinary multi-field raw unions. Structural
        // base-type checks also cover one-field and zero-sized raw unions,
        // without referring to a private or locally scoped type name.
        let raw_checks = raw_layouts.iter().map(|fields| {
            let predicate=fields.iter().map(|field|pureschema::direct_field(field,&meta))
                .collect::<Vec<_>>().join(" && ");
            let schema=fields.iter().map(|field|format!("{field}:{meta}.type_field_type(T,{})",json!(field)))
                .collect::<Vec<_>>().join(",");
            let check=format!("when {meta}.type_base_type(T)==struct #raw_union #align({builtin}.align_of(T)){{{schema}}}{{#panic(Site)}}\n");
            if predicate.is_empty(){check}else{format!("when {predicate}{{{check}}}\n")}
        }).collect::<String>();
        let count = pureschema::field_count(&names, &meta);
        let (padding_code, padding_count, _) = self.record_padding_count(&meta, &builtin, &name);
        let overlap = pureschema::overlap_count(&names, &meta, &builtin);
        let is_unary = unary(op);
        let power = if op == "**" {
            self.module("numeric")
        } else {
            String::new()
        };
        let args = if is_unary {
            "a:$T,$Site:string"
        } else {
            "a:$T,b:$U,$Site:string"
        };
        let check = if is_unary {
            String::new()
        } else {
            "when T!=U {#panic(Site)} else ".to_string()
        };
        let operands = |a: &str, b: &str| {
            if is_unary {
                format!("{a},Site")
            } else {
                format!("{a},{b},Site")
            }
        };
        let fields = names.iter().map(|field| {
            let call=format!("result.{field}={name}({})",operands(&format!("a.{field}"),&format!("b.{field}")));
            // Passing a bool alone through a procedure normalizes its byte.
            // Field operators must retain its native bit pattern, including
            // when a previous complement produced a noncanonical bool byte.
            let operation=if matches!(op,"~unary"|"&"|"|"|"~") {
                let direct=scalar(&format!("a.{field}"),&format!("b.{field}"),op,&power);
                format!("when {meta}.type_is_boolean({builtin}.type_of(a.{field})) {{result.{field}={direct}}} else {{{call}}}")
            } else {call};
            format!("when {} {{{operation}}}\n",pureschema::direct_field(field,&meta))
        }).collect::<String>();
        let kinds: &[&str] = match op {
            "**" => &["integer", "float"],
            "<<" | ">>" => &["integer"],
            "&~" => &["integer", "enum", "bit_set", "simd_vector"],
            "~unary" => &["integer", "boolean", "bit_set", "simd_vector"],
            "&" | "|" | "~" => &["integer", "boolean", "enum", "bit_set", "simd_vector"],
            _ => &[
                "integer",
                "float",
                "complex",
                "quaternion",
                "enum",
                "matrix",
                "simd_vector",
                "bit_set",
            ],
        };
        let numeric = |t: &str| {
            kinds
                .iter()
                .map(|kind| format!("{meta}.type_is_{kind}({t})"))
                .collect::<Vec<_>>()
                .join("||")
        };
        let array_native = if matches!(op, "**" | "<<" | ">>") {
            String::new()
        } else {
            format!(
                "when {} {{return {}}} else ",
                numeric(&format!("{meta}.type_elem_type(T)")),
                scalar("a", "b", op, &power)
            )
        };
        let numeric = numeric("T");
        // Native shifts take a scalar count and power is scalar-only, so
        // corresponding fixed-array fields recurse element by element.
        let element_call = operands("a[i]", "b[i]");
        let leaf = scalar("a", "b", op, &power);
        let code=format!("\n{name}::#force_inline proc \"contextless\"({args})->T{{\n{check}when {meta}.type_is_struct(T){{\n{raw_checks}when ({overlap})>1 {{#panic(Site)}} else {{when {meta}.type_struct_field_count(T)!=({count}) {{{padding_code}when {meta}.type_struct_field_count(T)!=({count})+{padding_count} {{#panic(Site)}}}}\nresult:T\n{fields}return result}}\n}} else when {meta}.type_is_array(T)||{meta}.type_is_enumerated_array(T){{\n{array_native}{{result:T;when {builtin}.len(T{{}})==0 {{return result}};for _,i in result {{result[i]={name}({element_call})}};return result}}\n}} else when {numeric} {{return {leaf}}} else {{#panic(Site)}}\n}}\n");
        self.helpers.insert(
            key,
            self.g(code, NodeId(0)).region(
                &self.source.path,
                self.source.node(NodeId(0)).span,
                "expression-helper",
            ),
        );
        name
    }
    fn generic_struct_math(
        &mut self,
        id: NodeId,
        a: NodeId,
        b: NodeId,
        compound: bool,
        expected: Option<Hint>,
    ) -> Result<Code> {
        let op = if self.source.node(id).kind == "Unary" {
            "~unary".to_string()
        } else {
            self.source.node(id).op.trim_end_matches('=').to_string()
        };
        let helper = self.generic_struct_helper(&op);
        let shown = display_op(&op);
        let token=self.struct_error(id,&format!("Cannot apply '{shown}' to struct: operands must have the same type and every named field must be a self-contained fixed-size value supporting '{shown}'; pointers, slices, strings, dynamic containers, Integer and Rational are excluded."));
        if compound {
            return self.big_statement(id, |this| {
                let typ = this.native_type(a)?.finish().text;
                let condition = this.struct_dispatch_condition(&typ);
                this.struct_math_busy.insert(id);
                let native = this.render(id, expected.clone(), false);
                this.struct_math_busy.remove(&id);
                let mut code = vec![this.g(format!("when {condition} {{\n"), id)];
                let targets = this.big_capture_targets(id, &[a], &mut code)?;
                let target = &targets[0];
                let right = format!(
                    "{}struct_rhs_{}",
                    this.prefix,
                    this.source.node(id).span.start
                );
                let type_of = this.native("type_of");
                let hint = Hint {
                    constant_literal: false,
                    code: this.g(format!("{type_of}({target})"), a),
                    semantic: Some(this.typ(a)),
                    element_only: false,
                    plain_array: false,
                };
                code.extend([
                    this.g(format!("{right}:="), b),
                    this.struct_operand(b, &hint, false)?,
                    this.g(
                        format!(
                            "\n{target}={helper}({target},{right},{})\n}} else {{",
                            json!(token)
                        ),
                        id,
                    ),
                    native?,
                    this.g("}\n", id),
                ]);
                Ok(Code::concat(code))
            });
        }
        // One native/record dispatch for the entire operator tree. Repeating
        // that dispatch at every unknown-type operand duplicates nested source
        // exponentially, even though Odin eventually selects only one branch.
        let mut nodes=HashSet::new();
        let mut pending=vec![id];
        let mut anchor=None;
        while let Some(node)=pending.pop() {
            let node=self.unparen(node);
            let operator=Self::struct_tree_operator(self.source.node(node));
            let extend=operator && (node==id || {
                let typ=self.typ(node);
                typ.is_unknown() || record(&typ)
            });
            let syntax=self.source.node(node);
            if extend {
                nodes.insert(node);
                if syntax.kind=="Unary" {pending.push(self.role(node,"expr")?);}
                else {pending.push(self.role(node,"right")?);pending.push(self.role(node,"left")?);}
            } else if anchor.is_none() && !(syntax.kind=="Compound" && self.source.role(node,"type").is_none()) {
                anchor=Some(node);
            }
        }
        let anchor=anchor.unwrap_or(a);
        let typ=self.native_type(anchor)?;
        let condition=self.struct_dispatch_condition(&typ.finish().text);
        let hint=Hint{constant_literal:false,code:typ,semantic:Some(self.typ(anchor)),element_only:false,plain_array:false};
        let mut inserted=Vec::new();
        for node in &nodes {if self.struct_math_busy.insert(*node){inserted.push(*node);}}
        let native=self.render(id,expected,false);
        for node in inserted {self.struct_math_busy.remove(&node);}
        let native=native?;
        let record=self.struct_tree_value(id,&nodes,&hint)?;
        Ok(Code::concat([self.g("(",id),record,self.g(format!(" when {condition} else "),id),native,self.g(")",id)]))
    }
    pub(super) fn struct_tree_operator(node:&crate::ast::Node)->bool {
        node.kind=="Unary" && node.op=="~" || node.kind=="Binary" && matches!(node.op.as_str(),"+"|"-"|"*"|"/"|"&"|"|"|"~"|"&~"|"<<"|">>"|"**")
    }
    fn struct_type_anchor(&mut self,id:NodeId,typ:&Type)->NodeId {
        let mut at=self.unparen(id);
        while Self::struct_tree_operator(self.source.node(at)) {
            let roles=if self.source.node(at).kind=="Unary" {&["expr"][..]}else{&["left","right"][..]};
            let mut next=None;
            for role in roles {
                if let Some(child)=self.source.role(at,role) {
                    if self.typ(child)==*typ {next=Some(self.unparen(child));break;}
                }
            }
            let Some(child)=next else{break;};
            at=child;
        }
        at
    }
    fn struct_tree_value(&mut self,id:NodeId,nodes:&HashSet<NodeId>,hint:&Hint)->Result<Code> {
        let node=self.unparen(id);
        if !nodes.contains(&node) {return self.struct_operand(id,hint,false);}
        let is_unary=self.source.node(node).kind=="Unary";
        let op=if is_unary {"~unary".to_string()}else{self.source.node(node).op.clone()};
        let helper=self.generic_struct_helper(&op);
        let shown=display_op(&op);
        let token=self.struct_error(node,&format!("Cannot apply '{shown}' to struct: operands must have the same type and every named field must be a self-contained fixed-size value supporting '{shown}'; pointers, slices, strings, dynamic containers, Integer and Rational are excluded."));
        let left=self.role(node,if is_unary{"expr"}else{"left"})?;
        let left=self.struct_tree_value(left,nodes,hint)?;
        let right=if is_unary {self.g("",node)}else {
            let right=self.role(node,"right")?;
            Code::concat([self.g(",",node),self.struct_tree_value(right,nodes,hint)?])
        };
        Ok(Code::concat([self.g(format!("{helper}("),node),left,right,self.g(format!(",{})",json!(token)),node)]))
    }
    fn struct_shape(
        &mut self,
        typ: &Type,
        path: &str,
        depth: usize,
        site: NodeId,
        op: &str,
    ) -> std::result::Result<Shape, String> {
        if depth > 64 {
            return Err(format!(
                "field '{path}' exceeds the supported nesting depth"
            ));
        }
        match base(typ) {
            Type::Vector{elem,..} if !matches!(op,"**"|"<<"|">>") => {
                self.struct_shape(elem,path,depth+1,site,op)?;
                Ok(Shape::Leaf)
            }
            Type::Matrix{elem,..} if matches!(op,"+"|"-"|"*"|"/") => {
                self.struct_shape(elem,path,depth+1,site,op)?;
                Ok(Shape::Leaf)
            }
            Type::Array {elem, length: Some(length)} => {
                let shape = self.struct_shape(elem, &format!("{path}[]"), depth + 1,site,op)?;
                if matches!(shape, Shape::Leaf) && !matches!(op, "**" | "<<" | ">>") { Ok(Shape::Leaf) }
                else { Ok(Shape::Array(usize::try_from(*length).map_err(|_| format!("invalid array length at '{path}'"))?, Box::new(shape))) }
            }
            Type::Scalar {name, ..} if matches!(name.as_str(),"bool"|"b8"|"b16"|"b32"|"b64") && matches!(op,"&"|"|"|"~"|"~unary") => Ok(Shape::Leaf),
            Type::Scalar {name, ..} if (matches!(op,"&"|"|"|"~"|"&~"|"<<"|">>"|"~unary") && name.starts_with('f')) || (op=="**" && !(typ.numeric())) => Err(format!("field '{path}' does not support '{}'",display_op(op))),
            Type::Scalar {name, ..} if typ.numeric() || name=="bit_set" || name.starts_with("complex") || name.starts_with("quaternion") => Ok(Shape::Leaf),
            Type::Enum(_) if matches!(op,"**"|"~unary") => Err(format!("field '{path}' does not support '{}'",display_op(op))),
            Type::Enum(_) => Ok(Shape::Leaf),
            Type::Generic | Type::Unknown | Type::Array {length:None,..} => {
                let helper=self.generic_struct_helper(op);
                let shown=display_op(op);
                let token=self.struct_error(site,&format!("Cannot apply '{shown}' to struct: field '{path}' must be a self-contained fixed-size value supporting '{shown}'; reference-containing values, Integer and Rational are excluded."));
                Ok(Shape::Deferred(helper,json!(token).to_string()))
            }
            Type::Struct(owner) | Type::Record(owner, _) => {
                let source = &self.resolver.sources[owner.file];
                if source.node(owner.node).kind != "StructType" || source.node(owner.node).op == "raw_union" {
                    return Err(format!("field '{path}' is not an ordinary value struct"));
                }
                self.struct_fields_shape(typ, path, depth,site,op)
            }
            Type::Anonymous(_) => self.struct_fields_shape(typ, path, depth,site,op),
            _ => Err(format!("field '{path}' is not a self-contained fixed-size arithmetic value; pointers, slices, strings, dynamic containers, Integer and Rational are excluded")),
        }
    }
    fn struct_fields_shape(
        &mut self,
        typ: &Type,
        path: &str,
        depth: usize,
        site: NodeId,
        op: &str,
    ) -> std::result::Result<Shape, String> {
        let fields = self.resolver.record_fields(typ);
        let mut shapes = Vec::with_capacity(fields.len());
        for (name, typ) in fields {
            if name == "_" {
                continue;
            }
            let path = if path.is_empty() {
                name.clone()
            } else {
                format!("{path}.{name}")
            };
            let shape = self.struct_shape(&typ, &path, depth + 1, site, op)?;
            shapes.push((name, shape));
        }
        Ok(Shape::Record(shapes))
    }
    fn struct_value(
        shape: &Shape,
        a: &str,
        b: &str,
        op: &str,
        type_of: &str,
        power: &str,
        constant: bool,
        statements: &mut String,
        serial: &mut usize,
    ) -> String {
        match shape {
            Shape::Leaf => scalar(a, b, op, power),
            Shape::Deferred(helper, site) => {
                if unary(op) {
                    format!("{helper}({a},{site})")
                } else {
                    format!("{helper}({a},{b},{site})")
                }
            }
            Shape::Record(fields) => format!(
                "{type_of}({a}){{{}}}",
                fields
                    .iter()
                    .map(|(name, shape)| {
                        format!(
                            "{name}={}",
                            Self::struct_value(
                                shape,
                                &format!("({a}).{name}"),
                                &format!("({b}).{name}"),
                                op,
                                type_of,
                                power,
                                constant,
                                statements,
                                serial
                            )
                        )
                    })
                    .collect::<Vec<_>>()
                    .join(",")
            ),
            Shape::Array(length, elem) if constant => format!(
                "{type_of}({a}){{{}}}",
                (0..*length)
                    .map(|i| Self::struct_value(
                        elem,
                        &format!("({a})[{i}]"),
                        &format!("({b})[{i}]"),
                        op,
                        type_of,
                        power,
                        true,
                        statements,
                        serial
                    ))
                    .collect::<Vec<_>>()
                    .join(",")
            ),
            Shape::Array(length, elem) => {
                let index = *serial;
                *serial += 1;
                let name = format!("struct_array_{index}");
                let i = format!("{name}_i");
                let mut body = String::new();
                let value = Self::struct_value(
                    elem,
                    &format!("({a})[{i}]"),
                    &format!("({b})[{i}]"),
                    op,
                    type_of,
                    power,
                    false,
                    &mut body,
                    serial,
                );
                statements.push_str(&format!("{name}:{type_of}({a})\nfor {i} in 0..<{length} {{\n{body}{name}[{i}]={value}\n}}\n"));
                name
            }
        }
    }
    fn struct_helper(&mut self, id: NodeId, shape: &Shape, op: &str) -> String {
        let name = format!(
            "{}struct_math_{}_{}",
            self.prefix,
            self.source.node(id).span.start,
            self.source.node(id).span.end
        );
        let key = (self.helper_scope(id), self.prefix.clone(), name.clone());
        if !self.helpers.contains_key(&key) {
            let type_of = self.native("type_of");
            let power = if op == "**" {
                self.module("numeric")
            } else {
                String::new()
            };
            let mut body = String::new();
            let value = Self::struct_value(
                shape, "a", "b", op, &type_of, &power, false, &mut body, &mut 0,
            );
            let mismatch =
                self.struct_error(id, "Struct arithmetic requires operands of the same type.");
            let args = if unary(op) { "a:$T" } else { "a:$T,b:$U" };
            let guard = if unary(op) {
                String::new()
            } else {
                format!("when T!=U {{#panic({})}} else ", json!(mismatch))
            };
            let code = format!("\n{name} :: #force_inline proc \"contextless\"({args})->T {{\n{guard}{{\n{body}return {value}\n}}\n}}\n");
            self.helpers.insert(
                key,
                self.g(code, id).region(
                    &self.source.path,
                    self.source.node(id).span,
                    "expression-helper",
                ),
            );
        }
        name
    }
    fn struct_operand(&mut self, id: NodeId, hint: &Hint, constant: bool) -> Result<Code> {
        let inner = self.unparen(id);
        let literal = self.source.node(inner).kind == "Compound"
            && self.source.role(inner, "type").is_none()
            && !self.is_array(inner);
        let value = self.render(
            if literal { inner } else { id },
            Some(hint.clone()),
            constant,
        )?;
        Ok(if literal {
            Code::concat([hint.code.clone(), value])
        } else {
            value
        })
    }
    pub(super) fn struct_math(
        &mut self,
        id: NodeId,
        expected: Option<Hint>,
        constant: bool,
    ) -> Result<Option<Code>> {
        if !self.source.thor || self.struct_math_busy.contains(&id) {
            return Ok(None);
        }
        let node = self.source.node(id);
        let compound = node.kind == "Assignment"
            && matches!(
                node.op.as_str(),
                "+=" | "-=" | "*=" | "/=" | "&=" | "|=" | "~=" | "&~=" | "<<=" | ">>=" | "**="
            );
        let is_unary = node.kind == "Unary" && node.op == "~";
        if !(node.kind == "Binary"
            && matches!(
                node.op.as_str(),
                "+" | "-" | "*" | "/" | "&" | "|" | "~" | "&~" | "<<" | ">>" | "**"
            ))
            && !is_unary
            && !compound
        {
            return Ok(None);
        }
        let (a, b) = if is_unary {
            let a = self.role(id, "expr")?;
            (a, a)
        } else if compound {
            let (lhs, rhs) = (self.source.group(id, "lhs"), self.source.group(id, "rhs"));
            if lhs.len() != 1 || rhs.len() != 1 {
                return Ok(None);
            }
            (lhs[0], rhs[0])
        } else {
            (self.role(id, "left")?, self.role(id, "right")?)
        };
        // Evaluation helpers render the expression independently of its
        // declaration/default. Retain that explicit contextual type.
        let expected = if expected.is_none() && node.op == "**" {
            if let Some(annotation) = self
                .source
                .value_annotation(id)
                .filter(|annotation| record(&self.typ(*annotation)))
            {
                Some(Hint {
                    code: self.render(annotation, None, true)?,
                    semantic: Some(self.typ(annotation)),
                    constant_literal: constant,
                    element_only: false,
                    plain_array: false,
                })
            } else {
                expected
            }
        } else {
            expected
        };
        let expected = if node.op == "**" && self.typ(a).is_unknown() && self.typ(b).is_unknown() {
            let parent = node.parent.map(|p| {
                if self.source.node(p).kind == "FieldValue" {
                    self.source.node(p).parent.unwrap_or(p)
                } else {
                    p
                }
            });
            let call = parent.filter(|p| self.source.node(*p).kind == "Call");
            let typ = call.and_then(|call| {
                let plans = self
                    .resolver
                    .call_plans(self.reference(call), &Default::default());
                if plans.len() != 1 {
                    return None;
                }
                let plan = &plans[0];
                self.resolver
                    .fields(plan.signature, "params")
                    .into_iter()
                    .zip(&plan.arguments)
                    .find(|(_, args)| {
                        args.iter().any(|arg| {
                            *arg == self.reference(id)
                                || self.resolver.sources[arg.file].role(arg.node, "value")
                                    == Some(id)
                                    && arg.file == self.file
                        })
                    })
                    .and_then(|((_, annotation), _)| annotation)
                    .map(|annotation| self.resolver.specialized_type(annotation, &plan.bindings))
            });
            if let Some(typ) = typ.filter(record) {
                self.context_type(&typ).map(|code| Hint {
                    code: self.g(code, id),
                    semantic: Some(typ),
                    constant_literal: constant,
                    element_only: false,
                    plain_array: false,
                })
            } else {
                expected
            }
        } else {
            expected
        };
        let at = self.typ(a);
        let bt = self.typ(b);
        let anchor = if record(&at) {
            Some((a, at))
        } else if !compound && record(&bt) {
            Some((b, bt))
        } else {
            None
        };
        let (typ, typ_code) = if let Some((anchor, typ)) = anchor {
            let anchor=self.struct_type_anchor(anchor,&typ);
            (typ, self.native_type(anchor)?)
        } else if let Some(hint) = expected
            .clone()
            .filter(|hint| hint.semantic.as_ref().is_some_and(record))
        {
            (hint.semantic.unwrap(), hint.code)
        } else {
            if !constant
                && self.typ(a).is_unknown()
                && !self.resolver.denotes_type(self.reference(a))
            {
                // Native contextual literals (notably bit-set defaults such as
                // `~{}`) must retain Odin's expected type. A generic helper
                // cannot infer a type from an untyped literal in its own call.
                if expected.as_ref().and_then(|h|h.semantic.as_ref()).is_some_and(|t|!t.is_unknown()&&!record(t)){
                    return Ok(None);
                }
                if let Some(annotation)=self.source.value_annotation(id){
                    let context=self.typ(annotation);
                    if !context.is_unknown(){
                        if !record(&context){return Ok(None);}
                        let hint=Hint{code:self.render(annotation,None,true)?,semantic:Some(context),constant_literal:constant,element_only:false,plain_array:false};
                        return self.struct_math(id,Some(hint),constant);
                    }
                }
                let implicit=|node:NodeId|{let node=self.unparen(node);self.source.node(node).kind=="Compound"&&self.source.role(node,"type").is_none()};
                if implicit(a)&&(is_unary||implicit(b)){return Ok(None);}
                // Enum selectors depend on the surrounding native context.
                // They cannot denote a struct operand; wrapping them in a
                // polymorphic struct helper would discard that context.
                let selector=|node:NodeId|self.source.node(self.unparen(node)).kind=="ImplicitSelector";
                if selector(a)||selector(b){return Ok(None);}
                return self
                    .generic_struct_math(id, a, b, compound, expected)
                    .map(Some);
            }
            return Ok(None);
        };
        let op = if self.source.node(id).kind == "Unary" {
            "~unary".to_string()
        } else {
            self.source.node(id).op.trim_end_matches('=').to_string()
        };
        let shown = display_op(&op);
        let shape = match self.struct_shape(&typ, "", 0, id, &op) {
            Ok(shape) => shape,
            Err(reason) => {
                return Ok(Some(self.error_token(
                    id,
                    &format!("Cannot apply '{shown}' to struct: {reason}."),
                )))
            }
        };
        let hint = Hint {
            constant_literal: constant,
            code: typ_code,
            semantic: Some(typ),
            element_only: false,
            plain_array: false,
        };
        if compound {
            let helper = self.struct_helper(id, &shape, &op);
            return self
                .big_statement(id, |this| {
                    let mut code = vec![];
                    let targets = this.big_capture_targets(id, &[a], &mut code)?;
                    let target = &targets[0];
                    let rhs = format!(
                        "{}struct_rhs_{}",
                        this.prefix,
                        this.source.node(id).span.start
                    );
                    let type_of = this.native("type_of");
                    code.extend([
                        this.g(format!("{rhs}:="), b),
                        this.struct_operand(
                            b,
                            &Hint {
                                code: this.g(format!("{type_of}({target})"), a),
                                ..hint.clone()
                            },
                            false,
                        )?,
                        this.g(format!("\n{target}={helper}({target},{rhs})\n"), id),
                    ]);
                    Ok(Code::concat(code))
                })
                .map(Some);
        }
        let left = self.struct_operand(a, &hint, constant)?;
        let right = if is_unary {
            Code::concat([])
        } else {
            self.struct_operand(b, &hint, constant)?
        };
        if constant {
            let name = format!(
                "{}struct_constant_{}_{}",
                self.prefix,
                self.source.node(id).span.start,
                self.source.node(id).span.end
            );
            let key = (
                self.helper_scope(id),
                self.prefix.clone(),
                format!(
                    "struct_constant_{:020}_{:020}",
                    self.source.node(id).span.end,
                    usize::MAX - self.source.node(id).span.start
                ),
            );
            let type_of = self.native("type_of");
            let power = if op == "**" {
                self.module("numeric")
            } else {
                String::new()
            };
            let value = Self::struct_value(
                &shape,
                &format!("{name}_a"),
                &format!("{name}_b"),
                &op,
                &type_of,
                &power,
                true,
                &mut String::new(),
                &mut 0,
            );
            let mismatch = self
                .error_token(b, "Struct arithmetic requires operands of the same type.")
                .finish()
                .text;
            let value = if is_unary {
                value
            } else {
                format!("({value} when {type_of}({name}_a)=={type_of}({name}_b) else {mismatch})")
            };
            let code = Code::concat([
                self.g(format!("\n{name}_a :: "), a),
                left,
                if is_unary {
                    Code::concat([])
                } else {
                    Code::concat([self.g(format!("\n{name}_b :: "), b), right])
                },
                self.g(format!("\n{name} :: {value}\n"), id),
            ]);
            self.helpers.insert(key, code);
            return Ok(Some(self.g(name, id)));
        }
        let helper = self.struct_helper(id, &shape, &op);
        Ok(Some(Code::concat([
            self.g(format!("{helper}("), id),
            left,
            if is_unary {
                Code::concat([])
            } else {
                Code::concat([self.g(",", id), right])
            },
            self.g(")", id),
        ])))
    }
}
