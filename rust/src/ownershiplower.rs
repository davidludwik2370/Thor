//! Native zero-sized permission profiles for checked ordinary calls.
//! Public/ordinary signatures keep their source ownership behavior.
use super::*;
impl Lower<'_, '_> {
    pub(super) fn ownership_signature(&mut self, id: NodeId) -> Result<Option<Code>> {
        let Some(proc) = self.source.node(id).parent.filter(|proc| {
            self.source.node(*proc).kind == "Procedure"
                && self.emitting_variants.contains(proc)
                && self.borrowing.procedures.contains(&self.reference(*proc))
                && !self.borrowing.entries.contains(&self.reference(*proc))
        }) else {
            return Ok(None);
        };
        let params = self.role(id, "params")?;
        let marker = &self.borrow_marker;
        let extra = format!("{}{marker}:${marker}_Type=true,{marker}_ownership:${marker}_ownership_Type=struct{{}}{{}}",
            if self.source.node(params).children.is_empty() { "" } else { "," });
        let mut fields = vec![];
        for field in self.source.node(params).children.clone() {
            fields.push((
                self.source.node(field).span,
                self.render(field, None, false)?,
            ));
        }
        let code = self.splice(
            params,
            fields,
            vec![(self.source.node(params).span.end, self.g(extra, proc))],
        )?;
        let span = self.source.node(params).span;
        let mut replacements = vec![];
        let mut inserts = vec![];
        if span.is_empty() {
            inserts.push((span.start, code));
        } else {
            replacements.push((span, code));
        }
        for child in self.source.node(id).children.clone() {
            if child != params {
                replacements.push((
                    self.source.node(child).span,
                    self.render(child, None, false)?,
                ));
            }
        }
        Ok(Some(self.splice(id, replacements, inserts)?))
    }
    pub(super) fn ownership_body(&mut self, id: NodeId) -> Result<Option<Code>> {
        let Some(proc) = self.source.node(id).parent.filter(|proc| {
            self.source.node(*proc).kind == "Procedure"
                && self.emitting_variants.contains(proc)
                && self.borrowing.procedures.contains(&self.reference(*proc))
                && !self.borrowing.entries.contains(&self.reference(*proc))
        }) else {
            return Ok(None);
        };
        if !self.ownership_wrapping.insert(id) {
            return Ok(None);
        }
        let body = self.render(id, None, false);
        self.ownership_wrapping.remove(&id);
        Ok(Some(Code::concat([
            self.g(format!("{{when {}_Type==bool ", self.borrow_marker), proc),
            body?,
            self.g("}", proc),
        ])))
    }
    pub(super) fn ownership_argument(&mut self, call: NodeId) -> Result<Option<Code>> {
        if self.checked_targets.get(&call).is_some_and(|targets| {
            targets
                .iter()
                .all(|target| self.borrowing.entries.contains(target))
        }) {
            return Ok(None);
        }
        let Some(fields) = self.borrowing.profiles.get(&self.reference(call)).cloned() else {
            return Ok(None);
        };
        let mut profile = vec![];
        for (access, permission) in fields {
            let key = access.key(self.resolver);
            let query = self.permission_query(&permission, call)?;
            profile.push(format!("{key}:([0]bool when {query} else [0]u8)"));
        }
        Ok(Some(self.g(
            format!(
                "{}=true,{}_ownership=struct{{{}}}{{}}",
                self.borrow_marker,
                self.borrow_marker,
                profile.join(",")
            ),
            call,
        )))
    }
}
