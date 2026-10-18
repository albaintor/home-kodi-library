"""Version the mobile data model independently of the REST route and server release.

Contract 1 describes the DTOs shipped by Media Catalog 1.0. Additive API
changes do not require a new contract. A breaking change must increment the
contract, keep /api/v1/status and /api/v1/discovery readable by older clients,
and increase MIN_MOBILE_CONTRACT only when compatibility has actually ended.

For a migration window, support several contracts (min <= max); otherwise
force the app to show an update-required screen before reading the catalogue.
"""

MIN_MOBILE_CONTRACT = 1
MAX_MOBILE_CONTRACT = 1


def mobile_compatibility() -> dict[str, int]:
    if MIN_MOBILE_CONTRACT < 1 or MAX_MOBILE_CONTRACT < MIN_MOBILE_CONTRACT:
        raise ValueError("Invalid server/mobile API contract range")
    return {"min_contract": MIN_MOBILE_CONTRACT, "max_contract": MAX_MOBILE_CONTRACT}
