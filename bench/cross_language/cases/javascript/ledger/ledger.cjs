class Ledger {
  constructor() {
    this.balances = new Map([[0, 7]]);
    this.total = 7;
  }

  apply(entries) {
    const pending = new Map();
    let subtotal = 0;
    for (const [id, units] of entries) {
      if (this.balances.has(id) || pending.has(id)) return false;
      pending.set(id, units);
      subtotal += units;
    }
    for (const [id, units] of entries) this.balances.set(id, units);
    this.total += subtotal;
    return true;
  }
}

function batch(start, size, duplicate) {
  const entries = [];
  for (let offset = 0; offset < size; offset++) {
    const id = duplicate && offset === size - 1 ? 1 : start + offset;
    entries.push([id, id % 1000 + 1]);
  }
  return entries;
}

function exercise(size) {
  const ledger = new Ledger();
  if (!ledger.apply(batch(1, size, false))) throw new Error("commit failed");
  const rolledBack = !ledger.apply(batch(size + 1, size, true));
  return [ledger.balances.size, ledger.total, rolledBack];
}

module.exports = { exercise };
