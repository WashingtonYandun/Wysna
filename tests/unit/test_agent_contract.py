"""The shipped catalog and generated native constants have one build-time source."""
import json
from pathlib import Path

from common.AgentContract import capabilities, native_sdk_commands, skill_topics
from common.Version import WYSNA_VERSION
from compiler.Bootstrap import SELFHOST_BOOTSTRAP_INPUTS
from compiler.CompilationSession import _compute_interface_and_nominals
from parser.Parser import Parser
from tooling.AgentGraphCLI import get_skill_topic
from tools.generate_agent_contract import generate

ROOT = Path(__file__).resolve().parents[2]


def test_catalog_generation_and_version_provenance():
    generate(check=True)
    catalog = json.loads((ROOT / 'src/common/agent_contract.json').read_text(encoding='utf-8'))
    assert catalog['compiler_version'] == WYSNA_VERSION
    assert 'src/common/agent_contract.json' in SELFHOST_BOOTSTRAP_INPUTS
    assert 'tools/generate_agent_contract.py' in SELFHOST_BOOTSTRAP_INPUTS
    for engine in ('seed', 'compiler'):
        payload = capabilities(engine)
        assert payload['compiler_version'] == WYSNA_VERSION
        assert set(payload['machine_output']['commands']) <= set(payload['commands'])
    assert {'packages', 'project', 'schema'} <= set(native_sdk_commands())


def test_guides_have_complete_discoverable_topics():
    topics = skill_topics()
    for name, content in topics.items():
        payload = get_skill_topic(name)
        assert payload['content'] == content
        assert payload['available_topics'] == sorted(topics)
        assert payload['compiler_version'] == WYSNA_VERSION
    copy = capabilities('compiler')
    copy['commands'].clear()
    assert capabilities('compiler')['commands']


def test_public_metadata_changes_invalidate_the_interface_digest():
    parser = Parser()
    def digest(source):
        return _compute_interface_and_nominals(parser.parse(source))[0]
    original = digest('pub fn f()->int{1}')
    assert digest('pub fn f()->int{2}') == original
    assert digest('@must_use\npub fn f()->int{1}') != original
    assert digest('@Meta(label:"a")\npub fn f()->int{1}') != digest(
        '@Meta(label:"b")\npub fn f()->int{1}')
