import importlib.util
import json
from pathlib import Path

spec = importlib.util.spec_from_file_location('desk_setup', Path(__file__).with_name('desk_setup.py'))
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)


def test_credentials_are_a_single_bounded_utf8_json_line():
    payload = setup.encode_credentials('WiFi', 'secret')
    assert payload.endswith(b'\n') and payload.count(b'\n') == 1
    assert json.loads(payload) == {'ssid': 'WiFi', 'password': 'secret'}
    assert setup.encode_credentials('', 'secret') is None
    assert setup.encode_credentials('x' * 33, '') is None
    assert setup.encode_credentials('wifi', 'x' * 64) is None


def test_com3_is_never_selected():
    assert not setup.is_safe_port('com3')
    assert not setup.is_safe_port(' COM3 ')
    assert setup.is_safe_port('COM4')
