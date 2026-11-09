from ouncemcp.core.schema import compact_schema


def test_optional_null_collapses():
    schema = {
        "properties": {
            "cursor": {
                "anyOf": [{"type": "string"}, {"type": "null"}],
                "default": None,
                "description": "next page",
            }
        },
        "type": "object",
    }
    assert compact_schema(schema)["properties"]["cursor"] == {
        "type": "string",
        "description": "next page",
    }


def test_non_null_default_is_kept():
    prop = {"anyOf": [{"type": "integer"}, {"type": "null"}], "default": 5}
    assert compact_schema(prop) == prop


def test_titles_dropped_but_property_named_title_kept():
    schema = {"title": "X", "properties": {"title": {"type": "string", "title": "Title"}}}
    assert compact_schema(schema) == {"properties": {"title": {"type": "string"}}}


def test_input_not_mutated():
    prop = {"anyOf": [{"type": "string"}, {"type": "null"}], "default": None}
    compact_schema(prop)
    assert "anyOf" in prop
