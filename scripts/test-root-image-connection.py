"""Run with python3 scripts/test-root-image-connection.py; no network or sleeps."""
import importlib.util
from pathlib import Path
from unittest.mock import Mock, patch
import urllib.error

spec = importlib.util.spec_from_file_location('roots', Path(__file__).with_name('prepare-ios27-root-images.py'))
roots = importlib.util.module_from_spec(spec)
spec.loader.exec_module(roots)
with patch.object(roots.time, 'sleep'), patch('builtins.print'):
    factory = Mock(side_effect=[urllib.error.URLError('TLS timeout'), TimeoutError('TLS timeout'), 'connected'])
    assert roots.connect_ipsw(factory) == 'connected'
    assert factory.call_count == 3
    for error, count in ((ValueError('wrong product'), 1), (TimeoutError('TLS timeout'), 3)):
        factory = Mock(side_effect=error)
        try:
            roots.connect_ipsw(factory)
        except type(error) as actual:
            assert actual is error
        else:
            raise AssertionError('error swallowed')
        assert factory.call_count == count
print('PASS: transient retries, fail-fast identity error, exhausted timeout preserved')
