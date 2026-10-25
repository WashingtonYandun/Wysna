import assert from "node:assert/strict";
import { pathToFileURL } from "node:url";

const modulePath = process.argv[2];
assert.ok(modulePath, "expected the generated Wysna browser facade path");
const { createWysna } = await import(pathToFileURL(modulePath).href);

const first = await createWysna();
assert.equal(first.exports.add(20n, 22n), 42n);
assert.equal(first.exports.echo_int(-(1n << 62n)), -(1n << 62n));
assert.equal(first.exports.echo_float(1.25), 1.25);
assert.equal(first.exports.echo_bool(true), true);
assert.equal(first.exports.echo_bool(false), false);
assert.equal(first.exports.echo_text("wysna\u0000🛰️"), "wysna\u0000🛰️");
assert.equal(first.exports.noop(7n), undefined);
assert.throws(
  () => first.exports.echo_int(7),
  error => error.code === "INVALID_ARGUMENT",
);
assert.throws(
  () => first.exports.echo_int(1n << 63n),
  error => error.code === "INVALID_ARGUMENT",
);
first.destroy();
first.destroy();
assert.throws(
  () => first.exports.add(1n, 2n),
  error => error.code === "INVALID_STATE",
);

const second = await createWysna();
assert.equal(second.exports.add(1n, 2n), 3n);
second.destroy();
