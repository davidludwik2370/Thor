//! Independent native compiler. Python is a differential oracle, never a runtime dependency.
#![forbid(unsafe_code)]
pub mod ast;
pub mod cli;
pub mod comptime;
pub mod compiler_output;
pub mod bigconstantvalue;
pub mod bigstatic;
pub mod bigvalues;
pub mod borrowbind;
pub mod borroweval;
pub mod borrowflow;
pub mod borrowlocals;
pub mod borrowprogram;
pub mod diagnostic;
mod diagnosticcleanup;
pub mod digest;
pub mod emit;
pub mod evaluation;
pub mod evaluationcache;
pub mod evaluationinputs;
mod extremaplan;
mod fallbacks;
mod file_alias;
mod platform_io;
pub mod formatting;
pub mod frontend;
pub mod kind;
pub mod lower;
pub mod memory;
pub mod symbolizer;
pub mod diskcache;
pub mod migrate;
mod native_compat;
pub mod permissions;
mod permissioncache;
mod parallel;
pub mod profile;
pub mod privileges;
pub mod project;
pub mod project_template;
pub mod purity;
mod purediagnostics;
pub mod semantics;
mod resulttypes;
mod parallelcompile;
pub mod specialization;
pub mod values;
pub mod vet;
pub mod unverified;
mod lifetime;

pub const VERSION: &str = env!("CARGO_PKG_VERSION");

pub mod publishing;

mod source_tools;
mod source_uses;
mod unused_attributes;

mod vet_attributes;
mod caller_metadata;
mod noalloc;
mod scoped;
