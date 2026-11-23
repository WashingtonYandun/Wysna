# ADR-06 · Metadatos tipados y evolución segura del runtime

- Fecha: 2026-11-22.
- Estado: R0 integrado en main/develop mediante PR #177 (`ba0953e`); R1 candidato de implementación; R2–R4 definidos, pendientes.
- Sustituye: restricción de extensibilidad del apartado de atributos de ADR-05.
- Autorización: el mantenedor eligió atributos propios como metadatos tipados y
  solicitó concurrencia, seguridad, portabilidad y evolución de tipos para agentes.

## Decisión implementada

Los esquemas de anotaciones son structs normales, sin genéricos, aplicados a
funciones superiores mediante campos nombrados con literales `str`, `bool`, `int`.
Se reutiliza el sistema de tipos y de imports; no se crea un segundo lenguaje de
esquemas ni se ejecutan constructores, contratos o plugins al leer metadatos.
Las anotaciones propias no cambian el ABI ni conceden capacidades. Los atributos
integrados conservan sus reglas de diagnóstico.

`src/common/agent_contract.json` contiene versión, catálogos por engine y guías.
El build genera los valores nativos y `Version.py`; un chequeo rechaza salidas
desactualizadas. Los sondeos del host siguen siendo datos dinámicos de Seed.
Los cambios del contrato participan en la huella del bootstrap.

La inspección de funciones expone las anotaciones; la huella de interfaz pública
las incluye. W2003 proporciona familias pendientes como datos estructurados para
reparar patrones sin interpretar texto de terminal.

La ubicación y pila de diagnóstico pasan a TLS. El contexto compartido conserva
cuotas y contabilidad. Los intrínsecos de concurrencia se identifican mediante
origen de declaración y biblioteca configurada; las posiciones de texto dejan de
ser parte de la semántica de esas funciones.

## Siguiente contrato

[R1–R4](13-RUNTIME-AGENT-FIRST.md) fijan el orden: transferencia y efectos de
workers, grupos con cancelación cooperativa/backpressure, anchos numéricos
explícitos y perfiles de despliegue verificados. Estas funcionalidades no se
consideran implementadas por tener un ADR. `int` conserva semántica Int64; ARC
atómico y C99 permanecen. La publicación requiere pruebas y artefactos de release,
no solo un cambio del número de versión.

## R1: propiedad entre workers

La transferencia exige copias completas de entradas, capturas y resultados, no
solo incrementar ARC. Se adopta un contrato cerrado de callbacks sin efectos del
host ni globals no capturados, con W2014/W2015 en ambos checkers. Se rechazan
clases, interfaces, Weak, handles y funciones como datos en el grafo transferido.
Los detalles, costes y límites están en [WORKER_SAFETY](../WORKER_SAFETY.md).

El oráculo de rechazo incluye contratos de campos negativos, genéricos e
importados y `ensures` con retorno implícito. Estas correcciones son posteriores
a v0.9.0; no se reasigna el tag ni se anuncian grupos/cancelación de R2.
