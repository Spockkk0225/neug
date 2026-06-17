## Overview

Implement import and export of GraphAr format files via the NeuG Extension
mechanism.

GraphAr is a graph-level format: a dataset contains graph metadata (`graph.yml`)
plus vertex and edge data chunks. Full graph import/export, subgraph import,
and query subgraph export all need graph-level metadata handling, schema
creation or extraction, vertex/edge ordering, and endpoint remapping.

This spec chooses the **`CALL` function path** as the implementation surface for
GraphAr I/O. This design aligns with Neo4j's APOC library conventions (e.g.,
`apoc.export.csv.*`), where graph I/O operations are exposed as callable
procedures rather than language-level syntax extensions.


## User Requirements

### R1: Full Graph Persistent Import

**Behavior**: Given a GraphAr dataset path (directory containing `graph.yml` and
data files), a GraphAr descriptor path, or later a compressed archive
(`.tar.gz` / `.zip`), auto-discover the graph descriptor, create all vertex/edge
type schemas, and batch-import all vertex and edge data into the database.

The imported graph is **permanent**: it is persisted in storage and queryable
after reopening the database.


```cypher
CALL graphar.import("path/to/graphar/")
RETURN vertex_count, edge_count;
```


---

### R2: Subgraph Import

**Behavior**: Import a selected subgraph from a GraphAr dataset into NeuG
storage. The selected subgraph may contain multiple vertex labels and edge
labels, but not necessarily the entire GraphAr graph.

The import interface should support two projection categories through a JSON
projection parameter passed to `CALL graphar.import(...)`.

#### R2.1: Table Projection

Table projection is table-level projection over GraphAr vertex and edge tables.
It specifies which vertex/edge types to import, which properties to keep, and
optional filter predicates over those tables.


```json
{
  "projection": "table",
  "node_table": {
    "Person": {
      "properties": ["id", "name", "age"],
      "filter": "name <> 'Alice'"
    },
    "Company": {
      "properties": ["id", "name"],
      "filter": ""
    }
  },
  "edge_table": {
    "Knows": {
      "properties": ["id", "since"],
      "filter": "id < 3"
    },
    "Belongs": {
      "properties": [],
      "filter": ""
    }
  }
}
```

Single table import is a special case of table projection, e.g. importing only one vertex table:

```json
{
  "projection": "table",
  "node_table": {
    "Person": {
      "properties": [],
      "filter": ""
    }
  }
}
```

Rules:

- `projection` must be `table`.
- `node_table` maps vertex type names to table projection specs.
- `edge_table` maps edge type names to table projection specs.
- `properties` lists the properties to import for that type.
- Empty `properties` means importing all properties for that type.
- Empty `filter` strings mean no filtering for that type.
- Only listed vertex and edge types are imported.

#### R2.2: Neighbor Projection


Table projection is inherently a Parquet capability with column pruning and
predicate pushdown. GraphAr's CSR indexes additionally enable neighbor projection:
importing subgraphs composed of a seed vertex and its neighbors, which is
particularly effective for neighbor-centric queries such as IS-3 (one-hop) and
IC-8 (multi-hop).

Neighbor projection is graph-structure-level projection using GraphAr topology and
CSR indexes. It specifies one seed vertex and imports its one-hop neighbors
through selected edge tables, together with the required vertices, edges, and
selected properties.

```json
{
  "projection": "neighbor",
  "source_node": {
    "table": "Person",
    "properties": ["id", "name", "age"],
    "id": 1
  },
  "edge_table": {
    "Knows": {
      "properties": ["id", "name"]
    },
    "Belongs": {
      "properties": ["id", "name"]
    }
  }
}
```

Rules:

- `projection` must be `neighbor`.
- `source_node` specifies exactly one seed vertex.
- `source_node.table` is the vertex type of the seed vertex.
- `source_node.id` is the primary-key value of the seed vertex.
- `source_node.properties` lists the seed vertex properties to import.
- `edge_table` maps edge type names to neighbor projection specs.
- `edge_table.*.properties` lists the edge properties to import for that type.
- Only one-hop neighbor projection is supported.

Open questions / future extensions:

- Multiple source vertices in one projection.
- Multi-hop neighbor projection.
- Multi-hop merge strategy, including `expanded` for the traversed neighbor
  graph and `induced` for the induced subgraph among selected vertices.

---

### R3: Full Graph Export

**Behavior**: Export the complete NeuG database graph to GraphAr format files,
including graph metadata, vertex data, edge topology, and edge properties.

Syntax:

```cypher
CALL graphar.export("/tmp/graphar_out")
RETURN vertex_count, edge_count;
```

---

### R4: Query Subgraph Export

**Behavior**: Export a subgraph to GraphAr format. Since GraphAr stores data
separately by type (one file per vertex/edge table), the export function accepts
an explicit map that groups nodes and edges by their type labels.

#### R4.1: Export Streaming Query Results

Export the subgraph produced by a streaming query. Users explicitly group nodes
and edges by type using `collect()` in the query, giving full control over type
assignment for multi-label scenarios.

```cypher
MATCH (p:Person)
WITH collect(p) AS persons
MATCH (c:Company)
WITH persons, collect(c) AS companies
MATCH (p:Person)-[e:works_at]->(c:Company)
CALL graphar.export("/tmp/out", {
  "vertex": { "Person": persons, "Company": companies },
  "edge":   { "works_at": collect(e) }
})
RETURN vertex_count, edge_count;
```

#### R4.2: Export Projected Subgraph

Export an existing projected graph by name. The extension reads the projected
graph definition and exports all vertices and edges within that projection.

```cypher
CALL graphar.export("/tmp/out", { "graph": "graph_name" })
RETURN vertex_count, edge_count;
```

This path depends on projected graph support being available in NeuG.
