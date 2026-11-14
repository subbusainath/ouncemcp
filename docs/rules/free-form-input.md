# `free-form-input`

Severity: Warning. Reported by `ouncemcp check` (spec DX-3).

## What it finds

An input parameter accepts any object (`dict`, `dict[str, Any]`, an object schema with no properties) or any value (`Any`).

## Why it matters

The schema is the only description of the structure the tool expects. With an open object the model has to guess field names and types, and mistakes surface as handler errors instead of validation errors the model can fix.

## How to fix it

Declare the structure: a Pydantic model for the parameter, or typed top-level parameters. If the tool really takes arbitrary keys (labels, tags), type the values, for example `dict[str, str]`, and describe the keys.
