#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.search_v2.grouping import canonical_identity_key, group_offers  # noqa: E402
from app.search_v2.models import Offer, ProductCondition, ProductIdentity  # noqa: E402


def make(offer_id: str, *, storage=256, modifiers=("pro",), condition=ProductCondition.NEW,
         diagonal=None, region="", color="black") -> Offer:
    identity = ProductIdentity(
        category="phone", brand="Apple", canonical_model="iPhone 16", modifiers=list(modifiers),
        storage=storage, diagonal=diagonal, condition=condition, region_or_sim_variant=region,
        key_configuration={"color": color},
    )
    return Offer(offer_id=offer_id, identity=identity, condition=condition)


class SearchV2GroupingTests(unittest.TestCase):
    def test_same_configuration_groups_across_sellers_and_ignores_color(self) -> None:
        groups = group_offers([make("a", color="black"), make("b", color="white")])
        self.assertEqual(len(groups), 1)
        self.assertEqual(len(groups[0].offers), 2)
        self.assertEqual({item.offer_id for item in groups[0].offers}, {"a", "b"})
        self.assertTrue(groups[0].group_id)
        self.assertEqual(groups[0].identity.storage, 256)

    def test_hard_configuration_dimensions_do_not_mix(self) -> None:
        offers = [
            make("base"), make("storage", storage=128), make("max", modifiers=("pro", "max")),
            make("used", condition=ProductCondition.USED), make("region", region="eSIM-only"),
        ]
        groups = group_offers(offers)
        self.assertEqual(len(groups), 5)
        self.assertEqual(sum(len(group.offers) for group in groups), 5)
        self.assertEqual(len({group.canonical_key for group in groups}), 5)

    def test_key_is_deterministic_and_order_independent_for_modifiers(self) -> None:
        first = make("a", modifiers=("pro", "max")).identity
        second = make("b", modifiers=("max", "pro")).identity
        self.assertEqual(canonical_identity_key(first), canonical_identity_key(second))


if __name__ == "__main__":
    unittest.main(verbosity=2)
