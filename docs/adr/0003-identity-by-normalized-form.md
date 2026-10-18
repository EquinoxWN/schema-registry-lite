# ADR 0003: Identify schemas by a normalized form that keeps defaults

- **Status:** Accepted

## Context

A registry gives each distinct schema one global ID and returns the existing version when a
producer re-registers a schema it already registered (producers do this on every start). "The same
schema" must not depend on whitespace, attribute order, docs or short versus full names, but it
must depend on everything that changes compatibility.

Avro's Parsing Canonical Form (PCF) removes formatting, docs and aliases and writes full names, but
it also removes field defaults. Two schemas that differ only in a default would get the same
identity, although one can read old data and the other cannot.

## Decision

`avro.normalized` writes PCF and adds back field defaults and the enum default. Schemas are stored
with a UNIQUE constraint on this normalized text; the original text of the first registration is
kept and returned by `GET /schemas/ids/{id}`.

## Consequences

- Reformatted or documented copies of a schema map to the same ID and version (tested with a
  pretty-printed copy and with docs added).
- Changing only a default creates a new version, and the compatibility check runs on it.
- The same schema registered under two subjects shares one global ID, as in Confluent's registry.
- Re-registering a schema that already exists under the subject returns its old version without a
  compatibility check, even if the mode has changed since; this matches Confluent's behaviour and is
  covered by a test.
- Fingerprints are not Avro's CRC-64 PCF fingerprints, so IDs are not interchangeable with other
  registries; clients use the IDs this registry returns.
