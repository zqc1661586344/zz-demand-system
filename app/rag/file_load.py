"""文件加载器注册表，暂时用不上，先留着"""

import json
from pathlib import Path
from langchain_core.documents import Document

from app.logging_config import get_logger

logger = get_logger(__name__)
# 文件加载器注册表
FILE_LOAD_REGISTRY = {}


def register_file_exporter(*names: str):
    """类装饰器，被装饰的类自动注册文件导出器"""

    def decorator(cls):
        for name in names:
            FILE_LOAD_REGISTRY[name] = cls
            cls.mime_type = name
        return cls

    return decorator


# PDF文件加载器
@register_file_exporter("application/pdf")
class PDFLoader:
    def __init__(self, file_path: str):
        self.file_path = file_path

    def load(self) -> list[Document]:
        """加载 PDF 文件，返回文档列表"""
        from langchain_community.document_loaders import PyPDFLoader

        loader = PyPDFLoader(self.file_path)
        return loader.load()


# markdown文件加载器
# TODO：markdown有"text/plain" or "text/markdown"两种mime类型
@register_file_exporter("text/markdown")
class MarkdownLoader:
    def __init__(self, file_path: str):
        self.file_path = file_path

    def load(self) -> list[Document]:
        """加载 markdown 文件，返回文档列表"""
        from langchain_community.document_loaders import MarkdownLoader

        loader = MarkdownLoader(self.file_path)
        return loader.load()


# word文件加载器
@register_file_exporter("application/vnd.msxmlformats-officedocument.wordprocessingml.document")
class WordLoader:
    def __init__(self, file_path: str):
        self.file_path = file_path

    def load(self) -> list[Document]:
        """加载 word 文件，返回文档列表"""
        from langchain_community.document_loaders import Docx2txtLoader

        loader = Docx2txtLoader(self.file_path)
        return loader.load()


# csv文件加载器
@register_file_exporter("text/csv")
class CSVLoader:
    def __init__(self, file_path: str):
        self.file_path = file_path

    def load(self) -> list[Document]:
        """加载 csv 文件，返回文档列表"""
        from langchain_community.document_loaders import CSVLoader

        loader = CSVLoader(file_path=self.file_path)
        return loader.load()


# html文件加载器
@register_file_exporter("text/html")
class HTMLLoader:
    def __init__(self, file_path: str):
        self.file_path = file_path

    def load(self) -> list[Document]:
        """加载 html 文件，返回文档列表"""
        from langchain_community.document_loaders import BSHTMLLoader

        loader = BSHTMLLoader(self.file_path)
        return loader.load()


# excel文件加载器（pandas + openpyxl 逐 sheet 转 CSV 文本，避免 unstructured 重依赖；每 sheet 一个 Document）
@register_file_exporter("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
class ExcelLoader:
    def __init__(self, file_path: str):
        self.file_path = file_path

    def load(self) -> list[Document]:
        """加载 excel 文件，返回文档列表"""
        # from langchain_community.document_loaders import ExcelLoader

        import pandas as pd

        sheets = pd.read_excel(self.file_path, sheet_name=None)  # dict[sheet_name, DataFrame]
        return [
            Document(
                page_content=f"### Sheet: {name}\n\n{df.astype(str).to_csv(index=False)}",
                metadata={"source": self.file_path},
            )
            for name, df in sheets.items()
        ]


# ppt文件加载器（python-pptx 遍历每个 slide 的 text frame，每张 slide 一个 Document）
@register_file_exporter("application/vnd.openxmlformats-officedocument.presentationml.presentation")
class PPTLoader:
    def __init__(self, file_path: str):
        self.file_path = file_path

    def load(self) -> list[Document]:
        """加载 ppt 文件，返回文档列表"""
        # from langchain_community.document_loaders import PPTLoader

        from pptx import Presentation

        prs = Presentation(self.file_path)
        docs = []
        for i, slide in enumerate(prs.slides, 1):
            parts = [sh.text for sh in slide.shapes if getattr(sh, "has_text_frame", False)]
            docs.append(
                Document(
                    page_content=f"### Slide {i}\n\n" + "\n\n".join(parts),
                    metadata={"source": self.file_path},
                )
            )
        return docs


# toml文件加载器
@register_file_exporter("application/toml")
class TomlLoader:
    def __init__(self, file_path: str):
        self.file_path = file_path

    def load(self) -> list[Document]:
        """加载 toml 文件，返回文档列表"""
        from langchain_community.document_loaders import TomlLoader

        loader = TomlLoader(self.file_path)
        return loader.load()


# json文件（自定义处理，兼容法规 seed_data 格式和通用 JSON）
@register_file_exporter("application/json")
class JSONLoader:
    def __init__(self, file_path: str):
        self.file_path = Path(file_path)

    def load(self) -> list[Document]:
        """加载 json 文件，返回文档列表。自定义 JSON 加载器，兼容多种格式：

        1. 法规 seed_data 格式: {title, articles: [{article_number, chapter?, content}]}  → 每条条文一个 Document
        2. 对象数组: [{key: value, ...}, ...]                                                    → 每个对象一个 Document
        3. 字符串数组: ["text", ...]                                                             → 每个字符串一个 Document
        4. 普通对象: {key: value, ...}                                                           → 整体一个 Document
        """
        # from langchain_community.document_loaders import JSONLoader

        with open(str(self.file_path), "r", encoding="utf-8") as f:
            data = json.load(f)

        # 格式 1：法规 seed_data — 顶层有 articles 数组
        if isinstance(data, dict) and isinstance(data.get("articles"), list):
            regulation_title = data.get("title") or data.get("name") or self.file_path.stem
            regulation_type = data.get("regulation_type", "")
            docs: list[Document] = []
            for art in data["articles"]:
                content = art.get("content", "").strip()
                if not content:
                    continue
                parts = [f"# {regulation_title}"]
                if regulation_type:
                    parts.append(f"类型：{regulation_type}")
                if art.get("chapter"):
                    parts.append(f"章节：{art['chapter']}")
                if art.get("article_number"):
                    parts.append(f"条文编号：{art['article_number']}")
                parts.append("")
                parts.append(content)
                docs.append(
                    Document(
                        page_content="\n".join(parts),
                        metadata={
                            "source": str(self.file_path),
                            "regulation_title": regulation_title,
                            "article_number": art.get("article_number", ""),
                        },
                    )
                )

            if docs:
                logger.info(
                    f"json (regulation format): extracted {len(docs)} articles from {regulation_title}"
                )
                return docs

        # 格式 2：对象数组
        if isinstance(data, list) and data and isinstance(data[0], dict):
            docs = []
            for item in data:
                lines = []
                for k, v in item.items():
                    if isinstance(v, (dict, list)):
                        lines.append(f"{k}: {json.dumps(v, ensure_ascii=False)}")
                    else:
                        lines.append(f"{k}: {v}")
                docs.append(
                    Document(
                        page_content="\n".join(lines),
                        metadata={"source": str(self.file_path)},
                    )
                )
            logger.info(f"json (list of objects): {len(docs)} items")
            return docs

        # 格式 3：字符串数组
        if isinstance(data, list) and data and isinstance(data[0], str):
            docs = [
                Document(page_content=item, metadata={"source": str(self.file_path)})
                for item in data
                if isinstance(item, str) and item.strip()
            ]
            logger.info(f"json (list of strings): {len(docs)} items")
            return docs

        # 格式 4：普通对象或其他 — 整体序列化为文本
        docs = [
            Document(
                page_content=json.dumps(data, ensure_ascii=False, indent=2),
                metadata={"source": str(self.file_path)},
            )
        ]
        logger.info("json (generic): single document")
        return docs
