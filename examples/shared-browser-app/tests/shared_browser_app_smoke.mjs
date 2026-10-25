import assert from "node:assert/strict";
import { pathToFileURL } from "node:url";

const modulePath = process.argv[2];
assert.ok(modulePath, "expected the generated Wysna browser facade path");

const { createWysna } = await import(pathToFileURL(modulePath).href);
const library = await createWysna();
const total = library.exports.calculate_total_cents(3n, 1299n);
assert.equal(total, 3897n);
library.destroy();
console.log(total.toString());
