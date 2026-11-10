//! Independent native compiler. Python is a differential oracle, never a runtime dependency.
#![forbid(unsafe_code)]
pub mod ast;
pub mod borrowbind;
pub mod borroweval;
pub mod borrowflow;
pub mod borrowlocals;
pub mod borrowprogram;
pub mod diagnostic;
pub mod digest;
pub mod emit;
pub mod evaluation;
mod extremaplan;
pub mod formatting;
pub mod frontend;
pub mod kind;
pub mod lower;
pub mod memory;
pub mod permissions;
pub mod privileges;
pub mod project;
pub mod purity;
pub mod semantics;
pub mod specialization;
pub mod values;

pub const VERSION: &str = env!("CARGO_PKG_VERSION");
