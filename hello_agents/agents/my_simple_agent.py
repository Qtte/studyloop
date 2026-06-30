"""Backward-compatible simple agent wrapper."""

from __future__ import annotations

from typing import Optional

from ..core.config import Config
from ..core.llm import HelloAgentsLLM
from ..tools.registry import ToolRegistry
from .simple_agent import SimpleAgent


class MySimpleAgent(SimpleAgent):
    """Compatibility wrapper around :class:`SimpleAgent`."""

    def __init__(
        self,
        name: str,
        llm: HelloAgentsLLM,
        system_prompt: Optional[str] = None,
        config: Optional[Config] = None,
        tool_registry: Optional[ToolRegistry] = None,
        enable_tool_calling: bool = True,
        max_tool_iterations: int = 3,
    ):
        super().__init__(
            name=name,
            llm=llm,
            system_prompt=system_prompt,
            config=config,
            tool_registry=tool_registry,
            enable_tool_calling=enable_tool_calling,
            max_tool_iterations=max_tool_iterations,
        )
