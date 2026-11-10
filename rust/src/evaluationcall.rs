//! Reference allocator-selection and entry diagnostic rules.
use super::*;
impl Scheduler<'_> {
    pub(super) fn explicit_allocator(&mut self, call: Ref) -> bool {
        explicit_allocator(&mut self.resolver, call)
    }
    pub(super) fn io_message(&self, call: Ref) -> String {
        let mut subject = "Pure code".to_string();
        let source = self.source(call);
        if let Some(owner) = source
            .ancestors(call.node)
            .find(|id| source.node(*id).kind == "Procedure")
        {
            if source.node(owner).data["pure"] == true {
                let mut declaration = source.node(owner).parent;
                while declaration.is_some_and(|id| source.node(id).kind == "Paren") {
                    declaration = declaration.and_then(|id| source.node(id).parent);
                }
                if let Some(declaration) =
                    declaration.filter(|id| source.node(*id).kind == "Declaration")
                {
                    if let [name] = source.group(declaration, "names") {
                        subject = format!("Pure function '{}'", source.text(*name));
                    }
                }
            }
        }
        let callee = self.role(call, "callee").unwrap_or(call);
        format!("{subject} cannot call '{}' because it performs I/O. Use proc for I/O and keep it outside fn/#run call chains.",self.source(callee).text(callee.node))
    }
}
