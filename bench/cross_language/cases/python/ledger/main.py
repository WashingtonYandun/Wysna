import sys
from ledger import exercise

try:
    size = int(sys.argv[1])
    if not 1 <= size <= 1_000_000:
        sys.exit(2)
except (IndexError, ValueError):
    sys.exit(2)

count, total, rolled_back = exercise(size)
print(count, total, str(rolled_back).lower())
