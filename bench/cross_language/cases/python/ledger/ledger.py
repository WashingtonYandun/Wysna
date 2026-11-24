class Ledger:
    def __init__(self):
        self.balances = {0: 7}
        self.total = 7

    def apply(self, entries):
        pending = {}
        subtotal = 0
        for row_id, units in entries:
            if row_id in self.balances or row_id in pending:
                return False
            pending[row_id] = units
            subtotal += units
        for row_id, units in entries:
            self.balances[row_id] = units
        self.total += subtotal
        return True


def batch(start, size, duplicate):
    entries = []
    for offset in range(size):
        row_id = 1 if duplicate and offset == size - 1 else start + offset
        entries.append((row_id, row_id % 1000 + 1))
    return entries


def exercise(size):
    ledger = Ledger()
    if not ledger.apply(batch(1, size, False)):
        raise RuntimeError("commit failed")
    rolled_back = not ledger.apply(batch(size + 1, size, True))
    return len(ledger.balances), ledger.total, rolled_back
