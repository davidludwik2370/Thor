//! Execution policy; independent of Odin optimization and pure contracts.
use crate::diagnostic::{Error,Result};
#[derive(Clone,Copy,Debug,Default,PartialEq,Eq)]
pub enum Mode { Required, #[default] Normal, Aggressive, Override }
impl Mode {
    pub fn parse(value:&str)->Result<Self> {match value {
        "required"=>Ok(Self::Required),"normal"=>Ok(Self::Normal),
        "aggressive"=>Ok(Self::Aggressive),"override"=>Ok(Self::Override),
        _=>Err(Error::usage("--comptime: choose required, normal, aggressive, override")),
    }}
    pub fn name(self)->&'static str {match self {
        Self::Required=>"required",Self::Normal=>"normal",Self::Aggressive=>"aggressive",Self::Override=>"override",
    }}
    pub fn aggressive(self)->bool {matches!(self,Self::Aggressive|Self::Override)}
    pub fn overrides_runtime(self)->bool {self==Self::Override}
}
