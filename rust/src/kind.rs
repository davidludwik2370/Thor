//! Compact syntax tags; unknown future native tags preserve their spelling.
use std::ops::Deref;
macro_rules! kinds {
    ($($kind:ident),* $(,)?) => {
        #[derive(Clone,Debug,PartialEq,Eq,Hash)]
        pub enum Kind {$($kind,)* Unknown(Box<str>)}
        impl Kind {
            pub fn as_str(&self)->&str {match self {$(Self::$kind=>stringify!($kind),)*Self::Unknown(name)=>name}}
        }
        impl From<&str> for Kind {
            fn from(name:&str)->Self{match name {$(stringify!($kind)=>Self::$kind,)*_=>Self::Unknown(name.into())}}
        }
    }
}
kinds! {
    ArrayType, Assignment, Attribute, AutoCast, Binary, BitSetType, Block, Call,
    Case, Cast, CompileSwitch, CompileSwitchExpr, Comprehension, ComprehensionBody,
    Compound, Declaration, Defer, Deref, Directive, DistinctType, DynamicArrayType,
    Ellipsis, EnumType, ExpressionStatement, Field, FieldValue, Fields, File, For,
    ForeignBlock, ForeignImport, Ident, If, Implicit, ImplicitSelector, Import,
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
impl PartialEq<&str> for Kind {
    fn eq(&self, other: &&str) -> bool {
        self.as_str() == *other
    }
}
