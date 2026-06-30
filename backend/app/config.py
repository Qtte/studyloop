"""
Configuration helpers for the StudyLoop backend MVP.

═══════════════════════════════════════════════════════════════════════════════
设计说明
═══════════════════════════════════════════════════════════════════════════════

BackendSettings 是 StudyLoop 的"环境配置对象"，从 .env 文件加载一切运行时参数。

为什么用 Pydantic BaseModel 而不是 os.getenv 散落各处？
────────────────────────────────────────
1. 类型安全：Field + 默认值，所有配置值都有类型约束。
2. 集中管理：所有环境变量在同一个类里，一目了然。
3. 可测试：测试里可以直接 BackendSettings(use_mock_llm=True, ...) 构造测试配置。
4. 属性计算：should_use_mock_llm / has_vector_retrieval_config 是计算属性，
   封装了"无 key 时自动 mock"的判断逻辑。

Mock LLM 的触发条件（should_use_mock_llm）：
────────────────────────────────
- USE_MOCK_LLM=true → 强制 mock
- OPENAI_API_KEY 缺失 → 自动降级 mock
  两个条件任意一个满足即可，保证本地开发/测试不需要配置任何 API key。

向量检索的配置检测（has_vector_retrieval_config）：
────────────────────────────────
- 需要同时配置 QDRANT_URL + QDRANT_API_KEY + EMBED_API_KEY
- 缺任一则回退到内存关键词检索（SimpleKeywordRetriever）
- 这就是"优雅降级"：高级功能可用时自动启用，不可用时静默回退
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, Field


PROJECT_ROOT = Path(__file__).resolve().parents[2]
# 从项目根目录加载 .env 文件
load_dotenv(PROJECT_ROOT / ".env")


def _first_env(*keys: str, default: str | None = None) -> str | None:
    """
    从一组环境变量中取第一个非空值。

    这样支持"OPENAI_API_KEY 或 LLM_API_KEY 均可"的多 key 回退，
    兼容不同环境的命名习惯。
    """
    for key in keys:
        value = os.getenv(key)
        if value is not None and value != "":
            return value
    return default


class BackendSettings(BaseModel):
    """运行时设置 —— 从仓库 .env 文件加载。"""

    project_root: Path = Field(default=PROJECT_ROOT)

    # ── LLM 配置 ──
    # 支持 OPENAI_* 和 LLM_* 两种前缀（兼容不同习惯）
    openai_api_key: str | None = Field(
        default_factory=lambda: _first_env("OPENAI_API_KEY", "LLM_API_KEY")
    )
    openai_base_url: str = Field(
        default_factory=lambda: _first_env(
            "OPENAI_BASE_URL",
            "LLM_BASE_URL",
            default="https://api.openai.com/v1",
        )
        or "https://api.openai.com/v1"
    )
    openai_model: str = Field(
        default_factory=lambda: _first_env(
            "OPENAI_MODEL", "LLM_MODEL_ID", default="gpt-4o-mini"
        )
        or "gpt-4o-mini"
    )

    # ── 向量检索配置（可选，预留）──
    # 配置 Qdrant 向量数据库 + embedding 模型，启用语义检索
    # 未配置时自动降级为内存关键词检索
    qdrant_url: str | None = Field(
        default_factory=lambda: os.getenv("QDRANT_URL")
    )
    qdrant_api_key: str | None = Field(
        default_factory=lambda: os.getenv("QDRANT_API_KEY")
    )
    qdrant_collection: str = Field(
        default_factory=lambda: os.getenv(
            "QDRANT_COLLECTION", "hello_agents_vectors"
        )
    )
    qdrant_vector_size: int = Field(
        default_factory=lambda: int(os.getenv("QDRANT_VECTOR_SIZE", "384"))
    )
    qdrant_distance: str = Field(
        default_factory=lambda: os.getenv("QDRANT_DISTANCE", "cosine")
    )
    qdrant_timeout: float = Field(
        default_factory=lambda: float(os.getenv("QDRANT_TIMEOUT", "30"))
    )

    # ── Embedding 模型配置（可选，预留）──
    embed_model_type: str = Field(
        default_factory=lambda: os.getenv("EMBED_MODEL_TYPE", "openai")
    )
    embed_model_name: str = Field(
        default_factory=lambda: _first_env(
            "EMBED_MODEL_NAME",
            "OPENAI_EMBED_MODEL",
            default="text-embedding-3-small",
        )
        or "text-embedding-3-small"
    )
    embed_api_key: str | None = Field(
        default_factory=lambda: _first_env("EMBED_API_KEY", "OPENAI_API_KEY")
    )
    embed_base_url: str = Field(
        default_factory=lambda: _first_env(
            "EMBED_BASE_URL",
            "OPENAI_BASE_URL",
            default="https://api.openai.com/v1",
        )
        or "https://api.openai.com/v1"
    )

    # ── Mock 模式 ──
    # true 时使用确定性 MockLLM，不需要任何 API key
    use_mock_llm: bool = Field(
        default_factory=lambda: os.getenv("USE_MOCK_LLM", "false").lower()
        == "true"
    )

    # ── 笔记存储路径 ──
    notes_dir: Path = Field(
        default=PROJECT_ROOT / "backend" / "data" / "notes"
    )
    notes_index_path: Path = Field(
        default=PROJECT_ROOT
        / "backend"
        / "data"
        / "notes"
        / "notes_index.json"
    )
    study_history_db_path: Path | None = Field(
        default_factory=lambda: (
            Path(os.getenv("STUDY_HISTORY_DB_PATH"))
            if os.getenv("STUDY_HISTORY_DB_PATH")
            else None
        )
    )

    # ── 计算属性 ──

    @property
    def should_use_mock_llm(self) -> bool:
        """
        当显式启用 mock 或没有配置 API key 时使用 mock 模型。

        这个属性确保了"开箱即用"——本地 clone 下来不需要配置 .env。
        """
        return self.use_mock_llm or not self.openai_api_key

    @property
    def has_vector_retrieval_config(self) -> bool:
        """
        是否已配置向量检索所需的三个关键参数。

        缺任一则回退到内存关键词检索（SimpleKeywordRetriever）。
        """
        return bool(
            self.qdrant_url and self.qdrant_api_key and self.embed_api_key
        )

    @property
    def resolved_study_history_db_path(self) -> Path:
        """返回学习历史 SQLite 的最终路径。"""
        return self.study_history_db_path or (self.notes_dir / "study_history.db")


@lru_cache(maxsize=1)
def get_settings() -> BackendSettings:
    """
    返回缓存的 BackendSettings 单例。

    @lru_cache(maxsize=1) 确保只从 .env 加载一次，后续调用复用同一个实例。
    FastAPI 的依赖注入用 get_settings 而非全局变量，方便测试时替换配置。
    """
    return BackendSettings()
