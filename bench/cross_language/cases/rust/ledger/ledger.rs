use std::collections::HashMap;

pub struct Summary {
    pub count: usize,
    pub total: i64,
    pub rolled_back: bool,
}

struct Ledger {
    balances: HashMap<i64, i64>,
    total: i64,
}

impl Ledger {
    fn apply(&mut self, entries: &[(i64, i64)]) -> bool {
        let mut pending = HashMap::new();
        let mut subtotal = 0;
        for &(id, units) in entries {
            if self.balances.contains_key(&id) || pending.insert(id, units).is_some() {
                return false;
            }
            subtotal += units;
        }
        for &(id, units) in entries {
            self.balances.insert(id, units);
        }
        self.total += subtotal;
        true
    }
}

fn batch(start: i64, size: i64, duplicate: bool) -> Vec<(i64, i64)> {
    let mut entries = Vec::new();
    for offset in 0..size {
        let id = if duplicate && offset == size - 1 { 1 } else { start + offset };
        entries.push((id, id % 1000 + 1));
    }
    entries
}

pub fn exercise(size: i64) -> Summary {
    let mut ledger = Ledger { balances: HashMap::from([(0, 7)]), total: 7 };
    assert!(ledger.apply(&batch(1, size, false)));
    let rolled_back = !ledger.apply(&batch(size + 1, size, true));
    Summary { count: ledger.balances.len(), total: ledger.total, rolled_back }
}
