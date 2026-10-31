"""Builder reads the repository lock, never a second hand-maintained version list."""

from pathlib import Path
import unittest
from tools.release import build_pydeps


class BuilderTests(unittest.TestCase):
  def test_exact_linux_arm64_cp312_closure_from_lock(self):
    selected = build_pydeps.select_wheels((Path(__file__).resolve().parents[2] / 'uv.lock').read_bytes())
    self.assertEqual(
      {p['name']: p['version'] for p in selected},
      {
        'sanic': '25.12.1',
        'aiofiles': '25.1.0',
        'html5tagger': '2.0.0',
        'httptools': '0.8.0',
        'multidict': '6.7.1',
        'sanic-routing': '23.12.0',
        'setuptools': '84.0.0',
        'tracerite': '2.6.5',
        'typing-extensions': '4.16.0',
        'ujson': '6.0.0',
        'uvloop': '0.22.1',
        'websockets': '17.1',
      },
    )
    ujson = next(p for p in selected if p['name'] == 'ujson')
    self.assertEqual(ujson['sha256'], 'c51915961a51e37403fd94114e293d580dd916ddd1961b229217a87193d2454e')
    self.assertTrue(all(p['wheel'].endswith('-py3-none-any.whl') or '-cp312-cp312-manylinux2014_aarch64.' in p['wheel'] for p in selected))
