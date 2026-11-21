import { createWysna } from "./build/web.mjs";

const lib = await createWysna();
try {
  const discount = lib.exports.compute_discount_cents(12500n, 2n);
  const summaryJson = lib.exports.evaluate_cart_json(
    JSON.stringify({ customer: "WySofia", subtotal_cents: 12500, tier_code: 2 })
  );
  console.log("discount_cents:", discount.toString());
  console.log("summary:", JSON.parse(summaryJson));
} finally {
  lib.destroy();
}
