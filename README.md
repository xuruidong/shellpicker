# 扇贝背单词

基于扇贝网页 API 的单词爬取与终端交互式背单词工具。

## 功能

- 爬取扇贝单词书数据：单词、音标、释义、词典例句、真题例句
- 例句中文提示的四层递进回忆练习
- SM-2 简化调度：答对间隔递增，答错重来

## 依赖

```bash
pip install -r requirements.txt
```

## 数据准备

1. 创建 `shanbay_config.json`，填入扇贝的 cookie 与 csrftoken（浏览器登录后从开发者工具复制）。

2. 爬取并导入单词书：

```bash
python crawl_book.py                # 爬取单词书 -> book_learned.json / book_unlearned.json
python words_tool.py import-book    # 导入 SQLite (shanbay.db)
python words_tool.py senses --limit 100          # 爬释义
python words_tool.py examples --limit 100        # 爬词典例句
python words_tool.py ext-examples --limit 100    # 爬真题例句
python words_tool.py status         # 查看进度
```

## 背单词

```bash
python study.py                        # 默认: 10 个新词 + 20 个复习词
python study.py --new 20 --review 10   # 自定义新词/复习数量
python study.py --listening            # 听力模式(第1层只听发音)
python study.py --all                  # 允许没有例句的词
python study.py --no-audio             # 不播放音频
```

### 练习流程（四层递进）

| 层 | 提示内容 | 答对 | 记分 |
|---|---|---|---|
| 1 | 中文例句（目标词用【】标出），可 `p` 播发音 | 写出英文整句 | 熟（5 分） |
| 2 | 完整释义（词性 + 中文 + 英文 + 音标） | 写出英文整句 | 熟（4 分） |
| 3 | 英文挖空 `______` | 写出目标单词 | 不熟（3 分） |
| 4 | 答案（目标词大写）+ 中文翻译 | — | 忘了（1 分） |

### 交互按键

- 直接输入英文句子 / 单词 → 提交答案
- 直接回车 → 跳过当前层
- 输入 `p` → 播放发音

随时 `Ctrl+C` 退出，已完成卡片的进度已保存。

## 相关文档

- [docs/PLAN.md](docs/PLAN.md) — 设计文档
- [docs/README_study.md](docs/README_study.md) — study.py 详细说明
- [docs/SCHEMA_REFACTOR.md](docs/SCHEMA_REFACTOR.md) — 多书改造规划
