//! Independent native compiler. Python is a differential oracle, never a runtime dependency.
#![forbid(unsafe_code)]
pub mod ast;
pub mod diagnostic;
pub mod digest;
pub mod emit;
pub mod formatting;
pub mod frontend;
pub mod kind;
pub mod lower;
pub mod memory;
pub mod privileges;
pub mod project;
pub mod purity;
pub mod semantics;
pub mod values;

pub const VERSION: &str = env!("CARGO_PKG_VERSION");
