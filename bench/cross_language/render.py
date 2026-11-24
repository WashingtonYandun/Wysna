#!/usr/bin/env python3
"""Render the checked benchmark measurements as a browser-portable report."""
import argparse
from html import escape
import json
import os
from pathlib import Path

NAMES = {
    "wysna_baseline": "Wysna de referencia",
    "wysna": "Wysna candidato",
    "go": "Go",
    "rust": "Rust",
    "python": "Python",
    "javascript": "JavaScript",
    "java": "Java",
}
CASES = {
    "startup": "Arranque CLI",
    "json_1000": "JSON · 1.000 registros",
    "json_10000": "JSON · 10.000 registros",
    "ledger_1000": "Rollback · 2.000 operaciones",
    "ledger_20000": "Rollback · 40.000 operaciones",
}
BUILD_KINDS = {
    "release_rebuild": "Binario release",
    "bytecode_rebuild": "Compilación JVM",
    "syntax_check": "Comprobación sintáctica",
}
STYLES = """
:root { color-scheme: light; font-family: system-ui, sans-serif; color: #152738; background: #f4f7fa; }
*, *::before, *::after { box-sizing: border-box; }
body { margin: 0; line-height: 1.55; }
main, header > div { width: min(1120px, calc(100% - 34px)); margin: 0 auto; }
header { padding: 40px 0; background: #112e44; color: white; }
header p { color: #d0e0ed; max-width: 74ch; }
h1 { font-size: clamp(1.95rem, 5vw, 3rem); line-height: 1.1; letter-spacing: -.035em; margin: 0; }
main { padding: 24px 0 48px; }
h2 { margin: 26px 0 10px; letter-spacing: -.016em; }
p { max-width: 89ch; }
a { color: #145781; }
nav { display: flex; flex-wrap: wrap; gap: 8px 20px; }
.scroll { overflow-x: auto; margin: 12px 0 18px; border: 1px solid #d9e1e9; border-radius: 6px; background: white; }
table { border-collapse: collapse; min-width: 700px; width: 100%; font-variant-numeric: tabular-nums; font-size: .91rem; }
caption { padding: 10px; text-align: left; color: #52697b; }
th, td { padding: 9px 11px; border-bottom: 1px solid #e0e8ee; text-align: right; white-space: nowrap; }
th:first-child { text-align: left; }
thead { background: #eaf0f5; }
tbody tr:last-child > * { border-bottom: 0; }
td[data-focus] { background: #eaf5ef; font-weight: 720; color: #165246; }
small { display: block; color: #536a7b; }
code, dd { overflow-wrap: anywhere; }
dt { font-weight: 680; }
dd { margin-left: 0; margin-bottom: 8px; }
"""


def number(value, digits=2):
    return f"{value:,.{digits}f}".translate(str.maketrans(",.", ".,"))


def table(caption, headings, rows):
    columns = "".join(f'<th scope="col">{title}</th>' for title in headings)
    rendered = []
    for label, values in rows:
        cells = []
        for name, value in values:
            emphasis = " data-focus" if name == "wysna" else ""
            cells.append(f"<td{emphasis}>{value}</td>")
        rendered.append(f'<tr><th scope="row">{label}</th>{"".join(cells)}</tr>')
    return (
        f'<div class="scroll" tabindex="0" role="region" aria-label="{escape(caption)}"><table>'
        f'<caption>{caption}</caption><thead><tr>{columns}</tr></thead>'
        f'<tbody>{"".join(rendered)}</tbody></table></div>'
    )


def runtime_tables(result, keys):
    timing_rows = []
    memory_rows = []
    for key, name in CASES.items():
        medians = []
        rss = []
        for engine in keys:
            stats = result["runtime"][key][engine]
            medians.append((engine, f'{number(stats["p50_ms"])}<small>{number(stats["p95_ms"])}</small>'))
            rss.append((engine, number(stats["p50_rss_bytes"] / 2**20)))
        timing_rows.append((name, medians))
        memory_rows.append((name, rss))
    headings = ["Carga", *(NAMES[key] for key in keys)]
    return (
        table("Latencia en milisegundos · mediana arriba y p95 debajo", headings, timing_rows),
        table("Mediana del RSS pico en MiB por proceso", headings, memory_rows),
    )


def build_tables(result, keys):
    edit_rows = []
    size_rows = []
    for key in keys:
        edit = result["edited_cli"][key]
        cold = result["cold_json_build_ms"].get(key)
        edit_rows.append((NAMES[key], [
            ("", BUILD_KINDS[edit["kind"]]),
            (key, number(edit["p50_ms"])),
            ("", number(cold) if cold is not None else "—"),
        ]))
        if key == "java":
            sizes = result["java_class_bytes"]
            artifact = "Archivos .class"
        else:
            sizes = result["binary_bytes"].get(key)
            artifact = "Ejecutable nativo" if sizes else "Script con runtime"
        cells = [("", artifact)]
        for workload in ("startup", "json", "ledger"):
            cells.append((key, number(sizes[workload] / 1024, 1) if sizes else "—"))
        size_rows.append((NAMES[key], cells))
    return (
        table("Sondas de compilación y verificación",
              ["Lenguaje", "Operación", "Edición p50 (ms)", "JSON frío (ms)"], edit_rows),
        table("Tamaño de los artefactos producidos en KiB",
              ["Lenguaje", "Artefacto", "CLI", "JSON", "Rollback"], size_rows),
    )


def baseline_summary(result):
    if "wysna_baseline" not in result["versions"]:
        return ""
    rows = []
    for key, name in CASES.items():
        previous = result["runtime"][key]["wysna_baseline"]["p50_ms"]
        current = result["runtime"][key]["wysna"]["p50_ms"]
        rows.append((name, [("", number(previous)), ("wysna", number(current)),
                            ("", f"{number(current / previous)}×")]))
    prior_edit = result["edited_cli"]["wysna_baseline"]["p50_ms"]
    edit = result["edited_cli"]["wysna"]["p50_ms"]
    rows.append(("Recompilar CLI editado", [("", number(prior_edit)), ("wysna", number(edit)),
                                          ("", f"{number(edit / prior_edit)}×")]))
    return table("SDK de referencia y candidato en la misma corrida · medianas en ms",
                 ["Medida", "Referencia", "Candidato", "Candidato / referencia"], rows)


def encoding_section(encoding, data_link):
    if not encoding:
        return ""
    fixtures = {
        "unescaped_unicode": "Unicode sin escapes",
        "mixed_escapes": "Unicode con escapes",
    }
    rows = []
    for key, name in fixtures.items():
        samples = encoding["fixtures"][key]
        before = samples["reference"]["p50_ms"]
        after = samples["candidate"]["p50_ms"]
        rows.append((name, [
            ("", number(samples["input_bytes"], 0)),
            ("", number(before)),
            ("wysna", number(after)),
            ("", f"{number(after / before)}×"),
        ]))
    matrix = table("Serialización de cadenas JSON · mediana en ms",
                   ["Entrada", "Bytes", "PR #189", "Candidato", "Candidato / PR #189"], rows)
    return f"""<h3>Serialización de JSON en Wysna</h3>
<p>Se contrastó el SDK del PR #189 contra la optimización de tramos de texto. Cada celda resume 30 procesos y cada salida se comparó con la cadena JSON esperada.</p>
{matrix}
<p><a href="{data_link}">Muestras de serialización</a></p>"""


def render(result, result_link, encoding=None, encoding_link=None):
    keys = [key for key in NAMES if key in result["versions"]]
    protocol = result["protocol"]
    samples = sum(stats["n"] for variants in result["runtime"].values() for stats in variants.values())
    checks = sum(result["validation_checks"].values())
    latency, memory = runtime_tables(result, keys)
    builds, size = build_tables(result, keys)
    versions = "".join(f"<dt>{NAMES[key]}</dt><dd>{escape(result['versions'][key])}</dd>" for key in keys)
    sdks = ""
    for key, sdk in result["wysna_sdks"].items():
        revision = escape(sdk["source_commit"] or "sin manifest")
        sdks += f"<dt>{NAMES[key]} · revisión</dt><dd><code>{revision}</code></dd>"
    return f"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Wysna · Comparativo de rendimiento con Java</title>
<style>{STYLES}</style>
</head>
<body>
<header><div>
<h1>Wysna frente a Go, Rust, Python, JavaScript y Java</h1>
<p>{number(samples, 0)} procesos medidos · {checks} comprobaciones de entrada y salida · 0 llamadas a modelos</p>
</div></header>
<main>
<nav aria-label="Secciones"><a href="#progreso">Cambio de Wysna</a><a href="#ejecucion">Ejecución</a>
<a href="#compilacion">Compilación</a><a href="#metodo">Método y datos</a></nav>
<section id="progreso"><h2>Cambio de rendimiento de Wysna</h2>
{baseline_summary(result)}
<p>Un cociente por debajo de 1 indica una reducción de tiempo. Las diferencias pequeñas requieren repetir la medición.</p>
</section>
<section id="ejecucion"><h2>Tiempo y memoria</h2>
<p>La latencia incluye el arranque de un proceso nuevo, su trabajo y la escritura de la salida. La JVM de Java arranca en cada muestra.</p>
{latency}{memory}
{encoding_section(encoding, encoding_link)}
</section>
<section id="compilacion"><h2>Compilación y tamaño</h2>
{builds}
<p>La compilación fría de JSON tiene una muestra por toolchain. La edición del CLI tiene {protocol["build_samples"]} muestras. La columna de Java mide <code>javac</code>.</p>
{size}
<p>Java usa la JVM y, para JSON, Jackson Core 2.21.5 de {number(result["java_dependency"]["bytes"] / 1024, 1)} KiB. Los tamaños de los ejecutables corresponden a cada perfil release medido.</p>
</section>
<section id="metodo"><h2>Método y datos</h2>
<p>{protocol["samples"]} procesos registrados por celda y {protocol["warmups"]} calentamientos. Orden serial rotado con semilla {protocol["order_seed"]}. JSON comprueba tipos y agrega registros, y rollback conserva el estado confirmado al rechazar el duplicado. <code>wait4</code> registra RSS pico.</p>
<p>{escape(result["hardware"].replace(chr(10), " · "))}<br>{escape(result["os"].replace(chr(10), " · "))}</p>
<dl>{versions}{sdks}</dl>
<p><a href="{result_link}">Muestras y resultados JSON</a> · <a href="README.md">Código y reproducción</a></p>
</section>
</main>
</body>
</html>
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("data", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--encoding", type=Path)
    args = parser.parse_args()
    result = json.loads(args.data.read_text(encoding="utf-8"))
    encoding = json.loads(args.encoding.read_text(encoding="utf-8")) if args.encoding else None
    output_dir = args.output.resolve().parent
    result_link = escape(os.path.relpath(args.data, start=output_dir))
    encoding_link = escape(os.path.relpath(args.encoding, start=output_dir)) if args.encoding else None
    args.output.write_text(render(result, result_link, encoding, encoding_link), encoding="utf-8")


if __name__ == "__main__":
    main()
