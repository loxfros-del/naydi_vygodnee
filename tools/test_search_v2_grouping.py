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


def configured(
    offer_id: str,
    *,
    category: str,
    model: str,
    configuration: dict[str, object] | None = None,
    storage: int | None = None,
    diagonal: float | None = None,
    refresh_rate: int | str | None = None,
) -> Offer:
    identity = ProductIdentity(
        category=category,
        brand="Test Brand",
        canonical_model=model,
        storage=storage,
        diagonal=diagonal,
        refresh_rate=refresh_rate,
        key_configuration=dict(configuration or {}),
        condition=ProductCondition.NEW,
    )
    return Offer(offer_id=offer_id, identity=identity, condition=ProductCondition.NEW)


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

    def test_laptop_known_cpu_ram_and_ssd_conflicts_never_share_market_group(self) -> None:
        same_configuration = [
            configured(
                "first", category="laptop", model="IdeaPad 3", storage=512,
                configuration={"cpu": "RYZEN 5 7530U", "ram": "16 GB", "ssd": "512 GB"},
            ),
            configured(
                "same", category="laptop", model="IdeaPad 3", storage=512,
                configuration={"cpu": "Ryzen 5 7530U", "ram_gb": 16, "ssd_gb": 512},
            ),
        ]
        conflicting = [
            configured(
                "other-cpu", category="laptop", model="IdeaPad 3", storage=512,
                configuration={"cpu": "RYZEN 7 7730U", "ram_gb": 16, "ssd_gb": 512},
            ),
            configured(
                "other-ram", category="laptop", model="IdeaPad 3", storage=512,
                configuration={"cpu": "RYZEN 5 7530U", "ram_gb": 8, "ssd_gb": 512},
            ),
            configured(
                "other-ssd", category="laptop", model="IdeaPad 3", storage=256,
                configuration={"cpu": "RYZEN 5 7530U", "ram_gb": 16, "ssd": "256 GB"},
            ),
        ]

        groups = group_offers([*same_configuration, *conflicting])
        self.assertEqual(len(groups), 4)
        self.assertEqual(
            {item.offer_id for group in groups if len(group.offers) == 2 for item in group.offers},
            {"first", "same"},
        )

    def test_tv_resolution_aliases_group_but_known_refresh_conflict_splits(self) -> None:
        groups = group_offers([
            configured(
                "4k", category="tv", model="TCL C745", diagonal=55, refresh_rate="120 Гц",
                configuration={"resolution": "4K"},
            ),
            configured(
                "same", category="tv", model="TCL C745", diagonal=55, refresh_rate=120,
                configuration={"resolution": "3840 x 2160"},
            ),
            configured(
                "60hz", category="tv", model="TCL C745", diagonal=55, refresh_rate="60 Hz",
                configuration={"resolution": "4K"},
            ),
        ])
        self.assertEqual(len(groups), 2)
        self.assertEqual(
            {item.offer_id for group in groups if len(group.offers) == 2 for item in group.offers},
            {"4k", "same"},
        )

    def test_coffee_machine_type_is_sku_critical_but_unknown_marketing_fields_are_ignored(self) -> None:
        groups = group_offers([
            configured(
                "automatic", category="coffee_machine", model="Philips EP2220",
                configuration={"machine_type": "автоматическая", "marketing_label": "offer A"},
            ),
            configured(
                "same", category="coffee_machine", model="Philips EP2220",
                configuration={"machine_type": "automatic", "marketing_label": "offer B", "unknown_optional": "—"},
            ),
            configured(
                "capsule", category="coffee_machine", model="Philips EP2220",
                configuration={"machine_type": "capsule"},
            ),
        ])
        self.assertEqual(len(groups), 2)
        self.assertEqual(
            {item.offer_id for group in groups if len(group.offers) == 2 for item in group.offers},
            {"automatic", "same"},
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
