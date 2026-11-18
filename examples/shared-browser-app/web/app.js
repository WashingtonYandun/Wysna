import { createWysna } from "../build/pricing.mjs";
import { evaluateQuote, formatCents, parseStrictBigInt } from "./quote.js";

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

form.addEventListener("submit", event => {
  event.preventDefault();
  error.textContent = "";
  if (!library) return;

  try {
    const quantity = parseStrictBigInt(form.elements.quantity.value, "Quantity");
    const unitPriceCents = parseStrictBigInt(form.elements.unitPriceCents.value, "Unit price");
    const total = evaluateQuote(library.exports, quantity, unitPriceCents);
    result.textContent = "Estimated total: " + formatCents(total);
  } catch (calculationError) {
    error.textContent = calculationError.message;
  }
});

form.requestSubmit();
