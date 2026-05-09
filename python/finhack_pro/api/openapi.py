"""
FinHack Pro API 文档自动化模块

提供 OpenAPI 3.0 规范生成、Markdown/HTML 文档输出、客户端 SDK 代码生成等功能。
支持从 FastAPI 应用自动提取路由，也支持手动为 Rust 核心 API 编写文档。

Usage:
    from finhack_pro.api.openapi import APIDocGenerator

    generator = APIDocGenerator()
    spec = generator.generate_openapi_spec()
    md = generator.generate_markdown_docs(spec)
"""

from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class EndpointDoc:
    """API 端点文档数据类"""
    path: str
    method: str
    summary: str = ""
    description: str = ""
    parameters: List[Dict[str, Any]] = field(default_factory=list)
    request_body: Optional[Dict[str, Any]] = None
    responses: Dict[str, Any] = field(default_factory=dict)
    tags: List[str] = field(default_factory=list)


class APIDocGenerator:
    """API 文档生成器

    从 FastAPI 应用自动生成 OpenAPI 3.0 规范，
    并支持 Markdown、HTML 文档输出及客户端 SDK 代码生成。

    Args:
        app: FastAPI 应用实例（可选）
        output_dir: 文档输出目录
    """

    def __init__(self, app: Optional[Any] = None, output_dir: str = "docs/api") -> None:
        self.app = app
        self.output_dir = output_dir
        self._rust_core_endpoints = self._build_rust_core_endpoints()

    # ------------------------------------------------------------------
    # OpenAPI Spec Generation
    # ------------------------------------------------------------------

    def generate_openapi_spec(self) -> dict:
        """生成 OpenAPI 3.0 规范

        优先从 FastAPI 应用提取，否则基于 Rust 核心 bridge 端点构建。

        Returns:
            OpenAPI 3.0 规范字典
        """
        if self.app is not None:
            spec = self._extract_from_fastapi()
        else:
            spec = self._build_default_spec()

        # 合并 Rust 核心 bridge 端点
        self._merge_rust_core_endpoints(spec)
        return spec

    def _extract_from_fastapi(self) -> dict:
        """从 FastAPI 应用提取 OpenAPI 规范"""
        try:
            if hasattr(self.app, "openapi"):
                return self.app.openapi()
        except Exception:
            pass
        return self._build_default_spec()

    def _build_default_spec(self) -> dict:
        """构建默认 OpenAPI 3.0 规范骨架"""
        return {
            "openapi": "3.0.3",
            "info": {
                "title": "FinHack Pro API",
                "description": "FinHack Pro 量化交易系统 API 文档",
                "version": "2.1.0",
                "contact": {
                    "name": "FinHack Pro Team",
                },
                "license": {"name": "MIT"},
            },
            "paths": {},
            "components": {
                "schemas": {},
                "securitySchemes": {
                    "BearerAuth": {
                        "type": "http",
                        "scheme": "bearer",
                        "bearerFormat": "JWT",
                    }
                },
            },
            "tags": [
                {"name": "Health", "description": "健康检查"},
                {"name": "Bridge", "description": "Rust 核心 Bridge 接口"},
                {"name": "Backtest", "description": "回测管理"},
                {"name": "Trading", "description": "交易管理"},
                {"name": "Market Data", "description": "行情数据"},
            ],
        }

    def _build_rust_core_endpoints(self) -> Dict[str, Dict[str, Any]]:
        """构建 Rust 核心 bridge 端点文档"""
        return {
            "/health": {
                "get": {
                    "tags": ["Health"],
                    "summary": "健康检查",
                    "description": "检查 Rust 核心引擎运行状态，返回版本、内存使用等基本信息。",
                    "operationId": "health_check",
                    "responses": {
                        "200": {
                            "description": "健康状态",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "status": {"type": "string", "example": "healthy"},
                                            "version": {"type": "string", "example": "2.1.0"},
                                            "uptime_seconds": {"type": "integer", "example": 3600},
                                            "memory_mb": {"type": "number", "example": 128.5},
                                        },
                                    }
                                }
                            },
                        },
                        "503": {"description": "服务不可用"},
                    },
                }
            },
            "/bridge/indicators": {
                "post": {
                    "tags": ["Bridge"],
                    "summary": "计算技术指标",
                    "description": "通过 Rust 核心计算技术指标（SMA、EMA、RSI、MACD 等），支持批量计算。",
                    "operationId": "calculate_indicators",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["symbol", "indicators"],
                                    "properties": {
                                        "symbol": {
                                            "type": "string",
                                            "description": "标的代码",
                                            "example": "000001.SZ",
                                        },
                                        "indicators": {
                                            "type": "array",
                                            "items": {"type": "string"},
                                            "description": "指标列表",
                                            "example": ["sma", "ema", "rsi", "macd"],
                                        },
                                        "params": {
                                            "type": "object",
                                            "description": "指标参数",
                                            "example": {"sma_period": 20, "rsi_period": 14},
                                        },
                                    },
                                }
                            }
                        },
                    },
                    "responses": {
                        "200": {
                            "description": "计算结果",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "symbol": {"type": "string"},
                                            "indicators": {
                                                "type": "object",
                                                "additionalProperties": {
                                                    "type": "array",
                                                    "items": {"type": "number"},
                                                },
                                            },
                                        },
                                    }
                                }
                            },
                        },
                        "400": {"description": "参数错误"},
                    },
                }
            },
            "/bridge/batch_backtest": {
                "post": {
                    "tags": ["Bridge"],
                    "summary": "批量回测",
                    "description": "通过 Rust 核心执行批量回测任务，支持参数网格搜索和并行执行。",
                    "operationId": "batch_backtest",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["strategies"],
                                    "properties": {
                                        "strategies": {
                                            "type": "array",
                                            "items": {
                                                "type": "object",
                                                "properties": {
                                                    "name": {"type": "string"},
                                                    "params": {"type": "object"},
                                                },
                                            },
                                            "description": "策略配置列表",
                                        },
                                        "symbols": {
                                            "type": "array",
                                            "items": {"type": "string"},
                                            "description": "标的列表",
                                        },
                                        "start_date": {"type": "string", "format": "date"},
                                        "end_date": {"type": "string", "format": "date"},
                                        "initial_capital": {
                                            "type": "number",
                                            "example": 1000000,
                                        },
                                        "parallel": {
                                            "type": "boolean",
                                            "default": True,
                                        },
                                    },
                                }
                            }
                        },
                    },
                    "responses": {
                        "200": {
                            "description": "回测结果",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "task_id": {"type": "string"},
                                            "status": {"type": "string"},
                                            "results": {
                                                "type": "array",
                                                "items": {"type": "object"},
                                            },
                                        },
                                    }
                                }
                            },
                        },
                        "400": {"description": "参数错误"},
                        "500": {"description": "回测执行失败"},
                    },
                }
            },
            "/bridge/parallel_signals": {
                "post": {
                    "tags": ["Bridge"],
                    "summary": "并行信号计算",
                    "description": "通过 Rust 核心并行计算多策略信号，支持大规模标的扫描。",
                    "operationId": "parallel_signals",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["symbols", "strategies"],
                                    "properties": {
                                        "symbols": {
                                            "type": "array",
                                            "items": {"type": "string"},
                                            "description": "标的列表",
                                        },
                                        "strategies": {
                                            "type": "array",
                                            "items": {"type": "string"},
                                            "description": "策略名称列表",
                                        },
                                        "params": {
                                            "type": "object",
                                            "description": "策略参数",
                                        },
                                    },
                                }
                            }
                        },
                    },
                    "responses": {
                        "200": {
                            "description": "信号计算结果",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "signals": {
                                                "type": "array",
                                                "items": {
                                                    "type": "object",
                                                    "properties": {
                                                        "symbol": {"type": "string"},
                                                        "strategy": {"type": "string"},
                                                        "signal": {"type": "string"},
                                                        "strength": {"type": "number"},
                                                    },
                                                },
                                            }
                                        },
                                    }
                                }
                            },
                        },
                        "400": {"description": "参数错误"},
                    },
                }
            },
        }

    def _merge_rust_core_endpoints(self, spec: dict) -> None:
        """将 Rust 核心 bridge 端点合并到 OpenAPI 规范"""
        paths = spec.setdefault("paths", {})
        for path, methods in self._rust_core_endpoints.items():
            if path not in paths:
                paths[path] = methods
            else:
                for method, detail in methods.items():
                    if method not in paths[path]:
                        paths[path][method] = detail

    # ------------------------------------------------------------------
    # Export Functions
    # ------------------------------------------------------------------

    def export_openapi_json(self, output_path: str = "") -> str:
        """导出 OpenAPI 规范为 JSON 文件

        Args:
            output_path: 输出文件路径，为空时使用默认路径

        Returns:
            输出文件的绝对路径
        """
        spec = self.generate_openapi_spec()
        if not output_path:
            output_path = os.path.join(self.output_dir, "openapi.json")

        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(spec, f, indent=2, ensure_ascii=False)

        return os.path.abspath(output_path)

    def export_openapi_yaml(self, output_path: str = "") -> str:
        """导出 OpenAPI 规范为 YAML 文件

        Args:
            output_path: 输出文件路径，为空时使用默认路径

        Returns:
            输出文件的绝对路径
        """
        spec = self.generate_openapi_spec()
        if not output_path:
            output_path = os.path.join(self.output_dir, "openapi.yaml")

        Path(output_path).parent.mkdir(parents=True, exist_ok=True)

        try:
            import yaml

            with open(output_path, "w", encoding="utf-8") as f:
                yaml.dump(spec, f, default_flow_style=False, allow_unicode=True)
        except ImportError:
            # Fallback: 手动序列化为简单 YAML
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(json.dumps(spec, indent=2, ensure_ascii=False))

        return os.path.abspath(output_path)

    # ------------------------------------------------------------------
    # Documentation Generation
    # ------------------------------------------------------------------

    def generate_markdown_docs(self, spec: Optional[dict] = None) -> str:
        """从 OpenAPI 规范生成 Markdown 文档

        Args:
            spec: OpenAPI 规范字典，为空时自动生成

        Returns:
            Markdown 格式文档字符串
        """
        if spec is None:
            spec = self.generate_openapi_spec()

        lines: List[str] = []
        info = spec.get("info", {})
        lines.append(f"# {info.get('title', 'API Documentation')}")
        lines.append("")
        if info.get("description"):
            lines.append(info["description"])
            lines.append("")
        lines.append(f"**Version:** {info.get('version', 'N/A')}")
        lines.append("")

        # Tags
        tags = spec.get("tags", [])
        if tags:
            lines.append("## Tags")
            lines.append("")
            for tag in tags:
                lines.append(f"- **{tag.get('name', '')}**: {tag.get('description', '')}")
            lines.append("")

        # Endpoints
        endpoints = self._extract_endpoints(spec)
        if endpoints:
            lines.append("## Endpoints")
            lines.append("")

            # Group by tag
            tag_groups: Dict[str, List[EndpointDoc]] = {}
            for ep in endpoints:
                tag = ep.tags[0] if ep.tags else "Default"
                tag_groups.setdefault(tag, []).append(ep)

            for tag, eps in tag_groups.items():
                lines.append(f"### {tag}")
                lines.append("")
                for ep in eps:
                    method_upper = ep.method.upper()
                    lines.append(f"#### `{method_upper} {ep.path}`")
                    lines.append("")
                    if ep.summary:
                        lines.append(f"**{ep.summary}**")
                        lines.append("")
                    if ep.description:
                        lines.append(ep.description)
                        lines.append("")

                    # Parameters
                    if ep.parameters:
                        lines.append("**Parameters:**")
                        lines.append("")
                        lines.append("| Name | In | Type | Required | Description |")
                        lines.append("|------|-----|------|----------|-------------|")
                        for p in ep.parameters:
                            name = p.get("name", "")
                            loc = p.get("in", "")
                            ptype = p.get("schema", {}).get("type", "") if isinstance(p.get("schema"), dict) else str(p.get("type", ""))
                            required = "Yes" if p.get("required", False) else "No"
                            desc = p.get("description", "")
                            lines.append(f"| {name} | {loc} | {ptype} | {required} | {desc} |")
                        lines.append("")

                    # Request Body
                    if ep.request_body:
                        lines.append("**Request Body:**")
                        lines.append("")
                        lines.append("```json")
                        content = ep.request_body.get("content", {})
                        schema = content.get("application/json", {}).get("schema", {})
                        example = self._generate_schema_example(schema)
                        lines.append(json.dumps(example, indent=2, ensure_ascii=False))
                        lines.append("```")
                        lines.append("")

                    # Responses
                    if ep.responses:
                        lines.append("**Responses:**")
                        lines.append("")
                        for code, resp in ep.responses.items():
                            desc = resp.get("description", "")
                            lines.append(f"- `{code}`: {desc}")
                        lines.append("")

                    lines.append("---")
                    lines.append("")

        return "\n".join(lines)

    def generate_html_docs(self, spec: Optional[dict] = None) -> str:
        """从 OpenAPI 规范生成 HTML 文档

        Args:
            spec: OpenAPI 规范字典，为空时自动生成

        Returns:
            HTML 格式文档字符串
        """
        if spec is None:
            spec = self.generate_openapi_spec()

        info = spec.get("info", {})
        endpoints = self._extract_endpoints(spec)

        html_parts: List[str] = []
        html_parts.append("""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title} - API Documentation</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            line-height: 1.6;
            color: #333;
            background: #f5f5f5;
        }}
        .container {{ max-width: 1200px; margin: 0 auto; padding: 20px; }}
        .header {{
            background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
            color: white;
            padding: 40px;
            border-radius: 8px;
            margin-bottom: 30px;
        }}
        .header h1 {{ font-size: 2em; margin-bottom: 10px; }}
        .header .version {{ opacity: 0.8; font-size: 1.1em; }}
        .endpoint {{
            background: white;
            border-radius: 8px;
            margin-bottom: 20px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
            overflow: hidden;
        }}
        .endpoint-header {{
            padding: 15px 20px;
            border-bottom: 1px solid #eee;
            display: flex;
            align-items: center;
            gap: 10px;
        }}
        .method {{
            padding: 4px 12px;
            border-radius: 4px;
            font-weight: bold;
            font-size: 0.85em;
            text-transform: uppercase;
        }}
        .method-get {{ background: #61affe; color: white; }}
        .method-post {{ background: #49cc90; color: white; }}
        .method-put {{ background: #fca130; color: white; }}
        .method-delete {{ background: #f93e3e; color: white; }}
        .method-patch {{ background: #50e3c2; color: white; }}
        .endpoint-path {{ font-family: monospace; font-size: 1.1em; }}
        .endpoint-body {{ padding: 20px; }}
        .endpoint-body h3 {{ margin-bottom: 10px; color: #1a1a2e; }}
        .tag-section {{ margin-bottom: 30px; }}
        .tag-title {{
            font-size: 1.3em;
            color: #1a1a2e;
            border-bottom: 2px solid #1a1a2e;
            padding-bottom: 5px;
            margin-bottom: 15px;
        }}
        pre {{
            background: #f8f8f8;
            padding: 15px;
            border-radius: 4px;
            overflow-x: auto;
            font-size: 0.9em;
        }}
        code {{ font-family: 'Fira Code', monospace; }}
        table {{
            width: 100%;
            border-collapse: collapse;
            margin: 10px 0;
        }}
        th, td {{
            padding: 8px 12px;
            text-align: left;
            border-bottom: 1px solid #eee;
        }}
        th {{ background: #f8f8f8; font-weight: 600; }}
        .response-code {{
            display: inline-block;
            padding: 2px 8px;
            border-radius: 3px;
            font-weight: bold;
            font-size: 0.85em;
        }}
        .response-2xx {{ background: #d4edda; color: #155724; }}
        .response-4xx {{ background: #fff3cd; color: #856404; }}
        .response-5xx {{ background: #f8d7da; color: #721c24; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>{title}</h1>
            <p class="version">Version {version}</p>
            <p>{description}</p>
        </div>
""".format(
            title=info.get("title", "API Documentation"),
            version=info.get("version", "N/A"),
            description=info.get("description", ""),
        ))

        # Group endpoints by tag
        tag_groups: Dict[str, List[EndpointDoc]] = {}
        for ep in endpoints:
            tag = ep.tags[0] if ep.tags else "Default"
            tag_groups.setdefault(tag, []).append(ep)

        for tag, eps in tag_groups.items():
            html_parts.append(f'        <div class="tag-section">')
            html_parts.append(f'            <h2 class="tag-title">{self._escape_html(tag)}</h2>')
            for ep in eps:
                method_cls = f"method-{ep.method.lower()}"
                html_parts.append(f"""
            <div class="endpoint">
                <div class="endpoint-header">
                    <span class="method {method_cls}">{ep.method.upper()}</span>
                    <span class="endpoint-path">{self._escape_html(ep.path)}</span>
                </div>
                <div class="endpoint-body">
                    <h3>{self._escape_html(ep.summary)}</h3>
                    <p>{self._escape_html(ep.description)}</p>""")

                if ep.parameters:
                    html_parts.append("""
                    <h4>Parameters</h4>
                    <table>
                        <tr><th>Name</th><th>In</th><th>Type</th><th>Required</th><th>Description</th></tr>""")
                    for p in ep.parameters:
                        name = self._escape_html(p.get("name", ""))
                        loc = self._escape_html(p.get("in", ""))
                        ptype = ""
                        schema = p.get("schema", {})
                        if isinstance(schema, dict):
                            ptype = self._escape_html(schema.get("type", ""))
                        required = "Yes" if p.get("required", False) else "No"
                        desc = self._escape_html(p.get("description", ""))
                        html_parts.append(
                            f'<tr><td>{name}</td><td>{loc}</td><td>{ptype}</td>'
                            f"<td>{required}</td><td>{desc}</td></tr>"
                        )
                    html_parts.append("                    </table>")

                if ep.request_body:
                    content = ep.request_body.get("content", {})
                    schema = content.get("application/json", {}).get("schema", {})
                    example = self._generate_schema_example(schema)
                    example_json = json.dumps(example, indent=2, ensure_ascii=False)
                    html_parts.append(f"""
                    <h4>Request Body</h4>
                    <pre><code>{self._escape_html(example_json)}</code></pre>""")

                if ep.responses:
                    html_parts.append("<h4>Responses</h4><table><tr><th>Code</th><th>Description</th></tr>")
                    for code, resp in ep.responses.items():
                        desc = self._escape_html(resp.get("description", ""))
                        code_cls = "response-2xx"
                        if code.startswith("4"):
                            code_cls = "response-4xx"
                        elif code.startswith("5"):
                            code_cls = "response-5xx"
                        html_parts.append(
                            f'<tr><td><span class="response-code {code_cls}">{code}</span></td>'
                            f"<td>{desc}</td></tr>"
                        )
                    html_parts.append("</table>")

                html_parts.append("""
                </div>
            </div>""")

            html_parts.append("        </div>")

        html_parts.append("""
    </div>
</body>
</html>""")
        return "\n".join(html_parts)

    # ------------------------------------------------------------------
    # Endpoint Extraction
    # ------------------------------------------------------------------

    def _extract_endpoints(self, spec: dict) -> List[EndpointDoc]:
        """从 OpenAPI 规范提取端点文档列表

        Args:
            spec: OpenAPI 规范字典

        Returns:
            EndpointDoc 列表
        """
        endpoints: List[EndpointDoc] = []
        paths = spec.get("paths", {})

        for path, methods in paths.items():
            for method, detail in methods.items():
                if method.startswith("x-") or method in ("servers", "parameters", "$ref"):
                    continue

                parameters = detail.get("parameters", [])
                request_body = detail.get("requestBody")
                responses = detail.get("responses", {})
                tags = detail.get("tags", [])
                summary = detail.get("summary", "")
                description = detail.get("description", "")

                endpoints.append(
                    EndpointDoc(
                        path=path,
                        method=method,
                        summary=summary,
                        description=description,
                        parameters=parameters,
                        request_body=request_body,
                        responses=responses,
                        tags=tags,
                    )
                )

        return endpoints

    # ------------------------------------------------------------------
    # Example Generation
    # ------------------------------------------------------------------

    def _generate_example_requests(self, spec: dict) -> Dict[str, Any]:
        """为每个端点生成示例请求/响应

        Args:
            spec: OpenAPI 规范字典

        Returns:
            以路径为键的示例请求字典
        """
        examples: Dict[str, Any] = {}
        endpoints = self._extract_endpoints(spec)

        for ep in endpoints:
            key = f"{ep.method.upper()} {ep.path}"
            example: Dict[str, Any] = {
                "method": ep.method.upper(),
                "url": ep.path,
                "headers": {"Content-Type": "application/json"},
            }

            if ep.request_body:
                content = ep.request_body.get("content", {})
                schema = content.get("application/json", {}).get("schema", {})
                example["body"] = self._generate_schema_example(schema)

            if ep.parameters:
                example["query_params"] = {
                    p["name"]: self._generate_param_example(p)
                    for p in ep.parameters
                    if p.get("in") == "query"
                }

            # Generate example response from first 2xx response
            for code, resp in ep.responses.items():
                if code.startswith("2"):
                    content = resp.get("content", {})
                    schema = content.get("application/json", {}).get("schema", {})
                    example["response"] = {
                        "status": int(code),
                        "body": self._generate_schema_example(schema),
                    }
                    break

            examples[key] = example

        return examples

    def _generate_schema_example(self, schema: Dict[str, Any]) -> Any:
        """从 JSON Schema 生成示例值"""
        if not schema:
            return {}

        schema_type = schema.get("type", "object")

        if "example" in schema:
            return schema["example"]

        if schema_type == "object":
            result: Dict[str, Any] = {}
            props = schema.get("properties", {})
            required = set(schema.get("required", []))
            for name, prop in props.items():
                if name in required or len(result) < 3:
                    result[name] = self._generate_schema_example(prop)
            return result

        if schema_type == "array":
            items = schema.get("items", {})
            return [self._generate_schema_example(items)]

        if schema_type == "string":
            fmt = schema.get("format", "")
            if fmt == "date":
                return "2024-01-01"
            if fmt == "date-time":
                return "2024-01-01T00:00:00Z"
            return "string"

        if schema_type == "integer":
            return 0

        if schema_type == "number":
            return 0.0

        if schema_type == "boolean":
            return True

        return {}

    def _generate_param_example(self, param: Dict[str, Any]) -> Any:
        """为参数生成示例值"""
        schema = param.get("schema", {})
        if isinstance(schema, dict):
            return self._generate_schema_example(schema)
        return param.get("example", "string")

    # ------------------------------------------------------------------
    # Client SDK Generation
    # ------------------------------------------------------------------

    def _generate_client_code(self, spec: dict, language: str = "python") -> str:
        """生成客户端 SDK 代码

        Args:
            spec: OpenAPI 规范字典
            language: 编程语言 (python / javascript)

        Returns:
            客户端代码字符串
        """
        if language == "python":
            return self._generate_python_client(spec)
        elif language == "javascript":
            return self._generate_javascript_client(spec)
        else:
            raise ValueError(f"Unsupported language: {language}")

    def _generate_python_client(self, spec: dict) -> str:
        """生成 Python 客户端代码"""
        endpoints = self._extract_endpoints(spec)
        info = spec.get("info", {})

        lines: List[str] = []
        lines.append('"""')
        lines.append(f"Auto-generated {info.get('title', 'API')} Python Client")
        lines.append(f"Version: {info.get('version', 'N/A')}")
        lines.append("")
        lines.append("Generated by FinHack Pro APIDocGenerator")
        lines.append('"""')
        lines.append("")
        lines.append("from __future__ import annotations")
        lines.append("")
        lines.append("import json")
        lines.append("from typing import Any, Dict, List, Optional")
        lines.append("")
        lines.append("import httpx")
        lines.append("")
        lines.append("")
        lines.append("class FinHackProClient:")
        lines.append('    """Auto-generated API client"""')
        lines.append("")
        lines.append('    def __init__(self, base_url: str = "http://localhost:8080", api_key: str = "") -> None:')
        lines.append('        self.base_url = base_url.rstrip("/")')
        lines.append('        self._headers = {"Content-Type": "application/json"}')
        lines.append('        if api_key:')
        lines.append('            self._headers["Authorization"] = f"Bearer {api_key}"')
        lines.append("")
        lines.append("    def _request(self, method: str, path: str, **kwargs: Any) -> Dict[str, Any]:")
        lines.append('        """Send HTTP request"""')
        lines.append("        with httpx.Client(base_url=self.base_url, headers=self._headers) as client:")
        lines.append("            resp = client.request(method, path, **kwargs)")
        lines.append("            resp.raise_for_status()")
        lines.append("            return resp.json()")
        lines.append("")

        for ep in endpoints:
            method_name = self._python_method_name(ep)
            http_method = ep.method.lower()
            params_str = ""
            body_str = ""

            # Path parameters
            path_params = [p for p in ep.parameters if p.get("in") == "path"]
            query_params = [p for p in ep.parameters if p.get("in") == "query"]

            all_params = []
            for p in path_params + query_params:
                ptype = "str"
                schema = p.get("schema", {})
                if isinstance(schema, dict):
                    t = schema.get("type", "str")
                    if t == "integer":
                        ptype = "int"
                    elif t == "number":
                        ptype = "float"
                    elif t == "boolean":
                        ptype = "bool"
                    elif t == "array":
                        ptype = "List[str]"
                default = ""
                if not p.get("required", False):
                    default = " = None"
                all_params.append(f"{p['name']}: Optional[{ptype}]{default}")

            if ep.request_body:
                all_params.append("body: Optional[Dict[str, Any]] = None")

            if not all_params:
                all_params.append("**kwargs: Any")

            params_str = ", ".join(all_params)

            lines.append(f"    def {method_name}(self, {params_str}) -> Dict[str, Any]:")
            lines.append(f'        """{ep.summary or ep.method.upper() + " " + ep.path}"""')

            # Build path
            path = ep.path
            for p in path_params:
                lines.append(f'        path = "{path}".replace("{{{p["name"]}}}", str({p["name"]}))')
                path = ep.path  # reset for reference

            # Build query params
            if query_params:
                lines.append("        params: Dict[str, Any] = {}")
                for p in query_params:
                    lines.append(f'        if {p["name"]} is not None:')
                    lines.append(f'            params["{p["name"]}"] = {p["name"]}')

            # Build request kwargs
            kwargs_parts = []
            if query_params:
                kwargs_parts.append("params=params")
            if ep.request_body:
                kwargs_parts.append("json=body")

            request_kwargs = ", ".join(kwargs_parts) if kwargs_parts else ""

            if path_params:
                lines.append(f"        return self._request('{http_method}', path{', ' + request_kwargs if request_kwargs else ''})")
            else:
                lines.append(f"        return self._request('{http_method}', '{ep.path}'{', ' + request_kwargs if request_kwargs else ''})")
            lines.append("")

        return "\n".join(lines)

    def _generate_javascript_client(self, spec: dict) -> str:
        """生成 JavaScript 客户端代码"""
        endpoints = self._extract_endpoints(spec)
        info = spec.get("info", {})

        lines: List[str] = []
        lines.append("/**")
        lines.append(f" * Auto-generated {info.get('title', 'API')} JavaScript Client")
        lines.append(f" * Version: {info.get('version', 'N/A')}")
        lines.append(" * Generated by FinHack Pro APIDocGenerator")
        lines.append(" */")
        lines.append("")
        lines.append("class FinHackProClient {")
        lines.append('  constructor(baseUrl = "http://localhost:8080", apiKey = "") {')
        lines.append("    this.baseUrl = baseUrl.replace(/\\/$/, '');")
        lines.append("    this.headers = { 'Content-Type': 'application/json' };")
        lines.append("    if (apiKey) this.headers['Authorization'] = `Bearer ${apiKey}`;")
        lines.append("  }")
        lines.append("")
        lines.append("  async _request(method, path, options = {}) {")
        lines.append("    const resp = await fetch(`${this.baseUrl}${path}`, {")
        lines.append("      method,")
        lines.append("      headers: this.headers,")
        lines.append("      ...options,")
        lines.append("    });")
        lines.append("    if (!resp.ok) throw new Error(`HTTP ${resp.status}: ${resp.statusText}`);")
        lines.append("    return resp.json();")
        lines.append("  }")
        lines.append("")

        for ep in endpoints:
            method_name = self._js_method_name(ep)
            http_method = ep.method.upper()
            params_js = []
            body_js = ""

            path_params = [p for p in ep.parameters if p.get("in") == "path"]
            query_params = [p for p in ep.parameters if p.get("in") == "query"]

            for p in path_params + query_params:
                params_js.append(p["name"])

            if ep.request_body:
                params_js.append("body")

            if not params_js:
                params_js_str = ""
            else:
                params_js_str = ", " + ", ".join(params_js)

            lines.append(f"  async {method_name}({params_js_str.lstrip(', ')}) {{")
            lines.append(f"    // {ep.summary or ep.method.upper() + ' ' + ep.path}")

            path = ep.path
            for p in path_params:
                lines.append(f"    const path = `{ep.path}`.replace(/{{{p['name']}}}/g, {p['name']});")

            options_parts = []
            if ep.request_body:
                options_parts.append("body: JSON.stringify(body)")

            if options_parts:
                options_str = ", " + ", ".join(options_parts)
            else:
                options_str = ""

            if path_params:
                lines.append(f"    return this._request('{http_method}', path{options_str});")
            else:
                lines.append(f"    return this._request('{http_method}', '{ep.path}'{options_str});")

            lines.append("  }")
            lines.append("")

        lines.append("}")
        lines.append("")
        lines.append("module.exports = { FinHackProClient };")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _python_method_name(self, ep: EndpointDoc) -> str:
        """从端点路径生成 Python 方法名"""
        path = ep.path.strip("/").replace("/", "_").replace("-", "_").replace("{", "").replace("}", "")
        method_prefix = {
            "get": "get",
            "post": "create",
            "put": "update",
            "delete": "delete",
            "patch": "patch",
        }
        prefix = method_prefix.get(ep.method.lower(), ep.method.lower())
        if not path:
            return f"{prefix}_root"
        return f"{prefix}_{path}"

    def _js_method_name(self, ep: EndpointDoc) -> str:
        """从端点路径生成 JavaScript 方法名 (camelCase)"""
        parts = ep.path.strip("/").split("/")
        method_prefix = {
            "get": "get",
            "post": "create",
            "put": "update",
            "delete": "delete",
            "patch": "patch",
        }
        prefix = method_prefix.get(ep.method.lower(), ep.method.lower())
        result = [prefix]
        for part in parts:
            clean = re.sub(r"[{}-]", "", part)
            if clean:
                result.append(clean)
        return "".join(result)

    @staticmethod
    def _escape_html(text: str) -> str:
        """转义 HTML 特殊字符"""
        return (
            text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
        )
