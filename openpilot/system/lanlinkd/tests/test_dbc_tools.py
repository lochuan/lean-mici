from opendbc import get_generated_dbcs
from opendbc.can.dbc_asset import PLATFORM
from openpilot.tools.cabana.dbc.generate_dbc_json import generate_dbc_dict


def test_cabana_uses_explicit_development_source_mapping():
  name = generate_dbc_dict()[PLATFORM]
  assert name == 'toyota_secoc_pt_generated'
  assert name in get_generated_dbcs()
