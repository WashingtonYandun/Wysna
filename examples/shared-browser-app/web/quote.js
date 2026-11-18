export const INT64_MIN = -(1n << 63n);
export const INT64_MAX = (1n << 63n) - 1n;

export function fitsSignedInt64(value) {
  return typeof value === "bigint" && value >= INT64_MIN && value <= INT64_MAX;
}

export function parseStrictBigInt(rawValue, label) {
  const normalized = String(rawValue ?? "").trim();
  if (!/^-?\d+$/.test(normalized)) {
    throw new Error(`${label} must be an integer.`);
  }
  return BigInt(normalized);
}

export function formatCents(cents) {
  const whole = cents / 100n;
  const fractional = (cents % 100n).toString().padStart(2, "0");
  return "$" + whole.toString() + "." + fractional;
}

export function evaluateQuote(exportsObject, quantity, unitPriceCents) {
  if (!fitsSignedInt64(quantity)) {
    throw new Error("Quantity is outside the signed 64-bit integer ABI range.");
  }
  if (!fitsSignedInt64(unitPriceCents)) {
    throw new Error("Unit price is outside the signed 64-bit integer ABI range.");
  }
  const result = exportsObject.calculate_validated_total_cents(quantity, unitPriceCents);
  if (result === -1n) {
    throw new Error("Quantity must be between 1 and 1000.");
  }
  if (result === -2n) {
    throw new Error("Unit price must be between 0 and 100000000 cents.");
  }
  if (result < 0n) {
    throw new Error("Quote inputs are invalid.");
  }
  return result;
}

export function parseQuoteQueryId(searchString) {
  const params = new URLSearchParams(searchString || "");
  const raw = params.get("quote");
  if (!raw || !/^[1-9]\d*$/.test(raw)) {
    return null;
  }
  return raw;
}

export async function saveQuoteToServer(fetchFn, quantity, unitPriceCents) {
  if (!fitsSignedInt64(quantity) || !fitsSignedInt64(unitPriceCents)) {
    throw new Error("Quote values exceed signed 64-bit integer bounds.");
  }
  const response = await fetchFn("/quotes", {
    method: "POST",
    headers: { "Content-Type": "application/json", "Accept": "application/json" },
    body: `{"quantity":${quantity.toString()},"unit_price_cents":${unitPriceCents.toString()}}`,
  });
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload?.error || `Failed to save quote (${response.status})`);
  }
  return payload;
}

export async function loadQuoteFromServer(fetchFn, quoteId) {
  const response = await fetchFn(`/quotes/${quoteId}`, {
    method: "GET",
    headers: { "Accept": "application/json" },
  });
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload?.error || `Failed to load quote #${quoteId} (${response.status})`);
  }
  return payload;
}
