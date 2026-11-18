import { createWysna } from "../build/pricing.mjs";
import {
  evaluateQuote,
  formatCents,
  loadQuoteFromServer,
  parseQuoteQueryId,
  parseStrictBigInt,
  saveQuoteToServer,
} from "./quote.js";

const form = document.querySelector("#quote-form");
const calculateButton = document.querySelector("#calculate");
const saveButton = document.querySelector("#save-quote");
const result = document.querySelector("#result");
const savedStatus = document.querySelector("#saved-status");
const error = document.querySelector("#error");

const loadStartedAt = performance.now();
let library;
try {
  library = await createWysna();
  result.dataset.readyMs = (performance.now() - loadStartedAt).toFixed(2);
  calculateButton.disabled = false;
  calculateButton.textContent = "Calculate total";
  saveButton.disabled = false;
  result.textContent = "Wysna is ready.";
} catch (loadError) {
  error.textContent = "Could not load the Wysna WebAssembly module: " + loadError.message;
}

function readFormInputs() {
  const quantity = parseStrictBigInt(form.elements.quantity.value, "Quantity");
  const unitPriceCents = parseStrictBigInt(form.elements.unitPriceCents.value, "Unit price");
  return { quantity, unitPriceCents };
}

form.addEventListener("submit", event => {
  event.preventDefault();
  error.textContent = "";
  if (!library) return;

  try {
    const { quantity, unitPriceCents } = readFormInputs();
    const total = evaluateQuote(library.exports, quantity, unitPriceCents);
    result.textContent = "Estimated total: " + formatCents(total);
  } catch (calculationError) {
    error.textContent = calculationError.message;
  }
});

saveButton.addEventListener("click", async () => {
  error.textContent = "";
  if (!library) return;

  try {
    const { quantity, unitPriceCents } = readFormInputs();
    const total = evaluateQuote(library.exports, quantity, unitPriceCents);
    result.textContent = "Estimated total: " + formatCents(total);
    const saved = await saveQuoteToServer(window.fetch.bind(window), quantity, unitPriceCents);
    if (typeof window.history?.replaceState === "function") {
      const url = new URL(window.location.href);
      url.searchParams.set("quote", String(saved.id));
      window.history.replaceState(null, "", url.toString());
    }
    savedStatus.textContent = `Saved quote #${saved.id}: ${saved.total_cents} cents`;
  } catch (saveError) {
    error.textContent = saveError.message;
  }
});

const savedId = parseQuoteQueryId(window.location.search);
if (library && savedId !== null) {
  try {
    const loaded = await loadQuoteFromServer(window.fetch.bind(window), savedId);
    form.elements.quantity.value = String(loaded.quantity);
    form.elements.unitPriceCents.value = String(loaded.unit_price_cents);
    const total = BigInt(loaded.total_cents);
    result.textContent = "Estimated total: " + formatCents(total);
    savedStatus.textContent = `Loaded quote #${loaded.id}: ${loaded.total_cents} cents`;
  } catch (loadSavedError) {
    error.textContent = loadSavedError.message;
  }
} else {
  form.requestSubmit();
}
