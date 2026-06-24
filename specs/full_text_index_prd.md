# 全文索引 PRD

## 1. 整体介绍

### 1.1 什么是全文索引

全文索引是一种以“词语”为中心、从关键词反向定位到文档或数据位置的索引结构。它可以和正向索引对比理解。

正向索引（Forward Index）以“文档”为中心，如果要找所有包含“手机”的文档，系统需要遍历每一个文档，并检查文档的词语列表中是否包含“手机”。当文档数量很大时，这种方式效率较低，时间复杂度通常接近 `O(N)`。

```text
文档 ID -> 包含的词语列表

Doc1 -> ["苹果", "手机", "便宜"]
Doc2 -> ["电脑", "办公", "轻薄"]
Doc3 -> ["手机", "新品", "拍照"]
```


全文索引（Full-Text Index）以“词语”为中心，当用户查询“手机”时，系统可以直接通过关键词定位到相关文档列表，而不需要扫描全部文档。理想情况下，关键词在词典中的定位可以接近 `O(1)`。

```text
词语 -> 包含该词语的文档 ID 列表

"手机" -> [Doc1, Doc3, Doc5]
"苹果" -> [Doc1, Doc8]
"便宜" -> [Doc1, Doc6, Doc9]
```

### 1.3 抽象结构

一个典型的全文索引由三部分组成：

* 词典（Term Dictionary）
  * 存储所有不重复的词条（Term）。
  * 通常按字典序排序，便于快速查找（使用 B+ 树、哈希表或 FST 等结构）。

* 倒排列表（Posting List）
  * 每个词条对应一个列表，记录哪些文档包含了这个词。
  * 列表中每个元素称为倒排项（Posting），通常包含文档 ID（DocID）作为唯一标识文档，以及其它一些元数据。

* 元数据（Metadata）
  * 存放其他元数据，用于计算各类指标。

## 2. 核心索引操作

### 2.1 基础写入流程

写入或更新数据时，需要将文本字段转换为全文索引中的条目。

1. 对属性内容进行分词，得到 term 列表。

2. 对 term 做归一化处理，例如小写化、去标点、去停用词、词干化。

3. 对每个 term 更新 dict 和 posting list。

4. 更新每个 term 相关的元数据。

示例：

```text
输入数据:
  vid = 1001
  content = "Graph database query"

分词结果:
  graph
  database
  query

索引写入:
  graph    -> append 1001
  database -> append 1001
  query    -> append 1001
```

### 2.2 基础查询流程

* 对于单关键词查询 `query: graph`：

  1. 在 dict 中查找 `graph`。

  2. 获取 `graph` 对应的 posting list。

  3. 返回 posting list 中的主键 ID。

* 对于多关键词查询 `query: graph database`：

  1. 分词得到 `graph`、`database`。

  2. 分别读取两个 posting list。

  3. 根据查询语义执行 AND / OR / NOT。

  4. 返回合并后的结果。

### 2.3 短语检索能力

短语检索需要判断多个词是否按照指定顺序连续出现。

例如 `query: graph database`，仅知道 `graph -> [doc1, doc2]` 和 `database -> [doc1, doc3]` 不够，还需要知道词在文档中的位置：

```text
graph:
  doc1 positions [0]
  doc2 positions [0]

database:
  doc1 positions [1]
  doc3 positions [0]
```

可以发现在 `doc1` 中 `graph` 和 `database` 连续出现，即可判断 `doc1` 命中短语 `graph database`。

**需要的元数据**：

- `position`：记录每个 term 的 position，可在posting list中直接维护。


### 2.4 高亮展示能力

搜索结果高亮用于在返回结果中标记命中的关键词或片段。要实现高亮，仅知道某个文档包含关键词还不够，还需要知道关键词在原文中的具体位置。

例如：

```text
content: "Graph database query"
query: "database"
```

如果索引中记录了 `database` 的 term 位置或字符范围，查询返回时即可定位原文片段，并将命中词高亮展示。

**需要的元数据**：

- `offset`：记录关键词的起始位置，用于精准定位和高亮展示。

### 2.5 BM25 排序能力

BM25 是全文检索中常用的相关性排序算法。它通常需要以下信息：

```text
tf: term frequency，词在当前文档中的出现次数
df: document frequency，包含该词的文档数量
N: 总文档数量
doc_len: 当前文档长度
avg_doc_len: 平均文档长度
```

如果全文索引只保存 `keyword -> [doc_id]`，无法计算 BM25。为了支持 BM25，需要在 dict、posting list 或 doc metadata 中维护统计信息。

**需要的元数据**：

- `tf`：词在当前文档中的出现次数，可以直接在 posting list 中维护。每个倒排项不仅记录 `DocID`，也记录该词在该文档中的出现次数。
- `df`：包含该词的文档数量，需要在 dict 中维护，或者直接获取posting list的长度。
- `N`：总文档数量，可以通过 NeuG 本身的图数据统计接口返回，不需要在全文索引中重复维护。
- `doc_len`：文档长度，需要单独的 hashmap 存储，例如 `doc_id -> doc_len`。
- `avg_doc_len`：平均文档长度，可以通过单独维护变量 `tot_doc_len` 计算得到，即 `avg_doc_len = tot_doc_len / N`。



## 3 索引结构设计

### 3.1 抽象数据结构

上面提到，全文索引需要维护三个数据域：词典，倒排列表，元数据。他们的抽象结构大致如下：

```
Term Dictionary:
  Term -> PostingListIndex, df

Posting List:
  PostingListIndex -> [DocId, tf, [positions], [offsets]]

Metadata:
  DocId -> doc_len
  N: number of docs
  tot_doc_len: sum of all doc length
```

Term Dictionary 是一个 KV 数据结构，根据输入的 Term 快速定位到具体的 Posting List，同时 df 记录该 Term 在多少个 Doc 中出现。

Posting List 是一个线性表结构，每一项都记录了当前的 Term 在哪些 Doc 中出现，同时 tf 记录出现的次数， positions 和 offsets 记录出现的位置/偏移量。

此外还需要一个单独的 KV 结构记录每个文档的长度 doc_len，以及所有的文档的长度/数量总和，用于计算各类指标。

虽然 Posting List 名义上是一个 List，但考虑到索引修改的情况，实际也需要改造为一个哈希表来保证 `O(1)` 定位。因此，整体结构都可以统一用 KV 表对应：

```
Term -> [DocIds]
Term -> df

Term + DocId -> tf
Term + DocId -> [positions]
Term + DocId -> [offsets]

DocId -> doc_len
-> N
-> tot_doc_len
```

### 3.2 存储方案选型

全文索引本身的数据结构较为简单，但有大量底层优化空间。例如，Term Dictionary 依赖于高效的 KV 存储结构，Posting List 需要考虑频繁修改的情况。

#### 3.2.1 内部表存储（DuckDB方案）

duckdb 将这些数据结构都用自身内部的关系表进行存储，例如 `Term -> df`，就会创建一个 `(Term, df)` 的表，记录每个 Term 的 df，然后将 Term 设置为主键；而 `Term + DocId -> tf` 对应一个 `(Term, DocId, tf)` 的表，中 Term 和 DocId 作为联合主键。

此外，duckdb没有采用列表属性，因此 `Term -> [DocIds]` 会被展开为一个扁平的 `(Term, DocIds)` 表。类似地，`Term + DocId -> [positions]` 会被展开为 `(Term, DocId, position)` 表。

**优点**：

1. 零依赖，所有操作都直接在内部表上进行。

2. 事务一致性，索引层的事务行为和数据层保持一致。

3. 操作可复用，如哈希、逻辑删除等功能都可以复用数据层的设置。

**缺点**：

1. 空间占用高，扁平式存储有大量的重复元素。

2. 查询效率低，需要多次join操作，且缺少各类剪枝和压缩优化。

#### 3.2.2 B-Tree 存储（Sqlite 的 FST5 方案）

**优点**：

1. 数据分块，Sqlite会将数据划分为多个 segments，并且随着数据的写入逐渐将小的 segment 合并为大的 segment（类似于LSM-Tree）。每个 segment 都用 Sqlite 自己的 B-Tree 存储若干 Term 及其所在的 DocId，线性存储，且 DocId 通过 delta 编码压缩。

2. 新旧数据分离，新写入的数据会单独维护在一个 segment 中，不影响读进程，最后通过 merge 操作合并。

3. [DocIds] 索引加速，允许用户指定 DocId 的扫描范围。

**缺点**：

1. 查询复杂，需要检索多个 segments 并合并结果。

#### 3.2.3 有限状态自动机存储（Apache Lucene 方案）

**优点**：

1. 使用有限状态自动机压缩所有 Terms，最大程度提高 KV 效率。

**缺点**：

1. 整体依赖过重，暂不考虑。

#### 3.2.4 RocksDB 存储（Zvec 方案）

Zvec将 KV Store 的需求下沉到 RocksDB，Zvec本身不负责数据的检索，但是会做一定的优化。

**优点**：

1. 稠密自适应，[DocIds] 在较小时使用列表存储，在较大时转换为 bitmap 存储。

2. 只读部分优化，[DocIds] 转化为 BitPacked，将 tf / doc_len 等数据内联存储，加速计算。

3. top-k 剪枝，内部维护可能的最大值。

4. batch更新，当需要更新时，RocksDB 会暂时缓存，等到需要读取时再一次性执行多个更新操作。

5. CDF 统计，如果估计的 Doc 命中率过高，可以计算补集。

**缺点**：

1. 需要依赖额外的 RocksDB。

2. 需要对齐事务管理。



## 4 事务处理能力

索引的设计需要和 NeuG 的事务管理对齐。任何一个事务开启时，都是在当前这个时间点的快照上执行操作。

**基础需求**：

读-读并发：两个读进程可以并发访问索引。

读-写并发：写进程在写入时不阻塞已有的读进程，且写入的数据不会出现在已有的读进程中。

注意全文索引和 HNSW 索引略有不同：HNSW 索引的目的是召回结果，因此允许在同一个索引里出现多个事务的数据，最后通过 NeuG 的 MVCC 控制可见性。但是全文索引涉及到数值计算，每个事务必须独立维护各自的索引数据。

这就意味着三种实现方式：

1. 自行实现索引数据结构，必须也同时实现Copy-On-Write。
2. 引入其他依赖，如 RocksDB，其事务管理也必须和 NeuG 保持一致，
3. 由 NeuG 实现索引数据管理，即将数据存放在 NeuG 自己的数据表中，自然确保数据和索引的事务管理对齐。