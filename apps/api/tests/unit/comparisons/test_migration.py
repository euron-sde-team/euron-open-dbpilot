"""Schema diffs and the migration scripts generated from them, per dialect. Every script
statement must be one the confirmed-write path can describe (AC8)."""

from __future__ import annotations

import pytest

from dbpilot.comparisons.diff import compare
from dbpilot.comparisons.migration import generate
from dbpilot.queries import guard
from dbpilot.schema.schemas import SchemaColumn, SchemaForeignKey, SchemaIndex, SchemaObject


def _table(name: str, cols: list[SchemaColumn], schema: str | None, **kw: object) -> SchemaObject:
    return SchemaObject(schema_name=schema, name=name, kind="table", columns=cols, **kw)  # type: ignore[arg-type]


def _pair(dialect: str) -> tuple[list[SchemaObject], list[SchemaObject]]:
    schema = {"postgresql": "public", "mssql": "dbo"}.get(dialect)
    ref = f"{schema}.customers" if schema else "customers"
    pk = SchemaColumn(
        name="id", type="INTEGER", nullable=False, primary_key=True, autoincrement=True
    )
    before = [
        _table(
            "customers",
            [
                pk,
                SchemaColumn(name="email", type="VARCHAR(100)", nullable=False),
                SchemaColumn(name="old", type="TEXT"),
            ],
            schema,
            indexes=[SchemaIndex(name="ix_customers_old", columns=["old"], unique=False)],
        ),
        _table("legacy", [pk], schema),
        SchemaObject(schema_name=schema, name="v_old", kind="view"),
    ]
    after = [
        _table(
            "customers",
            [
                pk,
                SchemaColumn(name="email", type="VARCHAR(200)", nullable=True),
                SchemaColumn(name="tier", type="VARCHAR(20)", nullable=False, default="'basic'"),
            ],
            schema,
            indexes=[SchemaIndex(name="ix_customers_email", columns=["email"], unique=True)],
        ),
        _table(
            "orders",
            [pk, SchemaColumn(name="customer_id", type="INTEGER", nullable=False)],
            schema,
            foreign_keys=[
                SchemaForeignKey(
                    name="fk_orders_customer",
                    columns=["customer_id"],
                    ref_table=ref,
                    ref_columns=["id"],
                )
            ],
            indexes=[SchemaIndex(name="ix_orders_customer", columns=["customer_id"], unique=False)],
        ),
    ]
    return before, after


@pytest.mark.parametrize("dialect", ["postgresql", "mysql", "mssql", "sqlite"])
def test_scripts_are_ordered_flagged_and_describable(dialect: str) -> None:
    before, after = _pair(dialect)
    diff = compare(before, after)
    assert not diff.identical
    d = diff.as_dict()
    assert d["removed_tables"][0].endswith("legacy") and d["added_tables"][0].endswith("orders")
    assert d["removed_views"] and d["changed_tables"][0]["added_columns"] == ["tier"]
    script = generate(diff, dialect)
    sqls = [s for s, _ in script.statements]
    assert sqls[0].startswith("CREATE TABLE") and script.destructive
    destructive = [s for s, d in script.statements if d]
    assert any(s.startswith("DROP TABLE") for s in destructive)
    first_drop = next(i for i, (_, d) in enumerate(script.statements) if d)
    assert all(d for _, d in script.statements[first_drop:])  # drops come last
    if dialect == "sqlite":
        assert any("cannot change" in n for n in script.notes) and any(
            "foreign key" in n for n in script.notes
        )
    else:
        assert any("FOREIGN KEY" in s for s in sqls)
    infos = guard.classify(script.sql, dialect)
    assert len(infos) == len(sqls)


def test_dialect_specific_spellings() -> None:
    before, after = _pair("postgresql")
    pg = generate(compare(before, after), "postgresql").sql
    assert (
        'ALTER COLUMN "email" TYPE VARCHAR(200)' in pg
        and 'ALTER COLUMN "email" DROP NOT NULL' in pg
    )
    assert '"id" SERIAL NOT NULL' in pg and 'DROP INDEX "public"."ix_customers_old"' in pg
    before, after = _pair("mysql")
    my = generate(compare(before, after), "mysql").sql
    assert "MODIFY COLUMN `email` VARCHAR(200) NULL" in my and "AUTO_INCREMENT" in my
    assert "DROP INDEX `ix_customers_old` ON `customers`" in my
    before, after = _pair("mssql")
    ms = generate(compare(before, after), "mssql").sql
    assert "IDENTITY(1,1)" in ms and "ADD [tier] VARCHAR(20) NOT NULL DEFAULT 'basic'" in ms


def test_identical_schemas_produce_nothing() -> None:
    before, _ = _pair("postgresql")
    diff = compare(before, before)
    assert diff.identical and generate(diff, "postgresql").statements == []
    assert generate(diff, "postgresql").sql == ""


@pytest.mark.parametrize("dialect", ["mysql", "mssql"])
def test_scripts_keep_their_meaning_in_the_form_that_runs(dialect: str) -> None:
    """MySQL and SQL Server run the classifier's re-serialization: for a generated script
    it is a fixpoint (classifying it again changes nothing), so what is confirmed runs."""
    before, after = _pair(dialect)
    script = generate(compare(before, after), dialect)
    infos = guard.classify(script.sql, dialect)
    again = guard.classify(";\n".join(i.sql for i in infos), dialect)
    assert [i.sql for i in again] == [i.sql for i in infos]
    assert [(i.kind, i.destructive) for i in again] == [(i.kind, i.destructive) for i in infos]
    assert all("\n" not in i.sql for i in infos)  # re-serialized, not the generator's layout
