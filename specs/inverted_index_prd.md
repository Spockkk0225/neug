# 倒排索引 PRD

## 1. 整体介绍

### 1.1 什么是倒排索引

倒排索引是一种以“词语”为中心、从关键词反向定位到文档或数据位置的索引结构。它可以和正向索引对比理解。

正向索引（Forward Index）以“文档”为中心，如果要找所有包含“手机”的文档，系统需要遍历每一个文档，并检查文档的词语列表中是否包含“手机”。当文档数量很大时，这种方式效率较低，时间复杂度通常接近 `O(N)`。

```text
文档 ID -> 包含的词语列表

Doc1 -> ["苹果", "手机", "便宜"]
Doc2 -> ["电脑", "办公", "轻薄"]
Doc3 -> ["手机", "新品", "拍照"]
```


倒排索引（Inverted Index）以“词语”为中心，当用户查询“手机”时，系统可以直接通过关键词定位到相关文档列表，而不需要扫描全部文档。理想情况下，关键词在词典中的定位可以接近 `O(1)`。

```text
词语 -> 包含该词语的文档 ID 列表

"手机" -> [Doc1, Doc3, Doc5]
"苹果" -> [Doc1, Doc8]
"便宜" -> [Doc1, Doc6, Doc9]
```


### 1.2 应用场景

倒排索引适用于需要从关键词、词项或 token 快速定位数据的场景，包括但不限于：

- 全文检索：在文章、评论、描述、日志等文本字段中查找包含某些关键词的数据。
- 词频分析：统计某个词出现在哪些文档中，以及在每个文档中出现多少次。
- 相关性排序：结合词频、文档长度、逆文档频率等指标，对搜索结果进行打分排序。
- BM25 排序：基于倒排索引维护的词频和文档统计信息，为搜索结果计算更符合全文检索语义的相关性分数。
- 标签或属性检索：当属性值可以被拆分为多个 token 时，也可以用倒排索引快速定位候选数据。

### 1.3 抽象结构

一个典型的倒排索引由两部分组成：

* 词典（Term Dictionary / Vocabulary）
  * 存储所有不重复的词条（Term）。
  * 通常按字典序排序，便于快速查找（使用 B+ 树、哈希表或 FST 等结构）。

* 倒排列表（Posting List）
  * 每个词条对应一个列表，记录哪些文档包含了这个词。
  * 列表中每个元素称为倒排项（Posting），通常包含文档 ID（DocID）作为唯一标识文档，以及其它一些元数据。

### 1.4 示例说明

假设有三条数据：

```text
doc1: "graph database supports query"
doc2: "graph index improves search"
doc3: "database query optimizer"
```

分词后可以得到如下倒排索引：

```text
graph    -> [doc1, doc2]
database -> [doc1, doc3]
query    -> [doc1, doc3]
index    -> [doc2]
search   -> [doc2]
optimizer-> [doc3]
```

当用户查询 `graph` 时，系统直接返回候选集合 `[doc1, doc2]`。

当用户查询 `graph AND database` 时，系统可以对两个倒排列表求交集：

```text
graph    -> [doc1, doc2]
database -> [doc1, doc3]
result   -> [doc1]
```

当用户查询 `graph OR database` 时，系统可以对两个倒排列表求并集：

```text
result -> [doc1, doc2, doc3]
```

## 2. 实现技术方案

### 2.1 核心目标

倒排索引的本质目标是实现一个能够快速定位关键词的映射结构 `keyword -> posting list`。

从数据结构角度看，它可以抽象为一个 `HashMap<string, PostingList>`。


### 2.2 基础写入流程

写入或更新数据时，需要将文本字段转换为倒排索引中的条目。

1. 获取待索引的属性列，例如 title、content、description。

2. 对属性内容进行分词，得到 token 列表。

3. 对 token 做归一化处理，例如小写化、去标点、去停用词、词干化。

4. 记录每个 token 的元数据，包括主键 id 和其他元数据。

5. 对每个 token 更新 dict 和 posting list。

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

### 2.3 基础查询流程

* 对于单关键词查询 `query: graph`：

  1. 在 dict 中查找 `graph`。

  2. 获取 `graph` 对应的 posting list。

  3. 返回 posting list 中的主键 ID。

* 对于多关键词查询 `query: graph database`：

  1. 分词得到 `graph`、`database`。

  2. 分别读取两个 posting list。

  3. 根据查询语义执行 AND / OR / NOT。

  4. 返回合并后的结果。


### 2.4 KV-Store 选型

全文索引的关键难点在于实现一个高效的 KV-Store：能够快速通过 term 定位 posting list，同时兼顾内存占用、查询性能、写入成本和后续扩展能力。

从抽象上看，倒排索引需要支持如下 KV 访问模式：

```text
term -> posting list
```

不同 KV-Store 方案的核心差异在于 term dictionary 如何组织，以及 posting list 如何被定位和读取。

* Baseline：HashMap KV-Store。HashMap 可以直接建立 `term -> posting list` 的映射，查询路径简单，平均情况下可以 `O(1)` 快速定位关键词。缺点是 HashMap 需要存储原始 key，占用空间较高；同时 HashMap 存在 hash 冲突，极端情况下会影响查询性能。

* Apache Lucene 方案：有限状态自动机FSA。Trie 或有限状态自动机等结构会将词条之间共享的前缀合并，避免重复存储相同前缀，从而大幅减少内存占用。查询时先在自动机中定位 term，再找到对应 posting list。

* Sqlite 方案：B 树。B 树是 SQLite 等系统中常见的有序 KV 组织结构，本质上是一种排序树。相比 HashMap，B 树通过有序结构定位关键词，避免了 hash 冲突导致的性能下降问题，同时天然支持范围扫描和有序遍历。

* Zvec（RocksDB）方案：LSM 树。RocksDB 基于 LSM Tree，适合高吞吐写入和增量更新场景。

* LevelDB 方案：LSM 树。LevelDB 已经在 Neug 中引入依赖，可以作为全文索引 KV-Store 的早期落地方案。相比 RocksDB，LevelDB 功能更轻量，工程集成成本较低；但在并发写入、compaction 调优、缓存控制等方面能力相对有限。

### 2.5 事务处理能力

全文索引需要支持插入、删除等写操作，同时保证读查询不会被写操作阻塞或读到不一致状态。核心思路是将写入过程与已有可读索引结构隔离，只有在事务提交时才将变更合并到可见结构中。

#### 2.5.1 插入操作

插入时不直接修改已有的 term dictionary 和 posting list，而是在一个独立的数据结构上计算新增内容。可以理解为为当前事务或当前批次新开一个分区：

```text
committed index: 已提交、可被查询读取的索引结构
insert partition: 当前插入操作独立构建的新分区
```

插入流程：

```text
1. 对新增文档或属性值进行分词。
2. 在独立 insert partition 中构建 term -> posting list。
3. 插入过程中不修改 committed index。
4. 事务 commit 时，将 insert partition merge 到 committed index。
```

这种设计保证写操作不会影响正在进行的读操作。读查询可以继续访问已提交的索引快照；插入操作则在独立分区中完成计算。由于多个插入操作之间可以使用不同分区，它们之间也可以并发执行，最终在 commit 阶段完成合并。

#### 2.5.2 删除操作

删除时不立即从 posting list 中物理移除文档 ID，而是通过 bitmap 进行逻辑删除：

```text
delete bitmap:
  doc_id -> deleted / visible
```

查询时仍然可以读取原有 posting list，但需要结合 delete bitmap 过滤已经删除的文档。这样可以避免每次删除都重写 posting list，降低写放大。

只有在 compact 阶段，系统才会真正执行物理删除：

```text
1. 扫描 posting list。
2. 根据 delete bitmap 过滤已删除文档。
3. 重写新的 posting list。
4. 清理已经合并完成的 delete bitmap。
```

通过逻辑删除与后台 compact 分离，删除操作可以快速完成，同时读操作仍然可以基于稳定的索引结构执行。

### 2.6 查询优化

全文索引查询优化可以先围绕 posting list 的读取、合并和过滤展开，初期重点包括：

- 高频关键词改用 bitmap 存储：对于命中文档数量很大的 term，posting list 可能非常长。可以将这类高频 term 的结果集改用 bitmap 表示，提升集合交、并、差计算效率。
- 更新索引使用 merge：插入和删除产生的增量索引不直接重写主索引，而是在查询或 compact 阶段通过 merge 合并多个分区，降低写放大。
- 通过 CDF 统计优化查询逻辑：维护 term 的累计分布或频率统计信息，查询时优先处理选择性更高的 term，减少需要扫描和合并的 posting list 数量。
- posting list 分段：将长 posting list 拆成多个 segment，查询时按需读取，避免一次加载过大的列表。
- 热点缓存：缓存高频 term 的 posting list、bitmap 或统计信息，减少重复查询成本。

## 3. 基于倒排索引扩展的能力

倒排索引不只是关键词到数据 ID 的映射。随着 posting list 中维护的元数据增加，可以扩展出更丰富的检索能力。

### 3.1 短语检索

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

- `position`：记录每个 token 的 position，可在posting list中直接维护。


### 3.2 高亮展示

搜索结果高亮用于在返回结果中标记命中的关键词或片段。要实现高亮，仅知道某个文档包含关键词还不够，还需要知道关键词在原文中的具体位置。

例如：

```text
content: "Graph database query"
query: "database"
```

如果索引中记录了 `database` 的 token 位置或字符范围，查询返回时即可定位原文片段，并将命中词高亮展示。

**需要的元数据**：

- `offset`：记录关键词的起始位置，用于精准定位和高亮展示。


### 3.3 BM25 排序

BM25 是全文检索中常用的相关性排序算法。它通常需要以下信息：

```text
tf: term frequency，词在当前文档中的出现次数
df: document frequency，包含该词的文档数量
N: 总文档数量
doc_len: 当前文档长度
avg_doc_len: 平均文档长度
```

如果倒排索引只保存 `keyword -> [doc_id]`，无法计算 BM25。为了支持 BM25，需要在 dict、posting list 或 doc metadata 中维护统计信息。

**需要的元数据**：

- `tf`：词在当前文档中的出现次数，可以直接在 posting list 中维护。每个倒排项不仅记录 `DocID`，也记录该词在该文档中的出现次数。
- `df`：包含该词的文档数量，需要在 dict 中维护，或者直接获取posting list的长度。
- `N`：总文档数量，可以通过 NeuG 本身的图数据统计接口返回，不需要在倒排索引中重复维护。
- `doc_len`：文档长度，需要单独的 hashmap 存储，例如 `doc_id -> doc_len`。
- `avg_doc_len`：平均文档长度，可以通过单独维护变量 `tot_doc_len` 计算得到，即 `avg_doc_len = tot_doc_len / N`。
