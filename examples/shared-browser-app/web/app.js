import { createWysna } from "../build/pricing.mjs";

const form = document.querySelector("#quote-form");
const calculateButton = document.querySelector("#calculate");
const result = document.querySelector("#result");
const error = document.querySelector("#error");

let library;
try {
  library = await createWysna();
  calculateButton.disabled = false;
  calculateButton.textContent = "Calculate total";
  result.textContent = "Wysna is ready.";
} catch (loadError) {
  error.textContent = "Could not load the Wysna WebAssembly module: " + loadError.message;
}

function formatCents(cents) {
  const whole = cents / 100n;
  const fractional = (cents % 100n).toString().padStart(2, "0");
  return "$" + whole.toString() + "." + fractional;
}

form.addEventListener("submit", event => {
  event.preventDefault();
  error.textContent = "";
  if (!library) return;

  try {
    const quantity = BigInt(form.elements.quantity.value);
    const unitPriceCents = BigInt(form.elements.unitPriceCents.value);
    if (quantity < 1n || quantity > 1000n) {
      throw new Error("Quantity must be between 1 and 1000.");
    }
    if (unitPriceCents < 0n || unitPriceCents > 100000000n) {
      throw new Error("Unit price must be between 0 and 100000000 cents.");
    }

    const total = library.exports.calculate_total_cents(quantity, unitPriceCents);
    result.textContent = "Estimated total: " + formatCents(total);
  } catch (calculationError) {
    error.textContent = calculationError.message;
  }
});

form.requestSubmit();
