"""Read the build-time contract shared by Seed and the native agent tools."""
from copy import deepcopy
from functools import lru_cache
import json
from pathlib import Path


@lru_cache(maxsize=1)
def _contract():
    path = Path(__file__).with_name('agent_contract.json')
    contract = json.loads(path.read_text(encoding='utf-8'))
    if contract['schema_version'] != 'wysna.agent-contract/1':
        raise ValueError('Unsupported agent contract schema')
    return contract


def skill_topics():
    contract = _contract()
    return {name: content.replace('{version}', contract['compiler_version'])
            for name, content in contract['skills'].items()}


def capabilities(engine):
    contract = _contract()
    payload = deepcopy(contract['capabilities'][engine])
    payload['compiler_version'] = contract['compiler_version']
    return payload


def native_sdk_commands():
    return list(_contract()['native_sdk_commands'])
