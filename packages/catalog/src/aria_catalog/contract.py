"""The product.v1 product contract, checked on every provider's products.

The web renders a card only for a product that passes its field check, dropping the others
(packages/web/app/utils/validation.ts), and cites a product only by an ID of ASCII letters,
digits and `._:-`, at most 128 characters (packages/web/app/utils/citations.ts). A product
outside this contract is dropped instead of returned, so one bad row can't hide the rest.
"""

import re
from typing import Any, Optional

# In the order of the product.v1 example in docs/product-specs/product-cards.md and the fixture.
PRODUCT_FIELDS = (
    "id",
    "productDisplayName",
    "gender",
    "masterCategory",
    "subCategory",
    "articleType",
    "baseColour",
    "season",
    "year",
    "usage",
)
MAX_TEXT_LENGTH = 2000
# The web's citation rule: `[[Product:<id>]]` resolves only IDs that match this, in full.
PRODUCT_ID = re.compile(r"[A-Za-z0-9._:-]{1,128}")

_TEXT_FIELDS = tuple(field for field in PRODUCT_FIELDS if field not in ("id", "year"))


def product_v1(item: Any) -> Optional[dict]:
    """`item` as a product.v1 product with exactly the ten fields, or None when it doesn't qualify.

    A numeric ID becomes a string and a whole-number year an int, as upstream catalogs often send
    them. Every other field must already be a string of at most 2000 characters, and the display
    name can't be blank.
    """
    if not isinstance(item, dict):
        return None
    product_id, year = _product_id(item.get("id")), _year(item.get("year"))
    text = {field: item.get(field) for field in _TEXT_FIELDS}
    if product_id is None or year is None:
        return None
    if not all(isinstance(value, str) and len(value) <= MAX_TEXT_LENGTH for value in text.values()):
        return None
    if not text["productDisplayName"].strip():
        return None
    product = {**text, "id": product_id, "year": year}
    return {field: product[field] for field in PRODUCT_FIELDS}


def _product_id(value: Any) -> Optional[str]:
    if isinstance(value, int) and not isinstance(value, bool):
        value = str(value)
    return value if isinstance(value, str) and PRODUCT_ID.fullmatch(value) else None


def _year(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.isascii() and value.isdigit():
        return int(value)
    return None
