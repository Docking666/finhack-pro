"""
策略分享功能

将策略配置编码为可分享的字符串，支持导入分享的策略配置。
支持生成分享二维码。
"""

from __future__ import annotations

import base64
import gzip
import hashlib
import json
from datetime import datetime
from typing import Any, Dict, Optional

from loguru import logger


class StrategySharer:
    """策略分享器类
    
    用于将策略配置编码为可分享的字符串，以及导入分享的策略配置。
    """
    
    # 版本号，用于兼容性检查
    VERSION = "1.0"
    
    # 分享码前缀
    PREFIX = "FHP"  # FinHack Pro
    
    def __init__(self):
        """初始化策略分享器"""
        pass
    
    def share(self, strategy_config: Dict[str, Any]) -> str:
        """生成分享码
        
        将策略配置压缩、编码为可分享的字符串。
        
        Args:
            strategy_config: 策略配置字典
        
        Returns:
            分享码字符串
        
        Example:
            >>> sharer = StrategySharer()
            >>> config = {"strategy": "dual_thrust", "symbols": ["600519.SH"], ...}
            >>> code = sharer.share(config)
            >>> print(code)
            FHP-xxxxx...
        """
        try:
            # 添加元数据
            config_with_meta = {
                "version": self.VERSION,
                "timestamp": datetime.now().isoformat(),
                "config": strategy_config,
            }
            
            # 序列化为JSON
            json_str = json.dumps(config_with_meta, ensure_ascii=False, separators=(',', ':'))
            
            # 压缩
            compressed = gzip.compress(json_str.encode('utf-8'))
            
            # Base64编码
            encoded = base64.urlsafe_b64encode(compressed).decode('ascii')
            
            # 生成校验码（取前8位）
            checksum = hashlib.md5(encoded.encode()).hexdigest()[:8]
            
            # 组合分享码
            share_code = f"{self.PREFIX}-{checksum}-{encoded}"
            
            logger.info(f"生成策略分享码，长度: {len(share_code)}")
            
            return share_code
            
        except Exception as e:
            logger.error(f"生成分享码失败: {e}")
            raise ValueError(f"生成分享码失败: {e}")
    
    def import_shared(self, share_code: str) -> Dict[str, Any]:
        """导入分享的策略
        
        解码分享码，返回策略配置。
        
        Args:
            share_code: 分享码字符串
        
        Returns:
            策略配置字典
        
        Raises:
            ValueError: 分享码无效或已损坏
        """
        try:
            # 验证格式
            if not share_code.startswith(f"{self.PREFIX}-"):
                raise ValueError("无效的分享码格式")
            
            # 解析分享码
            parts = share_code.split("-", 2)
            if len(parts) != 3:
                raise ValueError("分享码格式错误")
            
            prefix, checksum, encoded = parts
            
            # 验证校验码
            expected_checksum = hashlib.md5(encoded.encode()).hexdigest()[:8]
            if checksum != expected_checksum:
                raise ValueError("分享码校验失败，可能已损坏")
            
            # Base64解码
            compressed = base64.urlsafe_b64decode(encoded.encode('ascii'))
            
            # 解压
            json_bytes = gzip.decompress(compressed)
            
            # 解析JSON
            config_with_meta = json.loads(json_bytes.decode('utf-8'))
            
            # 版本检查
            version = config_with_meta.get("version", "0.0")
            if version != self.VERSION:
                logger.warning(f"分享码版本 {version} 与当前版本 {self.VERSION} 不同，可能存在兼容性问题")
            
            # 返回策略配置
            strategy_config = config_with_meta.get("config", {})
            
            logger.info(f"成功导入策略配置，时间戳: {config_with_meta.get('timestamp', 'unknown')}")
            
            return strategy_config
            
        except ValueError:
            raise
        except Exception as e:
            logger.error(f"导入分享码失败: {e}")
            raise ValueError(f"导入分享码失败: {e}")
    
    def generate_qrcode(self, share_code: str, output_path: Optional[str] = None) -> Optional[bytes]:
        """生成分享二维码
        
        将分享码编码为二维码图片。
        
        Args:
            share_code: 分享码字符串
            output_path: 输出文件路径（可选）
        
        Returns:
            二维码图片的字节流（如果未指定output_path）
        """
        try:
            from io import BytesIO

            import qrcode
            
            # 创建二维码
            qr = qrcode.QRCode(
                version=1,
                error_correction=qrcode.constants.ERROR_CORRECT_L,
                box_size=10,
                border=4,
            )
            qr.add_data(share_code)
            qr.make(fit=True)
            
            # 生成图片
            img = qr.make_image(fill_color="black", back_color="white")
            
            if output_path:
                # 保存到文件
                img.save(output_path)
                logger.info(f"二维码已保存到: {output_path}")
                return None
            else:
                # 返回字节流
                buffer = BytesIO()
                img.save(buffer, format='PNG')
                buffer.seek(0)
                return buffer.getvalue()
                
        except ImportError:
            logger.warning("qrcode库未安装，无法生成二维码")
            return None
        except Exception as e:
            logger.error(f"生成二维码失败: {e}")
            return None
    
    def validate_config(self, config: Dict[str, Any]) -> tuple[bool, list[str]]:
        """验证策略配置
        
        检查策略配置是否有效。
        
        Args:
            config: 策略配置字典
        
        Returns:
            (是否有效, 错误信息列表)
        """
        errors = []
        
        # 检查必需字段
        required_fields = ["strategy"]
        for field in required_fields:
            if field not in config:
                errors.append(f"缺少必需字段: {field}")
        
        # 检查策略类型
        valid_strategies = ["dual_thrust", "momentum", "mean_reversion"]
        strategy = config.get("strategy")
        if strategy and strategy not in valid_strategies:
            errors.append(f"无效的策略类型: {strategy}")
        
        # 检查标的代码
        symbols = config.get("symbols", [])
        if not isinstance(symbols, list):
            errors.append("symbols 必须是列表")
        elif len(symbols) == 0:
            errors.append("至少需要一个标的代码")
        
        # 检查日期格式
        for date_field in ["start_date", "end_date"]:
            date_value = config.get(date_field)
            if date_value:
                try:
                    datetime.strptime(date_value, "%Y-%m-%d")
                except ValueError:
                    errors.append(f"{date_field} 日期格式错误，应为 YYYY-MM-DD")
        
        # 检查数值范围
        if "initial_capital" in config:
            if config["initial_capital"] < 10000:
                errors.append("初始资金不能少于10000")
        
        return len(errors) == 0, errors
    
    def get_share_info(self, share_code: str) -> Dict[str, Any]:
        """获取分享码信息（不导入完整配置）
        
        Args:
            share_code: 分享码字符串
        
        Returns:
            分享码信息字典
        """
        try:
            config = self.import_shared(share_code)
            
            return {
                "valid": True,
                "strategy": config.get("strategy", "unknown"),
                "symbols_count": len(config.get("symbols", [])),
                "has_date_range": "start_date" in config and "end_date" in config,
                "initial_capital": config.get("initial_capital", 1000000),
            }
            
        except Exception as e:
            return {
                "valid": False,
                "error": str(e),
            }


def create_demo_share_code() -> str:
    """创建演示分享码
    
    Returns:
        演示分享码
    """
    demo_config = {
        "strategy": "dual_thrust",
        "symbols": ["600519.SH"],
        "start_date": "2023-01-01",
        "end_date": "2023-12-31",
        "initial_capital": 1000000,
        "benchmark": "000300.SH",
        "params": {
            "k1": 0.5,
            "k2": 0.5,
        }
    }
    
    sharer = StrategySharer()
    return sharer.share(demo_config)


if __name__ == "__main__":
    # 测试分享功能
    sharer = StrategySharer()
    
    # 创建测试配置
    test_config = {
        "strategy": "dual_thrust",
        "symbols": ["600519.SH", "000001.SZ"],
        "start_date": "2023-01-01",
        "end_date": "2023-12-31",
        "initial_capital": 1000000,
    }
    
    # 生成分享码
    share_code = sharer.share(test_config)
    print(f"分享码: {share_code}")
    
    # 导入分享码
    imported_config = sharer.import_shared(share_code)
    print(f"导入的配置: {imported_config}")
    
    # 验证配置
    is_valid, errors = sharer.validate_config(imported_config)
    print(f"配置有效: {is_valid}, 错误: {errors}")
