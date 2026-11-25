//! Narrow raylib 6.x contracts reviewed against upstream C revision
//! f5994221b4a1b57cae9e2f8871503cd309e12f14. Title arguments are retained:
//! unlike text drawing/measurement they cannot receive a read-only shortcut.
use super::*;
impl Analysis<'_, '_> {
    pub(super) fn foreign_model(
        &mut self,
        state: &mut State,
        f: &mut Frame,
        at: Ref,
        args: &[Ref],
        vs: &[Value],
        lib: &str,
        name: &str,
    ) -> Option<Vec<Value>> {
        if !self.raylib_six
            || lib != "vendor:raylib"
            || !matches!(
                name,
                "DrawText"
                    | "MeasureText"
                    | "InitWindow"
                    | "SetWindowTitle"
                    | "CloseWindow"
                    | "LoadImageFromTexture"
                    | "ExportImage"
                    | "ImageFlipVertical"
                    | "UnloadImage"
            )
        {
            return None;
        }
        if self.custom_foreign_callbacks {
            self.unknown_call(state,at,vs,"registered foreign callbacks prevent verification of the library's reference effects");
            if state.reachable(vs.iter().cloned()).is_empty() {
                self.report(
                    at,
                    None,
                    false,
                    "lifetime",
                    "registered foreign callbacks have unverified global reference effects",
                );
            }
            if name == "LoadImageFromTexture" {
                let result = self.image_model(state, f, at, args, vs, name).unwrap();
                for id in state.reachable(result.iter().cloned()) {
                    if let Some(cell) = state.cells.get_mut(&id) {
                        cell.uncertain = true;
                    }
                }
                return Some(result);
            }
            return Some(vec![Value::default()]);
        }
        if let Some(result) = self.image_model(state, f, at, args, vs, name) {
            return Some(result);
        }
        if matches!(
            contracts::effect(lib, name),
            Some(contracts::Effect::TextRead)
        ) {
            if let Some(i) = self.argument(args, "text", 0) {
                self.direct(state, &vs[i], at);
            }
            return Some(if name == "MeasureText" {
                vec![Value::default()]
            } else {
                vec![]
            });
        }
        if name == "CloseWindow" {
            if let Some(id) = self.foreign_title_slot {
                state.write(&Place::root(id), &Value::default(), false);
            }
            return Some(vec![]);
        }
        let i = self.argument(args, "title", if name == "InitWindow" { 2 } else { 0 })?;
        let title = vs[i].clone();
        self.direct(state, &title, at);
        let id = if let Some(id) = self.foreign_title_slot {
            id
        } else {
            let id = self.object(state, 0, at, 12, Storage::Static, Value::default());
            self.foreign_title_slot = Some(id);
            id
        };
        state.cells.entry(id).or_insert(Cell {
            value: Value::default(),
            life: LIVE,
            uncertain: false,
            released: None,
        });
        state.globals.insert(id);
        if name == "InitWindow" {
            // InitWindow keeps the previous pointer for nil/empty titles. A
            // runtime string may be empty, so retain both alternatives.
            let actual = self.role(args[i], "value").unwrap_or(args[i]);
            let nonempty = matches!(self.r.constant_value(actual,&Substitutions::new(),None),Some(Constant::Text(s)) if !s.is_empty());
            if !nonempty {
                let old = state.read(&Place::root(id));
                self.direct(state, &old, at);
                let mut joined = old;
                joined.join(&title);
                state.write(&Place::root(id), &joined, false);
                return Some(vec![]);
            }
        }
        state.write(&Place::root(id), &title, false);
        Some(vec![])
    }
}
