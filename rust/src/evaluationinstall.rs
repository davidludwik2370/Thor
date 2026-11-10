//! Install the independent evaluator's results into the ordinary build path.
use super::*;

impl Project {
    pub(super) fn schedule_evaluations(&mut self) -> Result<()> {
        if self.evaluation_scheduled {
            return Ok(());
        }
        let mut scheduler =
            crate::evaluation::Scheduler::new(self, std::time::Duration::from_secs(5));
        scheduler.evaluate_all()?;
        let values = std::mem::take(&mut scheduler.results);
        let cases = std::mem::take(&mut scheduler.cases);
        let witnesses = std::mem::take(&mut scheduler.scalar_witnesses);
        let skips = scheduler
            .inactive
            .iter()
            .map(|node| (*node, scheduler.unchecked.contains(node)))
            .collect();
        drop(scheduler);
        self.evaluated = values;
        self.specialized = cases;
        self.evaluation_skips = skips;
        self.evaluation_witnesses = witnesses;
        self.evaluation_scheduled = true;
        Ok(())
    }
}
