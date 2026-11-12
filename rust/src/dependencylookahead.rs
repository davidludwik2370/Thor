//! Bounded syntax/name lookahead from imports actually requested by analysis.
//! No effect facts are inferred here; ordinary analysis remains authoritative.
use crate::semantics::{ImportDemand, Ref, Resolver};
use std::collections::{HashSet, VecDeque};

#[derive(Default)]
pub(super) struct Lookahead {
    visited: HashSet<(Ref, bool)>,
    remaining: usize,
}
impl Lookahead {
    pub fn new() -> Self {
        // A discovery budget, not a language limit. Large, conditional and
        // unresolved generic bodies keep their normal demand-driven fallback.
        Self {
            remaining: 4096,
            ..Self::default()
        }
    }
    pub fn expand(&mut self, resolver: &Resolver<'_>, demands: &[ImportDemand]) {
        if self.remaining == 0 {
            return;
        }
        let previous = resolver.begin_symbol_queries();
        let tracking = resolver.track_import_demands(true);
        let mut pending = VecDeque::new();
        for demand in demands {
            if pending.len() >= self.remaining {
                break;
            }
            if let Some(owner) = demand.owner {
                // A missing constant/type can precede other independent imports
                // in the same caller. Resume that selected declaration too.
                pending.push_back((owner, true));
            }
            if let Some(symbol) = resolver.replay_import(demand) {
                enqueue_symbol(resolver, symbol, &mut pending);
            }
        }
        let before = self.remaining;
        while let Some((reference, procedure_body)) = pending.pop_front() {
            if self.remaining == 0 {
                break;
            }
            if !self.visited.insert((reference, procedure_body)) {
                continue;
            }
            self.remaining -= 1;
            let source = &resolver.sources[reference.file];
            let node = source.node(reference.node);
            if !selected(resolver, reference) {
                continue;
            }
            if node.kind == "When" {
                if let Some(cond) = source.role(reference.node, "cond") {
                    let cond = Ref {
                        node: cond,
                        ..reference
                    };
                    pending.push_back((cond, false));
                    if let Some(selected) = resolver.condition(cond) {
                        if let Some(arm) =
                            source.role(reference.node, if selected { "body" } else { "else" })
                        {
                            pending.push_back((
                                Ref {
                                    node: arm,
                                    ..reference
                                },
                                false,
                            ));
                        }
                    }
                }
                continue;
            }
            if node.kind == "Procedure" {
                if let Some(signature) = source.role(reference.node, "type") {
                    pending.push_back((
                        Ref {
                            node: signature,
                            ..reference
                        },
                        false,
                    ));
                    let mut generic = false;
                    for id in source.descendants(signature) {
                        if self.remaining == 0 {
                            break;
                        }
                        self.remaining -= 1;
                        if source.node(id).kind == "PolyType" {
                            generic = true;
                            break;
                        }
                    }
                    if procedure_body && !generic {
                        if let Some(body) = source.role(reference.node, "body") {
                            pending.push_back((
                                Ref {
                                    node: body,
                                    ..reference
                                },
                                false,
                            ));
                        }
                    }
                }
                continue;
            }
            // Without concrete arguments, choosing an overload would be
            // speculative. The real call planner will request its dependencies.
            if matches!(
                node.kind.as_str(),
                "ProcedureGroup" | "CompileSwitch" | "Run"
            ) {
                continue;
            }
            if node.kind == "Selector"
                || node.kind == "Ident" && resolver.expression_reference(reference)
            {
                if let Some(symbol) = resolver.symbol(reference) {
                    enqueue_symbol(resolver, symbol, &mut pending);
                }
            }
            // Large literals can have huge child lists. Bound queued work too,
            // rather than allocating the whole list before enforcing visits.
            for child in node
                .children
                .iter()
                .take(self.remaining.saturating_sub(pending.len()))
            {
                pending.push_back((
                    Ref {
                        node: *child,
                        ..reference
                    },
                    false,
                ));
            }
        }
        resolver.end_symbol_queries(previous);
        resolver.track_import_demands(tracking);
        crate::profile::count(
            "analysis.dependency_lookahead_nodes",
            (before - self.remaining) as u64,
        );
        if self.remaining == 0 {
            crate::profile::count("analysis.dependency_lookahead_budget_exhaustions", 1);
        }
    }
}
fn selected(resolver: &Resolver<'_>, reference: Ref) -> bool {
    let source = &resolver.sources[reference.file];
    let span = source.node(reference.node).span;
    for ancestor in source.ancestors(reference.node) {
        if source.node(ancestor).kind != "When" {
            continue;
        }
        let Some(cond) = source.role(ancestor, "cond") else {
            return false;
        };
        let Some(selected) = resolver.condition(Ref {
            node: cond,
            ..reference
        }) else {
            return false;
        };
        let arm = source.role(ancestor, if selected { "body" } else { "else" });
        if !arm.is_some_and(|arm| source.node(arm).span.contains(span)) {
            return false;
        }
    }
    true
}
fn enqueue_symbol(resolver: &Resolver<'_>, symbol: usize, pending: &mut VecDeque<(Ref, bool)>) {
    let symbol = &resolver.symbols[symbol];
    if let Some(annotation) = symbol.annotation {
        pending.push_back((
            Ref {
                node: annotation,
                ..symbol.reference
            },
            false,
        ));
    }
    if symbol.constant {
        if let Some(value) = symbol.value {
            pending.push_back((
                Ref {
                    node: value,
                    ..symbol.reference
                },
                true,
            ));
        }
    }
}
