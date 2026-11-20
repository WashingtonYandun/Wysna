<p align="center">
  <img src="assets/wawi.jpg" alt="Wawi, mascota de Wysna" width="112" />
</p>

# Wysna

**Un lenguaje compilado para automatizaciones y herramientas que personas y agentes pueden mantener juntos.**

Wysna tiene tipado estático y genera ejecutables nativos a través de C99. Puedes usarlo para crear una CLI que procesa JSON, validar datos antes de guardarlos o compartir una regla de negocio entre una herramienta local y el navegador. El compilador también expone la estructura del código, el impacto de un cambio y los diagnósticos en formatos que un editor o un agente puede consultar.

[Descargas](https://github.com/WashingtonYandun/Wysna/releases) · [Primer proyecto](#primer-proyecto) · [Grafo de impacto](#grafo-de-impacto) · [Documentación](docs/README.md)

[![CI en main](https://github.com/WashingtonYandun/Wysna/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/WashingtonYandun/Wysna/actions/workflows/ci.yml)
[![Última versión publicada](https://img.shields.io/github/v/release/WashingtonYandun/Wysna)](https://github.com/WashingtonYandun/Wysna/releases/latest)
[![Licencia Apache 2.0](https://img.shields.io/badge/licencia-Apache%202.0-blue)](LICENSE)

## Instalación

Elige la distribución de tu plataforma en [GitHub Releases](https://github.com/WashingtonYandun/Wysna/releases). Los recorridos del SDK y del grafo de proyecto de esta guía requieren **Wysna 0.8.0 o posterior**.

| Distribución | Cuándo usarla | Plataformas |
| --- | --- | --- |
| [SDK nativo](docs/NATIVE_SDK.md) | Proyectos C99, pruebas y herramientas para agentes. Incluye el compilador escrito en Wysna, Zig y la biblioteca estándar. Funciona sin Python. | Windows x64, macOS arm64 y Linux x64 |
| [Seed](docs/SEED_DISTRIBUTION.md) | WebAssembly/WASI, LLVM directo y el compilador de referencia e intérprete (`wysna repl`, oráculo AST). Sus instaladores incluyen CPython y Zig. | Windows x64, macOS arm64 y Linux x64 |

Para usar el SDK nativo, extrae el archivo y añade su carpeta `bin` al PATH. En Windows también puedes abrir `open-terminal.cmd` desde la carpeta extraída. Verifica la descarga con `SHA256SUMS` y confirma la instalación con `wysna --version`.

## Primer proyecto

Guarda este programa como `quote.wysna`. El contrato exige una cantidad positiva y un precio no negativo. El cálculo usa enteros para representar los centavos.

```wysna
pub fn quote_total(quantity: int, unit_price_cents: int) -> int
    requires quantity > 0, unit_price_cents >= 0
    ensures result == quantity * unit_price_cents
{
    return quantity * unit_price_cents
}

print(quote_total(3, 1299))
```

```sh
wysna check quote.wysna
wysna run quote.wysna
wysna build quote.wysna -o quote
```

El programa imprime `3897`. Puedes distribuir el ejecutable sin instalar Python en la máquina de destino. Si enlazas bibliotecas externas, distribúyelas según el contrato de tu plataforma.

Para generar la estructura de un proyecto con manifiesto y una prueba inicial:

```sh
wysna project new my-app
cd my-app
wysna project lock
wysna project test --frozen
```

El SDK nativo genera y verifica los locks tanto en proyectos sin dependencias como en proyectos con dependencias de paquetes locales. La [guía de proyectos](docs/PROJECTS.md) describe los manifiestos, las dependencias locales y los destinos.

## Grafo de impacto

El grafo responde preguntas sobre código comprobado por el compilador. En el ejemplo de [cotizaciones](examples/shared-browser-app/README.md), la CLI, el servicio y la web comparten una regla. Desde un checkout del repositorio puedes consultar qué volver a probar si cambia esa regla:

```sh
wysna graph --manifest examples/shared-browser-app/wysna.toml --symbol quote_validation_code --rel impact
```

El informe identifica las entradas afectadas y muestra las relaciones que llegan hasta el símbolo. También señala fronteras del análisis, como las llamadas desde JavaScript o el código C enlazado. El mismo informe se puede pedir con **Wysna: Show Project Impact** en VS Code o con `wysna_graph` a través de MCP.

| Tarea | Herramienta |
| --- | --- |
| Localizar dependientes y ver el código relevante | `graph` e `inspect --with-deps` |
| Procesar errores del compilador de forma automática | `check --output-format=json-v1` |
| Aplicar una edición por símbolo sobre la revisión esperada | `patch --expect-graph-hash` |
| Consultar efectos y describir entradas de herramientas | `explain` y `schema` |
| Conectar un editor o un agente | `lsp`, `mcp` y la [extensión de VS Code](editors/vscode-wysna/README.md) |

La [guía para agentes](docs/AI_AGENT_GUIDE.md) documenta el flujo y los formatos. La [evaluación A/B](bench/ai_eval/README.md) permite comparar lectura de archivos con consultas al grafo elegidas por el modelo.

## Ejemplos

| Proyecto | Qué muestra |
| --- | --- |
| [Auditoría operativa](examples/wysofia-ops/README.md) | Valida eventos JSON y calcula un informe. Tiene un destino opcional con SQLite. |
| [Sincronización de mediciones](examples/automation-sync/README.md) | Descarga un lote acotado de datos y lo importa a SQLite en una transacción. |
| [Cotizaciones](examples/shared-browser-app/README.md) | Comparte reglas de validación y cálculo entre CLI, servicio y WebAssembly. |

## Estado y arquitectura

Wysna está en desarrollo **pre-1.0**. C99 es el destino nativo principal. Seed es la implementación de referencia en Python y conserva perfiles adicionales. El SDK nativo usa el compilador autoalojado, escrito en Wysna. Ambos caminos nativos pasan por una representación intermedia verificada antes de generar C. La [arquitectura](docs/COMPILER_ARCHITECTURE.md) y la [matriz de conformidad](docs/ENGINE_PARITY_MATRIX.md) describen qué se prueba en cada motor.

El lenguaje incluye contratos, errores tipados, coincidencia de patrones, genéricos y `async`/`await`. El runtime usa conteo de referencias y copia en escritura para las colecciones. El backend LLVM directo es experimental. Consulta el [alcance](docs/SCOPE.md) y los [límites conocidos](docs/KNOWN_LIMITS.md).

Los ejecutables nativos se ejecutan con los permisos del proceso que los invoca. Los [contratos de efectos](docs/EFFECTS.md) y la [política de seguridad](SECURITY.md) detallan qué superficies se pueden analizar o limitar, incluida la frontera con código C externo.

## Documentación y contribución

- [Sintaxis](WYSNA_SINTAXIS.md) · [Especificación](SPEC.md) · [Biblioteca estándar](docs/STDLIB.md)
- [Guías y arquitectura](docs/README.md) · [Historial de cambios](CHANGELOG.md)
- [Contribuir desde código fuente](CONTRIBUTING.md) · [Código de conducta](CODE_OF_CONDUCT.md)

Wysna se distribuye bajo la [licencia Apache 2.0](LICENSE). Consulta [NOTICE](NOTICE) para las atribuciones.
