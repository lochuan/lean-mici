from opendbc.car.interfaces import get_interface_attr
from opendbc.car.mock.values import CAR as MOCK
from opendbc.car.toyota.values import CAR as TOYOTA

FW_VERSIONS = get_interface_attr('FW_VERSIONS', combine_brands=True, ignore_none=True)
_FINGERPRINTS = get_interface_attr('FINGERPRINTS', combine_brands=True, ignore_none=True)

_DEBUG_ADDRESS = {1880: 8}   # reserved for debug purposes


def is_valid_for_fingerprint(msg, car_fingerprint: dict[int, int]):
  adr = msg.address
  # ignore addresses that are more than 11 bits
  return (adr in car_fingerprint and car_fingerprint[adr] == len(msg.dat)) or adr >= 0x800


def eliminate_incompatible_cars(msg, candidate_cars):
  """Removes cars that could not have sent msg.

     Inputs:
      msg: A cereal/log CanData message from the car.
      candidate_cars: A list of cars to consider.

     Returns:
      A list containing the subset of candidate_cars that could have sent msg.
  """
  compatible_cars = []

  for car_name in candidate_cars:
    car_fingerprints = _FINGERPRINTS[car_name]

    for fingerprint in car_fingerprints:
      # add alien debug address
      if is_valid_for_fingerprint(msg, fingerprint | _DEBUG_ADDRESS):
        compatible_cars.append(car_name)
        break

  return compatible_cars


def all_legacy_fingerprint_cars():
  """Returns a list of all known car strings, FPv1 only."""
  return list(_FINGERPRINTS.keys())


# A dict that maps old platform strings to their latest representations
MIGRATION = {
  "TOYOTA SIENNA XLE 2018": TOYOTA.TOYOTA_SIENNA,
  "TOYOTA ALPHARD HYBRID 2021": TOYOTA.TOYOTA_ALPHARD_TSS2,
  "TOYOTA ALPHARD 2020": TOYOTA.TOYOTA_ALPHARD_TSS2,
  "TOYOTA AVALON 2016": TOYOTA.TOYOTA_AVALON,
  "TOYOTA AVALON 2019": TOYOTA.TOYOTA_AVALON_2019,
  "TOYOTA AVALON 2022": TOYOTA.TOYOTA_AVALON_TSS2,
  "TOYOTA CAMRY 2018": TOYOTA.TOYOTA_CAMRY,
  "TOYOTA CAMRY 2021": TOYOTA.TOYOTA_CAMRY_TSS2,
  "TOYOTA C-HR 2018": TOYOTA.TOYOTA_CHR,
  "TOYOTA C-HR 2021": TOYOTA.TOYOTA_CHR_TSS2,
  "TOYOTA COROLLA 2017": TOYOTA.TOYOTA_COROLLA,
  "TOYOTA COROLLA TSS2 2019": TOYOTA.TOYOTA_COROLLA_TSS2,
  "TOYOTA HIGHLANDER 2017": TOYOTA.TOYOTA_HIGHLANDER,
  "TOYOTA HIGHLANDER 2020": TOYOTA.TOYOTA_HIGHLANDER_TSS2,
  "TOYOTA PRIUS 2017": TOYOTA.TOYOTA_PRIUS,
  "TOYOTA PRIUS v 2017": TOYOTA.TOYOTA_PRIUS_V,
  "TOYOTA PRIUS TSS2 2021": TOYOTA.TOYOTA_PRIUS_TSS2,
  "TOYOTA RAV4 2017": TOYOTA.TOYOTA_RAV4,
  "TOYOTA RAV4 HYBRID 2017": TOYOTA.TOYOTA_RAV4H,
  "TOYOTA RAV4 2019": TOYOTA.TOYOTA_RAV4_TSS2,
  "TOYOTA RAV4 2022": TOYOTA.TOYOTA_RAV4_TSS2_2022,
  "TOYOTA RAV4 2023": TOYOTA.TOYOTA_RAV4_TSS2_2023,
  "TOYOTA MIRAI 2021": TOYOTA.TOYOTA_MIRAI,
  "TOYOTA SIENNA 2018": TOYOTA.TOYOTA_SIENNA,
  "LEXUS CT HYBRID 2018": TOYOTA.LEXUS_CTH,
  "LEXUS ES 2018": TOYOTA.LEXUS_ES,
  "LEXUS ES 2019": TOYOTA.LEXUS_ES_TSS2,
  "LEXUS IS 2018": TOYOTA.LEXUS_IS,
  "LEXUS IS 2023": TOYOTA.LEXUS_IS_TSS2,
  "LEXUS NX 2018": TOYOTA.LEXUS_NX,
  "LEXUS NX 2020": TOYOTA.LEXUS_NX_TSS2,
  "LEXUS LC 2024": TOYOTA.LEXUS_LC_TSS2,
  "LEXUS RC 2020": TOYOTA.LEXUS_RC,
  "LEXUS RX 2016": TOYOTA.LEXUS_RX,
  "LEXUS RX 2020": TOYOTA.LEXUS_RX_TSS2,
  "LEXUS GS F 2016": TOYOTA.LEXUS_GS_F,
  "mock": MOCK.MOCK,
}
