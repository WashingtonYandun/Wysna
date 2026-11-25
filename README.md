<p align="center">
  <img src="assets/wawi.jpg" alt="Wawi, la mascota de Wysna" width="112" />
</p>

# Wysna

**Lenguaje de programación compilado para agentes de IA.**

Wysna usa tipado estático y compila a ejecutables nativos mediante C99. Sirve para automatizar tareas, procesar datos y compartir lógica entre una aplicación de consola y el navegador. Su compilador permite que un agente consulte el código por símbolos, lea diagnósticos estructurados y compruebe un cambio antes de escribirlo.

[Web oficial](https://wysna.wysofia.com/) · [Descargas](https://github.com/WashingtonYandun/Wysna/releases/latest) · [Primer proyecto](#primer-proyecto) · [Documentación](docs/README.md)

[![CI en main](https://github.com/WashingtonYandun/Wysna/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/WashingtonYandun/Wysna/actions/workflows/ci.yml)
[![Última versión publicada](https://img.shields.io/github/v/release/WashingtonYandun/Wysna)](https://github.com/WashingtonYandun/Wysna/releases/latest)
[![Licencia Apache 2.0](https://img.shields.io/badge/license-Apache%202.0-blue)](LICENSE)

## Instalación

Escoge una distribución en [GitHub Releases](https://github.com/WashingtonYandun/Wysna/releases). Ambas están disponibles para Windows x64, macOS arm64 y Linux x64.

- **[SDK nativo](docs/NATIVE_SDK.md).** Para compilar y probar aplicaciones nativas, gestionar paquetes locales y usar las herramientas para agentes. Incluye Selfhost, Zig y la biblioteca estándar. Funciona sin Python.
- **[Seed](docs/SEED_DISTRIBUTION.md).** Para el intérprete de referencia, el REPL, la ejecución WASI y las bibliotecas para navegador. Sus instaladores incluyen un runtime privado de Python.

Verifica el archivo con `SHA256SUMS`, descomprime el SDK y añade su directorio `bin` al `PATH`. En Windows también puedes abrir `open-terminal.cmd`. Confirma la instalación con `wysna --version`.

Los ejemplos de este README usan **Wysna 0.9.5**. Si actualizas desde 0.9.0, conserva `package.language = "0.9"`, fija `toolchain.compiler = "0.9.5"` y ejecuta `wysna project lock` antes de usar `--frozen`. Para proyectos anteriores, consulta la [migración de 0.8 a 0.9](docs/MIGRATING_0_8_TO_0_9.md).

<a id="first-project"></a>

## Primer proyecto

Guarda el programa como `quote.wysna`. El contrato exige una cantidad positiva y un precio no negativo. Los precios se expresan en centavos.

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

El programa imprime `3897`. Para crear un proyecto con manifiesto, lockfile y una prueba inicial, ejecuta:

```sh
wysna project new my-app
cd my-app
wysna project lock
wysna project check --frozen
wysna project test --frozen
```

<a id="project-impact"></a>

## Entender y cambiar un proyecto

Antes de editar una función, consulta sus dependencias con `graph` y lee su declaración con `inspect`. Desde un checkout, esta consulta muestra qué entradas del proyecto comparten la regla de validación de una cotización.

```sh
wysna graph --manifest examples/shared-browser-app/wysna.toml --symbol quote_validation_code --rel impact
```

El resultado muestra las cadenas de dependencias y señala las fronteras que el análisis estático no pudo resolver. Un agente puede proponer cambios en varios archivos con `patch`, leer errores mediante `json-v1` y aplicar el cambio con el `plan_hash` de la previsualización. La inspección por lotes comparte un presupuesto de contexto. MCP expone estas operaciones para integraciones. La extensión de VS Code ofrece navegación y consulta de impacto. [Guía para agentes](docs/AI_AGENT_GUIDE.md).

Para expresar un límite del diseño, asigna roles a las clases con atributos tipados y declara en `wysna.toml` qué dependencias directas están prohibidas. `project check` y la previsualización de parches verifican esas reglas. `W6101` indica una infracción comprobada. `W6102` indica que la evidencia es insuficiente. [Contratos de arquitectura](docs/ARCHITECTURE_CONTRACTS.md).

## Proyectos y paquetes

`wysna.toml` describe los objetivos, las dependencias y las pruebas. El SDK nativo puede empaquetar dependencias, fijarlas por SHA-256 y restaurarlas desde la caché sin red. Usa `wysna.lock` y `--frozen` para comprobar la resolución. [Guía de proyectos](docs/PROJECTS.md).

- [Auditoría de operaciones](examples/wysofia-ops/README.md) valida eventos JSON y genera reportes, con un objetivo SQLite opcional.
- [Sincronización de mediciones](examples/automation-sync/README.md) descarga un lote acotado y lo importa a SQLite en una transacción.
- [Aplicación de cotizaciones](examples/shared-browser-app/README.md) comparte una regla entre consola, servicio y navegador.
- [Contratos de arquitectura](examples/architecture/wysna.toml) comprueba dependencias directas entre controlador, servicio y repositorio.

## Estado del lenguaje

Wysna está en etapa pre-1.0. Incluye contratos, errores tipados, pattern matching, genéricos y `async`/`await`. El SDK nativo usa el compilador Selfhost escrito en Wysna, con C99 como backend principal. Seed mantiene el intérprete de referencia. Consulta el alcance por plataforma y las fronteras de seguridad antes de elegir un destino. [Arquitectura](docs/COMPILER_ARCHITECTURE.md) · [Conformidad](docs/ENGINE_PARITY_MATRIX.md) · [Límites](docs/KNOWN_LIMITS.md) · [Modelo de efectos](docs/EFFECTS.md).

## Documentación y contribución

- [Manual web](https://wysna.wysofia.com/) · [Sintaxis](WYSNA_SINTAXIS.md) · [Especificación](SPEC.md) · [Biblioteca estándar](docs/STDLIB.md)
- [Guía para agentes](docs/AI_AGENT_GUIDE.md) · [Changelog](CHANGELOG.md)
- [Contribuir](CONTRIBUTING.md) · [Seguridad](SECURITY.md) · [Código de conducta](CODE_OF_CONDUCT.md)

Wysna se distribuye bajo la [licencia Apache 2.0](LICENSE). Consulta las atribuciones en [NOTICE](NOTICE).
