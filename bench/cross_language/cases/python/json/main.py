import json
import sys


def main(path):
    with open(path, "rb") as file:
        records = json.load(file)
    if not isinstance(records, list):
        raise ValueError("expected an array")
    accepted = rejected = total = 0
    for item in records:
        row_id, units, active = item["id"], item["units"], item["active"]
        if type(row_id) is not int or type(units) is not int or type(active) is not bool:
            raise ValueError("invalid record")
        if row_id > 0 and active and 0 <= units <= 1000:
            accepted += 1
            total += units
        else:
            rejected += 1
    print(accepted, rejected, total)


try:
    main(sys.argv[1])
except (OSError, ValueError, KeyError, TypeError):
    sys.exit(2)
