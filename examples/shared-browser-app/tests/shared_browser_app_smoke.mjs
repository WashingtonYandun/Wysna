import assert from "node:assert/strict";
import { pathToFileURL } from "node:url";
import { evaluateQuote } from "../web/quote.js";

const modulePath = process.argv[2];
assert.ok(modulePath, "expected the generated Wysna browser facade path");

const { createWysna } = await import(pathToFileURL(modulePath).href);
const library = await createWysna();
try {
  assert.equal(library.exports.calculate_total_cents(3n, 1299n), 3897n);
  const total = evaluateQuote(library.exports, 3n, 1299n);
  assert.equal(total, 3897n);

  const quantities = [0n, 1n, 1000n, 1001n];
  const prices = [-1n, 0n, 100000000n, 100000001n];
  for (const quantity of quantities) {
    for (const unitPrice of prices) {
      const validQty = quantity >= 1n && quantity <= 1000n;
      const validPrice = unitPrice >= 0n && unitPrice <= 100000000n;
      if (validQty && validPrice) {
        assert.equal(evaluateQuote(library.exports, quantity, unitPrice), quantity * unitPrice);
      } else if (!validQty) {
        assert.throws(
          () => evaluateQuote(library.exports, quantity, unitPrice),
          /Quantity must be between 1 and 1000\./,
        );
      } else {
        assert.throws(
          () => evaluateQuote(library.exports, quantity, unitPrice),
          /Unit price must be between 0 and 100000000 cents\./,
        );
      }
    }
  }

  for (const outOfRange of [-(1n << 63n) - 1n, 1n << 63n]) {
    assert.throws(
      () => evaluateQuote(library.exports, outOfRange, 100n),
      /signed 64-bit integer ABI range/,
    );
    assert.throws(
      () => evaluateQuote(library.exports, 1n, outOfRange),
      /signed 64-bit integer ABI range/,
    );
  }

  console.log(total.toString());
} finally {
  library.destroy();
}
