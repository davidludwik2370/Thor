//! Symbolic permissions from thorlang/pureborrow.py. A false permission means
//! owned storage; true means readonly/borrowed. Accesses remain parameter-relative
//! until a caller supplies its provenance. Type traits stay native type queries.
use crate::semantics::Ref;
use std::collections::BTreeSet;

pub const MAX_PATH: usize = 24;

#[derive(Clone, Debug, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub enum Step {
    Deref,
    Field(String),
    Item,
}
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub enum Aspect {
    Borrowed,
    Location,
}
#[derive(Clone, Debug, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub struct Access {
    pub procedure: Ref,
    pub index: usize,
    pub path: Vec<Step>,
    pub aspect: Aspect,
}
impl Access {
    pub fn step(&self, step: Step, aspect: Option<Aspect>) -> Self {
        let mut result = self.clone();
        result.path.push(step);
        if let Some(aspect) = aspect {
            result.aspect = aspect;
        }
        result
    }
}
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub enum Trait {
    Big,
    NotBig,
    Pointer,
    Sequence,
}
// Variant order follows Permission.order's kind names in the reference.
#[derive(Clone, Debug, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub enum Permission {
    Access(Access),
    And(Box<Self>, Box<Self>),
    Constant(bool),
    Or(Vec<Self>),
    Type(Trait, Ref),
}
pub const OWNED: Permission = Permission::Constant(false);
pub const READONLY: Permission = Permission::Constant(true);

impl Permission {
    pub fn is_owned(&self) -> bool {
        *self == OWNED
    }
    pub fn join(&self, other: &Self) -> Self {
        if *self == READONLY || *other == READONLY {
            return READONLY;
        }
        if *self == OWNED {
            return other.clone();
        }
        if *other == OWNED || self == other {
            return self.clone();
        }
        let mut parts = BTreeSet::new();
        for value in [self, other] {
            if let Self::Or(values) = value {
                parts.extend(values.iter().cloned());
            } else {
                parts.insert(value.clone());
            }
        }
        Self::Or(parts.into_iter().collect())
    }
    pub fn meet(&self, other: &Self) -> Self {
        if *self == OWNED || *other == OWNED {
            return OWNED;
        }
        if *self == READONLY {
            return other.clone();
        }
        if *other == READONLY || self == other {
            return self.clone();
        }
        Self::And(Box::new(self.clone()), Box::new(other.clone()))
    }
    pub fn project(&self, step: Step, aspect: Option<Aspect>) -> Self {
        match self {
            Self::Access(access) if access.path.len() >= MAX_PATH => READONLY,
            Self::Access(access) => Self::Access(access.step(step, aspect)),
            Self::Or(parts) => parts.iter().fold(OWNED, |result, part| {
                result.join(&part.project(step.clone(), aspect))
            }),
            _ => self.clone(),
        }
    }
    pub fn accesses(&self) -> BTreeSet<Access> {
        match self {
            Self::Access(access) => [access.clone()].into(),
            Self::And(left, right) => {
                let mut result = left.accesses();
                result.extend(right.accesses());
                result
            }
            Self::Or(parts) => parts.iter().flat_map(|part| part.accesses()).collect(),
            _ => BTreeSet::new(),
        }
    }
    pub fn substitute(&self, resolve: &mut impl FnMut(&Access) -> Self) -> Self {
        match self {
            Self::Access(access) => resolve(access),
            Self::And(left, right) => left.substitute(resolve).meet(&right.substitute(resolve)),
            Self::Or(parts) => parts
                .iter()
                .fold(OWNED, |result, part| result.join(&part.substitute(resolve))),
            _ => self.clone(),
        }
    }
    pub fn render(
        &self,
        access: &mut impl FnMut(&Access) -> String,
        trait_query: &mut impl FnMut(Trait, Ref) -> String,
    ) -> String {
        match self {
            Self::Constant(value) => value.to_string(),
            Self::Access(value) => access(value),
            Self::Type(trait_, node) => trait_query(*trait_, *node),
            Self::And(left, right) => format!(
                "({} && {})",
                left.render(access, trait_query),
                right.render(access, trait_query)
            ),
            Self::Or(parts) => format!(
                "({})",
                parts
                    .iter()
                    .map(|part| part.render(access, trait_query))
                    .collect::<Vec<_>>()
                    .join(" || ")
            ),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::ast::NodeId;
    fn input(index: usize) -> Permission {
        Permission::Access(Access {
            procedure: Ref {
                file: 0,
                node: NodeId(1),
            },
            index,
            path: vec![],
            aspect: Aspect::Borrowed,
        })
    }
    #[test]
    fn joins_are_canonical_and_constants_keep_the_reference_rules() {
        let (a, b, c) = (input(0), input(1), input(2));
        let left = a.join(&b).join(&c).join(&a);
        let right = c.join(&b.join(&a));
        assert_eq!(left, right);
        let owned = left.meet(&OWNED);
        let readonly = left.join(&READONLY);
        let identity = left.meet(&READONLY);
        assert_eq!(owned, OWNED);
        assert_eq!(readonly, READONLY);
        assert_eq!(identity, left);
    }
    #[test]
    fn projected_paths_and_location_aspects_match_the_reference() {
        let mut value = input(0);
        for _ in 0..MAX_PATH {
            value = value.project(Step::Item, Some(Aspect::Location));
        }
        let accesses = value.accesses();
        let access = accesses.first().unwrap();
        assert_eq!(access.path.len(), MAX_PATH);
        assert_eq!(access.aspect, Aspect::Location);
        let conservative = value.project(Step::Deref, None);
        assert_eq!(conservative, READONLY);
    }
    #[test]
    fn substituting_a_caller_permission_preserves_type_dependent_conditions() {
        let source = Ref {
            file: 0,
            node: NodeId(7),
        };
        let condition = Permission::Type(Trait::Sequence, source);
        let permission = input(0).meet(&condition).join(&input(1));
        let result = permission.substitute(&mut |access| {
            if access.index == 0 {
                READONLY
            } else {
                OWNED
            }
        });
        assert_eq!(result, condition);
        let rendered = result.render(&mut |_| "access".into(), &mut |trait_, _| {
            format!("{trait_:?}")
        });
        assert_eq!(rendered, "Sequence");
    }
}
