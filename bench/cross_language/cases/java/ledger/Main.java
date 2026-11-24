public final class Main {
    public static void main(String[] args) {
        if (args.length != 1) {
            System.exit(2);
        }
        int size;
        try {
            size = Integer.parseInt(args[0]);
        } catch (NumberFormatException exception) {
            System.exit(2);
            return;
        }
        if (size < 1 || size > 1_000_000) {
            System.exit(2);
        }
        Ledger.Summary result = Ledger.exercise(size);
        System.out.println(result.count() + " " + result.total() + " " + result.rolledBack());
    }
}
