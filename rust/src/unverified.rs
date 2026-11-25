//! Presentation and exit policy for incomplete analyses. This never changes
//! the analysis, establishes safety, or creates source corrections.
use crate::diagnostic::{Error, Result};
use serde_json::{json, Value};
use std::collections::{BTreeMap, HashSet};

#[derive(Clone, Copy, Default, Debug, PartialEq, Eq)]
pub enum Mode {
    #[default]
    Summary,
    Show,
    Error,
}
impl Mode {
    pub fn parse(value: &str) -> Result<Self> {
        match value {
            "summary" => Ok(Self::Summary),
            "show" => Ok(Self::Show),
            "error" => Ok(Self::Error),
            _ => Err(Error::usage("--unverified: choose summary, show, or error")),
        }
    }
    pub fn name(self) -> &'static str {
        match self { Self::Summary => "summary", Self::Show => "show", Self::Error => "error" }
    }
}

pub(crate) fn is_unverified(error: &Value) -> bool {
    error["stage"] == "vet" && (error["certainty"] == "unverified"
        || error["code"] == "thor/correction-unknown")
}

/// Explain the boundary, rather than implying an uncertain finding is a bug.
/// Classification uses the already-computed reason; there is no new graph walk.
pub(crate) fn prepare(error: &mut Value, mode: Mode) {
    let message = error["msgs"][0].as_str().unwrap_or_default();
    let (reason, help) = if error["code"] == "thor/correction-unknown" {
        ("correction-validation", "Compiler validation of this suggested edit did not finish. Review it manually; no automatic correction is offered.")
    } else if message.contains("budget") || message.contains("work limit") {
        ("analysis-limit", "The analyzer reached a work or depth limit. The remaining operations were not fully checked; this does not establish a memory bug.")
    } else if message.contains("did not converge") {
        ("convergence-limit", "The analyzer could not establish a stable summary for this loop or recursive call. Review the ownership changes across iterations or calls.")
    } else if message.contains("allocator lifetime is unknown") {
        ("allocator-ownership", "An enclosing arena or custom allocator may own this allocation. Check that allocator's cleanup and the caller's ownership contract before adding individual cleanup.")
    } else if message.contains("size arithmetic") {
        ("size-arithmetic", "The allocation or buffer count may have wrapped or narrowed. Check that it still represents the intended size.")
    } else if message.contains("layout") || message.contains("byte extent") {
        ("raw-memory-layout", "The analyzer cannot establish this access's size, layout or offset. Review the allocation extent, alignment and accessed range.")
    } else if message.contains("no reviewed") || message.contains("opaque") || message.contains("callback") {
        ("call-contract", "The callee's reference effects are not fully known. Check whether it retains, releases or returns borrowed storage; a read-only input alone does not establish that contract.")
    } else if message.contains("provenance") || message.contains("tracking depth") {
        ("reference-provenance", "The analyzer cannot identify all storage reached by this reference. Check its original owner and ensure that owner remains alive for every use.")
    } else if message.contains("some paths") || message.contains("another path") || message.contains("may") {
        ("conditional-ownership", "The analyzer cannot resolve all ownership or cleanup paths here. Check that each allocation is released at most once and every reference is used before its storage is released.")
    } else {
        ("ownership-contract", "The available ownership information is insufficient to establish safety or a bug. Review the referenced storage's owner, cleanup and any escaping aliases.")
    };
    error["type"] = json!(if mode == Mode::Error { "error" } else { "info" });
    error["certainty"] = json!("unverified");
    error["reason"] = json!(reason);
    error["help"] = json!(help);
    // Repeated reporting/collection must not duplicate the explanatory line.
    if let Some(messages) = error["msgs"].as_array_mut() {
        if !messages.iter().any(|message| message.as_str() == Some(help)) {
            messages.push(json!(help));
        }
    }
    // An unfinished proof must never be published as a safe source correction.
    if let Some(object) = error.as_object_mut() { object.remove("fix"); }
}

#[derive(Default)]
pub(crate) struct Summary {
    pub count: usize,
    pub definite: usize,
    categories: BTreeMap<String, usize>,
    locations: HashSet<(String, u64)>,
}
impl Summary {
    pub fn add(&mut self, error: &Value) {
        if is_unverified(error) {
            self.count += 1;
            let category = match error["code"].as_str() {
                Some("thor/lifetime-unknown") => "lifetime",
                Some("thor/leak-unknown") => "leak",
                _ => "correction",
            };
            *self.categories.entry(category.into()).or_default() += 1;
            self.locations.insert((error["pos"]["file"].as_str().unwrap_or_default().into(),
                error["pos"]["offset"].as_u64().unwrap_or_default()));
        } else if error["stage"] == "vet" && error["certainty"] == "definite" {
            self.definite += 1;
        }
    }
    pub fn json(&self, mode: Mode) -> Value {
        json!({"mode":mode.name(),"count":self.count,"locations":self.locations.len(),
            "categories":self.categories,"definite_memory_issues":self.definite})
    }
    pub fn text(&self, mode: Mode, command: &str) -> String {
        if self.count == 0 { return String::new(); }
        let finding = if self.count == 1 { "finding" } else { "findings" };
        let location = if self.locations.len() == 1 { "location" } else { "locations" };
        let mut text = format!("Unverified analysis: {} {finding} at {} source {location}. These are incomplete checks, not confirmed bugs.", self.count, self.locations.len());
        if mode == Mode::Summary {
            let vet = if command == "vet" { "" } else { " --vet" };
            text.push_str(&format!("\nShow details with thor {command}{vet} --unverified (keep the same source and build options)."));
        }
        text
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn policy_counts_findings_and_locations_without_reclassifying_definite_issues() {
        let mut summary = Summary::default();
        for code in ["thor/lifetime-unknown", "thor/leak-unknown"] {
            let mut d=json!({"stage":"vet","type":"warning","certainty":"unverified","code":code,
                "pos":{"file":"a.thor","offset":7},"msgs":["Unverified lifetime: analysis budget reached."],"fix":{}});
            prepare(&mut d,Mode::Summary);
            summary.add(&d);
            if d["type"]!="info" || d.get("fix").is_some() || d["reason"]!="analysis-limit" {panic!("invalid notice: {d}");}
            let once=d.clone();prepare(&mut d,Mode::Summary);
            if d!=once {panic!("reporting changed the same notice twice");}
        }
        let definite=json!({"stage":"vet","certainty":"definite","type":"error","code":"thor/lifetime"});
        summary.add(&definite);
        let stats=summary.json(Mode::Summary);
        if stats["count"]!=2 || stats["locations"]!=1 || stats["definite_memory_issues"]!=1 {panic!("bad counts: {stats}");}
        if is_unverified(&definite) {panic!("definite finding became unverified");}
    }
    #[test]
    fn strict_policy_also_covers_inconclusive_convention_corrections() {
        let mut d=json!({"type":"warning","stage":"vet","code":"thor/correction-unknown","msgs":["Could not verify correction."]});
        if !is_unverified(&d) {panic!("missed incomplete correction");}
        prepare(&mut d,Mode::Error);
        if d["type"]!="error" || d["reason"]!="correction-validation" || d["certainty"]!="unverified" {panic!("strict mode lost the finding");}
        for value in ["", "warn", "off", "true"] {if Mode::parse(value).is_ok(){panic!("accepted invalid mode");}}
    }
}
