const { exercise } = require("./ledger.cjs");

const size = Number(process.argv[2]);
if (!Number.isSafeInteger(size) || size < 1 || size > 1000000) process.exit(2);
console.log(...exercise(size));
