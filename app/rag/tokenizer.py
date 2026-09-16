import jieba

# 模块加载时预热，不要等到查询时才加载，否则第一次查询会非常慢
jieba.initialize()


def chinese_tokenizer(text: str) -> list[str]:
    """中英混合分词：对中文用jieba精确模式切词（词语级），英文按默认方式切分。

    BM25Retriever默认tokenizer只做lowercase + 按非字母数字字符 split，对中文会退化成单字（unigram）匹配，查准率低。用jieba后整个词语作为一个term参与BM25的IDF/词频计算，显著提升中文相关性。
    """
    return [t for t in jieba.lcut(text) if t.strip()]
