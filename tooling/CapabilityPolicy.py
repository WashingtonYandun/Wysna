"""Static capability policy for interpreted runs.

The policy uses the conservative effect report. It is a pre-execution gate over
modeled source effects, not an operating-system sandbox and not proof that trusted
runtime or FFI code cannot act.
"""

from compiler.EffectModel import CAPABILITIES
from error.Error import WysnaError


CAPABILITY_NAMES = frozenset(value for value in CAPABILITIES.values() if value != 'unknown')
DEFAULT_SANDBOX_CAPABILITIES = frozenset({'stdio', 'arguments'})


def parse_capabilities(value):
    names = value.split(',')
    if not value or any(not name for name in names):
        raise ValueError('capability lists must be comma-separated names')
    repeated = len(set(names)) != len(names)
    invalid = sorted(set(names) - CAPABILITY_NAMES)
    if invalid:
        raise ValueError('unknown capability: ' + ', '.join(invalid)
                         + '. Valid capabilities: ' + ', '.join(sorted(CAPABILITY_NAMES)))
    if repeated:
        raise ValueError('capability lists must not repeat names')
    return frozenset(names)


def enforce_capability_policy(report, mode, capabilities, file_name, *, untrusted_bindings=()):
    """Reject execution before interpretation when the static policy cannot pass."""
    if untrusted_bindings:
        raise WysnaError(
            'Capability policy cannot allow untrusted foreign declarations; '
            'the interpreter may bind them before execution',
            file=file_name, code='W5006', stage='capability-policy',
            help='Remove the external declarations or run without a policy.',
        )
    program = report['program']
    if not program['complete_within_model']:
        unresolved = ', '.join(program['unresolved_functions'])
        raise WysnaError(
            'Capability policy requires a complete effect analysis; unresolved functions: '
            + unresolved,
            file=file_name, code='W5006', stage='capability-policy',
            help='Remove indirect/virtual calls or uncontrolled FFI, or run without a policy.',
        )
    observed = set(program['capabilities'])
    if mode == 'deny':
        rejected = sorted(observed & capabilities)
        if rejected:
            raise WysnaError(
                'Capability policy denied modeled capabilities: ' + ', '.join(rejected),
                file=file_name, code='W5006', stage='capability-policy',
                help='This static gate is not a sandbox; native FFI and runtime code remain outside it.',
            )
        return
    rejected = sorted(observed - capabilities)
    if rejected:
        raise WysnaError(
            'Capability policy does not allow modeled capabilities: ' + ', '.join(rejected),
            file=file_name, code='W5006', stage='capability-policy',
            help='This static gate is not a sandbox; native FFI and runtime code remain outside it.',
        )


def enforce_resolved_capability_policy(report, mode, capabilities, file_name, *, untrusted_bindings=()):
    """Enforce capability policy using interprocedurally resolved closure/virtual call targets."""
    if untrusted_bindings:
        raise WysnaError(
            'Capability policy cannot allow untrusted foreign declarations; '
            'native or interpreted execution may bind them outside the policy',
            file=file_name, code='W5006', stage='capability-policy',
            help='Remove the external declarations or compile without --sandbox.',
        )
    program = report['program']
    complete = program.get('resolved_complete', program.get('complete_within_model', False))
    if not complete:
        unresolved = ', '.join(program.get('unresolved_functions', ()))
        raise WysnaError(
            'Sandbox capability policy requires a complete resolved effect analysis; unresolved functions: '
            + unresolved,
            file=file_name, code='W5006', stage='capability-policy',
            help='Ensure all indirect calls resolve to known functions and remove uncontrolled FFI.',
        )
    observed = set(program.get('resolved_capabilities', program.get('capabilities', ())))
    if mode == 'deny':
        rejected = sorted(observed & capabilities)
        if rejected:
            raise WysnaError(
                'Capability policy denied modeled capabilities: ' + ', '.join(rejected),
                file=file_name, code='W5006', stage='capability-policy',
                help='Grant the required capability or remove the host operation.',
            )
        return
    rejected = sorted(observed - capabilities)
    if rejected:
        raise WysnaError(
            'Capability policy does not allow modeled capabilities: ' + ', '.join(rejected),
            file=file_name, code='W5006', stage='capability-policy',
            help='Add the required capability to --allow or remove the host operation.',
        )


def generate_c_capability_guards(mode, capabilities):
    """Return C99 preprocessor macro definitions recording the enforced sandbox policy."""
    denied = (sorted(CAPABILITY_NAMES & capabilities) if mode == 'deny'
              else sorted(CAPABILITY_NAMES - capabilities))
    lines = ['/* Enforced Wysna capability sandbox policy */', '#define WYSNA_SANDBOX_ENFORCED 1']
    for cap in denied:
        macro_suffix = cap.upper().replace('.', '_').replace('-', '_')
        lines.append(f'#define WYSNA_DENY_CAP_{macro_suffix} 1')
    return '\n'.join(lines) + '\n'
