import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

final class Ledger {
    private record Entry(int id, int units) {}
    record Summary(int count, long total, boolean rolledBack) {}

    private final Map<Integer, Integer> balances = new HashMap<>();
    private long total = 7;

    private Ledger() {
        balances.put(0, 7);
    }

    private boolean apply(List<Entry> entries) {
        Map<Integer, Integer> pending = new HashMap<>();
        long subtotal = 0;
        for (Entry entry : entries) {
            if (balances.containsKey(entry.id()) || pending.containsKey(entry.id())) {
                return false;
            }
            pending.put(entry.id(), entry.units());
            subtotal += entry.units();
        }
        for (Entry entry : entries) {
            balances.put(entry.id(), entry.units());
        }
        total += subtotal;
        return true;
    }

    private static List<Entry> batch(int start, int size, boolean duplicate) {
        List<Entry> entries = new ArrayList<>();
        for (int offset = 0; offset < size; offset++) {
            int id = duplicate && offset == size - 1 ? 1 : start + offset;
            entries.add(new Entry(id, id % 1000 + 1));
        }
        return entries;
    }

    static Summary exercise(int size) {
        Ledger ledger = new Ledger();
        if (!ledger.apply(batch(1, size, false))) {
            throw new IllegalStateException("initial batch unexpectedly rejected");
        }
        boolean rolledBack = !ledger.apply(batch(size + 1, size, true));
        return new Summary(ledger.balances.size(), ledger.total, rolledBack);
    }
}
