//! Compact syntax tags; unknown future native tags preserve their spelling.
use std::ops::Deref;
use serde::{Deserialize, Deserializer};
macro_rules! kinds {
    ($($kind:ident),* $(,)?) => {
        #[derive(Clone,Debug,PartialEq,Eq,Hash)]
        pub enum Kind {$($kind,)* Unknown(Box<str>)}
        impl Kind {
            #[inline]
            pub fn as_str(&self)->&str {match self {$(Self::$kind=>stringify!($kind),)*Self::Unknown(name)=>name}}
        }
        impl PartialEq<&str> for Kind {
            #[inline]
            fn eq(&self, other: &&str) -> bool {
                if let Self::Unknown(name) = self {
                    return name.as_ref() == *other;
                }
                match *other {
                    $(stringify!($kind) => matches!(self, Self::$kind),)*
                    _ => false,
                }
            }
        }
        impl From<&str> for Kind {
            fn from(name:&str)->Self{match name {$(stringify!($kind)=>Self::$kind,)*_=>Self::Unknown(name.into())}}
        }
    }
}
kinds! {
    ArrayType, Assignment, Attribute, AutoCast, Binary, BitFieldType, BitSetType, Block, Call,
    Case, Cast, CompileSwitch, CompileSwitchExpr, Comprehension, ComprehensionBody,
    Compound, Declaration, Defer, Deref, Directive, DistinctType, DynamicArrayType,
    Ellipsis, EnumType, ExpressionStatement, FallbackList, Field, FieldValue, Fields, File, For,
    ForeignBlock, ForeignImport, HelperType, Ident, If, Implicit, ImplicitSelector, Import,
    Index, Interpolation, Literal, MapType, MultiPointerType, OrBranch, OrElse,
    OrPanic, OrReturn, Other, Package, Paren, PointerType, PolyType, Procedure,
    ProcedureGroup, ProcedureType, Range, Return, Run, Selector, SetType, Slice,
    StructType, Switch, SwitchExpr, SwitchResults, Ternary, TypeAssertion, TypeSwitch,
    TypeidType, Unary, UnionType, Unsafe, Using, When, WhenExpr,
}
impl Deref for Kind {
    type Target = str;
    fn deref(&self) -> &str {
        self.as_str()
    }
}
impl std::fmt::Display for Kind {
    fn fmt(&self, out: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        out.write_str(self.as_str())
    }
}
impl<'de> Deserialize<'de> for Kind {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        struct SyntaxKind;
        impl serde::de::Visitor<'_> for SyntaxKind {
            type Value = Kind;
            fn expecting(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
                formatter.write_str("a syntax kind name")
            }
            fn visit_str<E: serde::de::Error>(self, name: &str) -> Result<Kind, E> {
                Ok(Kind::from(name))
            }
        }
        deserializer.deserialize_str(SyntaxKind)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn string_comparisons_preserve_known_and_future_tag_spellings() {
        let tags = [
            "Ident",
            "Call",
            "Field",
            "File",
            "UnknownFutureTag",
            "",
            "call",
        ];
        for spelling in tags {
            for kind in [Kind::from(spelling), Kind::Unknown(spelling.into())] {
                for other in tags {
                    if (kind == other) != (spelling == other) || kind.as_str() != spelling {
                        panic!("syntax kind comparison changed for {spelling:?} and {other:?}");
                    }
                }
            }
        }
    }
}
