# shanbay.db 多书改造方案

> 状态：**规划中，尚未实施**。当前代码仍是单书结构。

## 背景

当前 `shanbay.db`（由 `words_tool.py` 生成、`study.py` 消费）是**单书结构**：

- 所有表都没有「书」维度，只按 `vocab_id`（扇贝全局词 id）存
- 书的标识 `plbtl`（六级精简版）**硬编码**在 `crawl_book.py` 的 URL 里，未写入数据库
- `words.source` 字段存的是 `learned`/`unlearned`（学习状态），不是书 id

问题：爬第二本书时，同一个词（扇贝 `vocab_id` 全局唯一）会互相覆盖，学习状态/熟悉度/复习进度全部污染。

## 目标结构（三层分层）

### 第 1 层：词典数据（全局共享，一本书爬一次即可）

```
words          vocab_id 主键 → word, ipa_us, ipa_uk, audio_us, audio_uk
senses         sense_id 主键 → vocab_id, dictionary_id, pos, definition_cn, definition_en
base_examples  example_id 主键 → vocab_id, content_en, content_cn, audio(可选)
ext_examples   example_id 主键 → vocab_id, dictionary_id, content_en, content_cn,
                                  source_name, year, star, pos, sense_cn, audio
```

关键：`vocab_id` 在扇贝全局唯一，`abrupt` 在六级书和考研书里是同一个 id，词典数据不重复爬。

### 第 2 层：书与词的关联（每本书一份）

```
books          book_id 主键 → name
book_words     (book_id, vocab_id) 复合主键 → learned/unlearned, familiarity,
                                              unit, unknown_count
```

### 第 3 层：复习进度（按书隔离）

```
study          (book_id, vocab_id) 复合主键 → ease, interval, due_date, reps, lapses
```

## 拆表理由

- **base 与 ext 例句分开**：base（`vocab_examples` 接口）字段简单（只有 content_en/cn）；ext（`ext_examples` 接口，必须带 dict_id）有出处/年份/星级/词性/释义/音频。当前混在 `examples` 表里，base 例句这些字段全是空值
- **词典数据不重复**：书维度只存「关系 + 学习状态」
- **复习按书隔离**：同一词在不同书里的 SM-2 进度独立

## 需保留不动

- `senses.dictionary_id` 是「词典」维度（`azxdq`=六级真题、`eeyvu`=基础词典），不是「书」维度，保留在 `senses` 表即可

## 待办 / 改动点

1. `words_tool.py`：新增 `books`、`book_words` 表；`examples` 拆为 `base_examples` + `ext_examples`；`import-book` 写 `book_words` 而非 `words.source`
2. `crawl_book.py`：书 id 参数化（去掉硬编码 `plbtl`）
3. `study.py`：从 `book_words` 取词；`study` 表挂 `book_id`
4. **先找出扇贝「单词书列表」接口**，拿到每本书的 `book_id` + 书名，避免手填 id

## 未决问题

- 扇贝单词书列表接口的 URL 和返回结构（待探测）
