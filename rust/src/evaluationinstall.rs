//! Install the independent evaluator's results into the ordinary build path.
use super::*;

impl Project {
    pub(super) fn schedule_evaluations(&mut self) -> Result<()> {
        if self.evaluation_scheduled {
            return Ok(());
        }
        let mut scheduler =
            crate::evaluation::Scheduler::new(self, self.options.run_timeout);
        let result = scheduler.evaluate_all();
        if self.options.cache_report {
            let report=scheduler.cache_report();
            println!("{}",json!({"thor_cache":report["stats"],"thor_prepared_hits":report["prepared_hits"]}));
        }
        result?;
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
