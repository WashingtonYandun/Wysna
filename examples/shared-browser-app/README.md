# Shared browser application

This project uses one Wysna pricing function from two entry points:

- The native command validates process arguments and prints the total in cents.
- The browser page validates form input and formats the result for display.
- Both hosts call the function in src/pricing.wysna.

Build both targets from the repository root:

    python main.py project build --manifest examples/shared-browser-app/wysna.toml --target app --output examples/shared-browser-app/build/quote
    python main.py project build --manifest examples/shared-browser-app/wysna.toml --target web --output examples/shared-browser-app/build/pricing.mjs

Run the native command with a quantity and a unit price in cents:

    examples/shared-browser-app/build/quote 3 1299

The command prints 3897. Serve the project directory to use the browser interface:

    python3 -m http.server --directory examples/shared-browser-app 8000

Then open http://localhost:8000/web/. The build emits the facade, Emscripten loader,
and WebAssembly file together in the build directory. Emscripten 5.0.1 must be
installed to build the web target.
