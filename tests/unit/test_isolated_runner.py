"""Host-side protocol checks for the Linux isolation launcher."""

import csv
import io
from pathlib import Path

from tools.run_isolated import OUTPUT_LIMIT, create_command, project_mount, read_output


def test_mount_uses_one_read_only_source_even_with_csv_characters():
    project = Path('/workspace/commas, and "quotes"')
    assert next(csv.reader([project_mount(project)])) == [
        'type=bind', f'src={project}', 'dst=/project', 'readonly', 'bind-recursive=disabled',
    ]


def test_docker_policy_precedes_image_and_agent_arguments():
    image = 'sha256:' + ('a' * 64)
    command = create_command('sandbox', image, Path('/workspace'), ['run', '/project/main.wysna'])
    assert command[-3:] == [image, 'run', '/project/main.wysna']
    for flag in ('--read-only', '--network=none', '--user=65532:65532', '--cap-drop=ALL',
                 '--security-opt=no-new-privileges', '--pull=never', '--log-driver=none',
                 '--env=HTTPS_PROXY=', '--env=https_proxy='):
        assert flag in command[:-3]


def test_output_is_drained_after_reaching_the_capture_limit():
    output = bytearray()
    stream = io.BytesIO(b'X' * (OUTPUT_LIMIT * 4))
    read_output(stream, output)
    assert stream.closed
    assert output == b'X' * (OUTPUT_LIMIT + 1)
