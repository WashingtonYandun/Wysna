const fs = require("node:fs");

function main(path) {
  const records = JSON.parse(fs.readFileSync(path, "utf8"));
  if (!Array.isArray(records)) throw new Error("expected an array");
  let accepted = 0;
  let rejected = 0;
  let total = 0;
  for (const { id, units, active } of records) {
    if (!Number.isSafeInteger(id) || !Number.isSafeInteger(units) || typeof active !== "boolean") {
      throw new Error("invalid record");
    }
    if (id > 0 && active && units >= 0 && units <= 1000) {
      accepted += 1;
      total += units;
    } else {
      rejected += 1;
    }
  }
  console.log(accepted, rejected, total);
}

try {
  main(process.argv[2]);
} catch {
  process.exit(2);
}
