"""Configuration decisions retain short-circuiting and exact stored values."""

import pytest

from xknxeditor.vendor.powertool.rules import decide


def test_short_circuit_keeps_unavailable_fields_unread():
    table = {
        "values": [["literal", True], ["literal", False], ["field", ["value"]]],
        "predicates": [["attached", "sensor"], ["truth", 2]],
        "branches": [[0, 1, -2], [1, -1, -2]],
    }
    reads = []

    def read(path):
        reads.append(path)
        return 3

    assert decide(table, 0, read, lambda _key: False) is False
    assert not reads
    assert decide(table, 0, read, lambda _key: True) is True
    assert reads == [["value"]]


@pytest.mark.parametrize(("value", "expected"), [(7, False), (8, True), (9, True)])
def test_scaled_stored_value_boundary(value, expected):
    table = {
        "values": [
            ["literal", True],
            ["literal", False],
            ["field", ["value"]],
            ["scaled", 2, ["shift"]],
        ],
        "predicates": [["at_least", 2, 3]],
        "branches": [[0, -1, -2]],
    }
    assert (
        decide(
            table,
            0,
            lambda path: 2 if path == ["shift"] else value,
            lambda _key: False,
        )
        is expected
    )


@pytest.mark.parametrize("value", [None, False, 0, -1, 1.5, "1"])
def test_result_keeps_value_type(value):
    table = {"values": [["literal", value]], "predicates": [], "branches": []}
    result = decide(table, -1, lambda _path: None, lambda _key: False)
    assert result == value
    assert type(result) is type(value)


def test_cycle_is_rejected():
    table = {
        "values": [["literal", False]],
        "predicates": [["attached", "sensor"]],
        "branches": [[0, 0, -1]],
    }
    with pytest.raises(ValueError, match="Cyclic"):
        decide(table, 0, lambda _path: None, lambda _key: True)


def test_bundled_decision_references_are_valid_and_acyclic():
    from xknxeditor.vendor.powertool.metadata import decision_tables, family_schemas

    table = decision_tables()
    values, predicates, branches = (
        table["values"],
        table["predicates"],
        table["branches"],
    )
    visited, pending = set(), set()

    def visit(reference):
        if reference < 0:
            assert 0 <= -reference - 1 < len(values)
            return
        assert reference < len(branches)
        assert reference not in pending
        if reference in visited:
            return
        pending.add(reference)
        predicate, yes, no = branches[reference]
        assert 0 <= predicate < len(predicates)
        visit(yes)
        visit(no)
        pending.remove(reference)
        visited.add(reference)

    for predicate in predicates:
        if predicate[0] != "attached":
            assert all(0 <= index < len(values) for index in predicate[1:])
    for index in range(len(branches)):
        visit(index)
    for schema in family_schemas():
        for spec in schema["nodes"].values():
            for reference in spec["rules"].values():
                visit(reference)
