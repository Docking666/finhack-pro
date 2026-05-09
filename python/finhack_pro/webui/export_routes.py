"""
结果导出API路由

支持导出回测结果为PDF和Excel格式，以及策略分享功能。
"""

from __future__ import annotations

import os
import tempfile
from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Response
from fastapi.responses import FileResponse, StreamingResponse
from loguru import logger
from pydantic import BaseModel, Field

from finhack_pro.webui.exporters.excel_exporter import ExcelExporter
from finhack_pro.webui.exporters.pdf_exporter import PDFExporter
from finhack_pro.webui.exporters.strategy_share import StrategySharer
from finhack_pro.webui.models import APIResponse

router = APIRouter(prefix="/api/export", tags=["export"])


# ============================================================
# 请求/响应模型
# ============================================================

class ExportRequest(BaseModel):
    """导出请求"""
    format: str = Field(..., description="导出格式: pdf, excel")
    backtest_result: Dict[str, Any] = Field(..., description="回测结果数据")
    params: Optional[Dict[str, Any]] = Field(None, description="回测参数")
    include_trades: bool = Field(True, description="是否包含交易记录")
    include_equity_curve: bool = Field(True, description="是否包含权益曲线")
    include_metrics: bool = Field(True, description="是否包含指标")


class StrategyShareRequest(BaseModel):
    """策略分享请求"""
    strategy_config: Dict[str, Any] = Field(..., description="策略配置")


class StrategyImportRequest(BaseModel):
    """策略导入请求"""
    share_code: str = Field(..., description="分享码")


class StrategyValidateRequest(BaseModel):
    """策略验证请求"""
    strategy_config: Dict[str, Any] = Field(..., description="策略配置")


# ============================================================
# 导出器实例
# ============================================================

def get_pdf_exporter() -> PDFExporter:
    """获取PDF导出器实例"""
    return PDFExporter()


def get_excel_exporter() -> ExcelExporter:
    """获取Excel导出器实例"""
    return ExcelExporter()


def get_strategy_sharer() -> StrategySharer:
    """获取策略分享器实例"""
    return StrategySharer()


# ============================================================
# 导出API
# ============================================================

@router.post("/backtest/pdf")
async def export_backtest_pdf(request: ExportRequest):
    """导出回测结果为PDF
    
    生成专业的回测报告PDF文件，包含封面、摘要、权益曲线、交易记录等。
    """
    try:
        exporter = get_pdf_exporter()
        
        # 生成文件名
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        strategy = request.params.get("strategy", "backtest") if request.params else "backtest"
        filename = f"backtest_{strategy}_{timestamp}.pdf"
        
        # 导出为字节流
        pdf_bytes = exporter.export_to_bytes(
            result=request.backtest_result,
            params=request.params,
        )
        
        # 返回文件流
        return StreamingResponse(
            iter([pdf_bytes]),
            media_type="application/pdf",
            headers={
                "Content-Disposition": f"attachment; filename={filename}",
            }
        )
        
    except Exception as e:
        logger.error(f"导出PDF失败: {e}")
        raise HTTPException(status_code=500, detail=f"导出PDF失败: {str(e)}")


@router.post("/backtest/excel")
async def export_backtest_excel(request: ExportRequest):
    """导出回测结果为Excel
    
    生成多工作表的Excel文件，包含摘要、权益曲线、交易记录等。
    """
    try:
        exporter = get_excel_exporter()
        
        # 生成文件名
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        strategy = request.params.get("strategy", "backtest") if request.params else "backtest"
        filename = f"backtest_{strategy}_{timestamp}.xlsx"
        
        # 导出为字节流
        excel_bytes = exporter.export_to_bytes(
            result=request.backtest_result,
            params=request.params,
        )
        
        # 返回文件流
        return StreamingResponse(
            iter([excel_bytes]),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={
                "Content-Disposition": f"attachment; filename={filename}",
            }
        )
        
    except Exception as e:
        logger.error(f"导出Excel失败: {e}")
        raise HTTPException(status_code=500, detail=f"导出Excel失败: {str(e)}")


@router.post("/backtest/json")
async def export_backtest_json(request: ExportRequest):
    """导出回测结果为JSON
    
    返回原始JSON数据，便于进一步处理。
    """
    try:
        import json
        
        # 生成文件名
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        strategy = request.params.get("strategy", "backtest") if request.params else "backtest"
        filename = f"backtest_{strategy}_{timestamp}.json"
        
        # 准备导出数据
        export_data = {
            "params": request.params,
            "result": request.backtest_result,
            "export_time": datetime.now().isoformat(),
        }
        
        # 过滤不需要的内容
        if not request.include_trades and "trades" in export_data["result"]:
            export_data["result"]["trades"] = []
        if not request.include_equity_curve and "equity_curve" in export_data["result"]:
            export_data["result"]["equity_curve"] = []
        
        json_bytes = json.dumps(export_data, ensure_ascii=False, indent=2).encode('utf-8')
        
        return StreamingResponse(
            iter([json_bytes]),
            media_type="application/json",
            headers={
                "Content-Disposition": f"attachment; filename={filename}",
            }
        )
        
    except Exception as e:
        logger.error(f"导出JSON失败: {e}")
        raise HTTPException(status_code=500, detail=f"导出JSON失败: {str(e)}")


# ============================================================
# 策略分享API
# ============================================================

@router.post("/strategy/share")
async def share_strategy(request: StrategyShareRequest):
    """生成策略分享码
    
    将策略配置编码为可分享的字符串，便于分享给其他用户。
    """
    try:
        sharer = get_strategy_sharer()
        
        # 验证配置
        is_valid, errors = sharer.validate_config(request.strategy_config)
        if not is_valid:
            raise HTTPException(status_code=400, detail=f"策略配置无效: {', '.join(errors)}")
        
        # 生成分享码
        share_code = sharer.share(request.strategy_config)
        
        return APIResponse(
            message="分享码生成成功",
            data={
                "share_code": share_code,
                "code_length": len(share_code),
            }
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"生成分享码失败: {e}")
        raise HTTPException(status_code=500, detail=f"生成分享码失败: {str(e)}")


@router.post("/strategy/import")
async def import_strategy(request: StrategyImportRequest):
    """导入分享的策略配置
    
    解码分享码，返回策略配置。
    """
    try:
        sharer = get_strategy_sharer()
        
        # 导入分享码
        strategy_config = sharer.import_shared(request.share_code)
        
        # 验证配置
        is_valid, errors = sharer.validate_config(strategy_config)
        
        return APIResponse(
            message="策略导入成功" if is_valid else "策略导入成功，但配置存在问题",
            data={
                "strategy_config": strategy_config,
                "valid": is_valid,
                "errors": errors if not is_valid else [],
            }
        )
        
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"导入策略失败: {e}")
        raise HTTPException(status_code=500, detail=f"导入策略失败: {str(e)}")


@router.post("/strategy/validate")
async def validate_strategy(request: StrategyValidateRequest):
    """验证策略配置
    
    检查策略配置是否有效。
    """
    try:
        sharer = get_strategy_sharer()
        
        is_valid, errors = sharer.validate_config(request.strategy_config)
        
        return APIResponse(
            message="配置有效" if is_valid else "配置存在问题",
            data={
                "valid": is_valid,
                "errors": errors,
            }
        )
        
    except Exception as e:
        logger.error(f"验证策略失败: {e}")
        raise HTTPException(status_code=500, detail=f"验证策略失败: {str(e)}")


@router.post("/strategy/qrcode")
async def generate_qrcode(request: StrategyShareRequest):
    """生成策略分享二维码
    
    将策略配置编码为分享码，并生成二维码图片。
    """
    try:
        sharer = get_strategy_sharer()
        
        # 生成分享码
        share_code = sharer.share(request.strategy_config)
        
        # 生成二维码
        qr_bytes = sharer.generate_qrcode(share_code)
        
        if qr_bytes is None:
            raise HTTPException(status_code=500, detail="生成二维码失败，请确保已安装qrcode库")
        
        # 返回二维码图片
        return StreamingResponse(
            iter([qr_bytes]),
            media_type="image/png",
            headers={
                "Content-Disposition": "attachment; filename=strategy_qrcode.png",
            }
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"生成二维码失败: {e}")
        raise HTTPException(status_code=500, detail=f"生成二维码失败: {str(e)}")


@router.post("/strategy/share-info")
async def get_share_info(request: StrategyImportRequest):
    """获取分享码信息
    
    返回分享码的基本信息，不导入完整配置。
    """
    try:
        sharer = get_strategy_sharer()
        
        info = sharer.get_share_info(request.share_code)
        
        return APIResponse(
            message="获取分享信息成功" if info.get("valid") else "分享码无效",
            data=info
        )
        
    except Exception as e:
        logger.error(f"获取分享信息失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取分享信息失败: {str(e)}")


# ============================================================
# 批量导出API
# ============================================================

class BatchExportRequest(BaseModel):
    """批量导出请求"""
    format: str = Field(..., description="导出格式: pdf, excel")
    backtest_results: list[Dict[str, Any]] = Field(..., description="回测结果列表")
    params_list: Optional[list[Dict[str, Any]]] = Field(None, description="回测参数列表")


@router.post("/backtest/batch")
async def export_backtest_batch(request: BatchExportRequest):
    """批量导出回测结果
    
    将多个回测结果打包导出。
    """
    try:
        import zipfile
        from io import BytesIO
        
        if request.format not in ["pdf", "excel"]:
            raise HTTPException(status_code=400, detail="不支持的导出格式")
        
        # 创建内存中的zip文件
        zip_buffer = BytesIO()
        
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
            for i, result in enumerate(request.backtest_results):
                params = request.params_list[i] if request.params_list and i < len(request.params_list) else None
                
                # 生成文件名
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                strategy = params.get("strategy", f"backtest_{i}") if params else f"backtest_{i}"
                
                if request.format == "pdf":
                    exporter = get_pdf_exporter()
                    file_bytes = exporter.export_to_bytes(result=result, params=params)
                    filename = f"backtest_{strategy}_{timestamp}.pdf"
                else:
                    exporter = get_excel_exporter()
                    file_bytes = exporter.export_to_bytes(result=result, params=params)
                    filename = f"backtest_{strategy}_{timestamp}.xlsx"
                
                zip_file.writestr(filename, file_bytes)
        
        zip_buffer.seek(0)
        
        # 返回zip文件
        return StreamingResponse(
            iter([zip_buffer.getvalue()]),
            media_type="application/zip",
            headers={
                "Content-Disposition": f"attachment; filename=backtest_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip",
            }
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"批量导出失败: {e}")
        raise HTTPException(status_code=500, detail=f"批量导出失败: {str(e)}")
