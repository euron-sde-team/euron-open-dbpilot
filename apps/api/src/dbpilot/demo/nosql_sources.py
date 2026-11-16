"""The demo MongoDB and Redis sources, built from a fixed seed. These builders run as the
demo databases' own administrators, outside DBPilot's connection model."""

from __future__ import annotations

import datetime as dt
import random
from typing import Any

import pymongo
import redis

READER = "dbpilot_reader"
CATEGORIES = ["kitchen", "garden", "office", "outdoor", "lighting", "storage"]
ADJECTIVES = ["Oak", "Slate", "Copper", "Linen", "Cedar", "Marble", "Amber", "Birch"]
NOUNS = ["Stool", "Planter", "Desk Tray", "Lantern", "Shelf", "Basket", "Lamp", "Bench"]


def build_catalog(admin_uri: str, database: str, reader_password: str) -> None:
    """A product catalog with reviews; a reader login with the ``read`` role."""
    rng = random.Random(8)
    client: pymongo.MongoClient[dict[str, Any]] = pymongo.MongoClient(
        admin_uri, serverSelectionTimeoutMS=10_000
    )
    try:
        db = client[database]
        db.drop_collection("products")
        db.drop_collection("reviews")
        products = [
            {
                "_id": i,
                "name": f"{rng.choice(ADJECTIVES)} {rng.choice(NOUNS)}",
                "category": rng.choice(CATEGORIES),
                "price": round(rng.uniform(9, 320), 2),
                "in_stock": rng.random() > 0.15,
                "tags": rng.sample(["new", "sale", "eco", "bestseller", "limited"], k=2),
                "added_on": dt.datetime(2025, 1, 1) + dt.timedelta(days=rng.randrange(600)),
            }
            for i in range(1, 241)
        ]
        db["products"].insert_many(products)
        db["products"].create_index("category")
        reviews = [
            {
                "product_id": rng.randrange(1, 241),
                "rating": rng.choice([1, 2, 3, 4, 4, 5, 5, 5]),
                "title": rng.choice(
                    ["Great", "As described", "Could be better", "Love it", "Sturdy"]
                ),
                "verified": rng.random() > 0.3,
            }
            for _ in range(1200)
        ]
        db["reviews"].insert_many(reviews)
        if not db.command("usersInfo", READER).get("users"):
            db.command(
                "createUser", READER, pwd=reader_password, roles=[{"role": "read", "db": database}]
            )
        else:
            db.command("updateUser", READER, pwd=reader_password)
    finally:
        client.close()


def build_sessions(url: str) -> None:
    """Web sessions, carts, rate counters and a leaderboard in the Redis database ``url``
    names (0 when it names none). It is emptied first."""
    rng = random.Random(3)
    r = redis.Redis.from_url(url)
    try:
        r.flushdb()
        for i in range(1, 121):
            r.hset(
                f"session:{i:05d}",
                mapping={
                    "user_id": str(rng.randrange(1, 400)),
                    "ip": f"10.0.{i % 250}.{rng.randrange(1, 250)}",
                    "agent": "Mozilla/5.0",
                },
            )
            r.expire(f"session:{i:05d}", rng.randrange(600, 86_400))
        for u in range(1, 41):
            r.rpush(
                f"cart:{u}", *[f"sku-{rng.randrange(1, 999):03d}" for _ in range(rng.randint(1, 5))]
            )
        for ip in range(1, 31):
            r.set(f"rate:10.0.0.{ip}", rng.randrange(1, 90), ex=60)
        r.zadd(
            "leaderboard:weekly", {f"player-{p}": rng.randrange(100, 9000) for p in range(1, 26)}
        )
        r.sadd("feature:flags", "new_checkout", "dark_mode", "beta_search")
        r.set("config:maintenance", "off")
    finally:
        r.close()
