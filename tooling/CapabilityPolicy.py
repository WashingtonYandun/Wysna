"""Static capability policy for interpreted runs.

The policy uses the conservative effect report. It is a pre-execution gate over
modeled source effects, not an operating-system sandbox and not proof that trusted
runtime or FFI code cannot act.
"""

from compiler.EffectModel import CAPABILITIES
from error.Error import WysnaError


CAPABILITY_NAMES = frozenset(value for value in CAPABILITIES.values() if value != 'unknown')


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
            file=file_name, line=1, col=1, code='W5006', stage='capability-policy',
            help='Remove indirect/virtual calls or uncontrolled FFI, or run without a policy.',
        )
    observed = set(program['capabilities'])
    if mode == 'deny':
        rejected = sorted(observed & capabilities)
        if rejected:
            raise WysnaError(
                'Capability policy denied modeled capabilities: ' + ', '.join(rejected),
                file=file_name, line=1, col=1, code='W5006', stage='capability-policy',
                help='This static gate is not a sandbox; native FFI and runtime code remain outside it.',
            )
        return
    rejected = sorted(observed - capabilities)
    if rejected:
        raise WysnaError(
            'Capability policy does not allow modeled capabilities: ' + ', '.join(rejected),
            file=file_name, line=1, col=1, code='W5006', stage='capability-policy',
            help='This static gate is not a sandbox; native FFI and runtime code remain outside it.',
        )
