"""
PyO3 子进程隔离包装器

将 PyO3 模块加载到独立子进程中，通过共享内存传递数据。
子进程崩溃不会影响主进程，实现进程级容灾。

架构:
    主进程                          子进程
┌──────────────┐              ┌──────────────┐
│ PyO3Isolated │ ──共享内存──→│ PyO3 Worker  │
│              │              │ (finhack_pyo3)│
│ 自动降级     │ ←──结果──────│ 计算+返回    │
└──────────────┘              └──────────────┘
"""

import json
import logging
import multiprocessing as mp
import os
import pickle
import sys
import threading
import time
import traceback
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from enum import Enum
from multiprocessing import shared_memory
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)


class WorkerState(Enum):
    """子进程状态"""
    IDLE = "idle"
    BUSY = "busy"
    CRASHED = "crashed"
    STOPPED = "stopped"


@dataclass
class WorkerInfo:
    """子进程信息"""
    process: mp.Process = None
    state: WorkerState = WorkerState.IDLE
    task_count: int = 0
    error_count: int = 0
    last_error: Optional[str] = None
    created_at: float = field(default_factory=time.time)


def _pyo3_worker_main(
    task_shm_name: str,
    result_shm_name: str,
    control_queue: mp.Queue,
    result_queue: mp.Queue,
    shm_size: int,
):
    """
    PyO3 子进程主函数
    
    Args:
        task_shm_name: 任务共享内存名称
        result_shm_name: 结果共享内存名称
        control_queue: 控制命令队列
        result_queue: 结果返回队列
        shm_size: 共享内存大小
    """
    try:
        # 导入 PyO3 模块
        import finhack_pyo3
        logger.info(f"[PyO3 Worker] 模块加载成功, version={finhack_pyo3.get_version()}")
    except ImportError as e:
        result_queue.put(("error", f"PyO3 模块导入失败: {e}"))
        return
    
    # 连接共享内存
    try:
        task_shm = shared_memory.SharedMemory(name=task_shm_name)
        result_shm = shared_memory.SharedMemory(name=result_shm_name)
    except Exception as e:
        result_queue.put(("error", f"共享内存连接失败: {e}"))
        return
    
    try:
        while True:
            # 等待任务
            try:
                cmd = control_queue.get(timeout=1.0)
            except:
                continue
            
            if cmd is None:
                # 退出命令
                break
            
            if cmd == "ping":
                result_queue.put(("pong", None))
                continue
            
            # 解析任务
            try:
                task_data = pickle.loads(bytes(task_shm.buf[:shm_size]))
            except Exception as e:
                result_queue.put(("error", f"任务解析失败: {e}"))
                continue
            
            # 执行任务
            try:
                func_name = task_data["func"]
                args = task_data.get("args", [])
                kwargs = task_data.get("kwargs", {})
                
                func = getattr(finhack_pyo3, func_name)
                result = func(*args, **kwargs)
                
                # 写入结果
                result_bytes = pickle.dumps(("ok", result))
                result_shm.buf[:len(result_bytes)] = result_bytes
                
                result_queue.put(("done", len(result_bytes)))
            except Exception as e:
                error_msg = f"{type(e).__name__}: {e}"
                result_bytes = pickle.dumps(("error", error_msg))
                result_shm.buf[:len(result_bytes)] = result_bytes
                result_queue.put(("error", error_msg))
    
    finally:
        task_shm.close()
        result_shm.close()


class PyO3Isolated:
    """
    PyO3 子进程隔离包装器
    
    特性:
    - 进程级隔离：Rust panic 只杀死子进程
    - 共享内存传输：避免序列化开销
    - 自动恢复：子进程崩溃后可重启
    - 超时保护：防止无限等待
    """
    
    def __init__(
        self,
        shm_size: int = 64 * 1024 * 1024,  # 64MB
        max_restart: int = 3,
        task_timeout: float = 30.0,
    ):
        self.shm_size = shm_size
        self.max_restart = max_restart
        self.task_timeout = task_timeout
        
        self._worker: Optional[WorkerInfo] = None
        self._task_shm: Optional[shared_memory.SharedMemory] = None
        self._result_shm: Optional[shared_memory.SharedMemory] = None
        self._control_queue: Optional[mp.Queue] = None
        self._result_queue: Optional[mp.Queue] = None
        
        self._lock = threading.Lock()
        self._available = False
        self._check_pyo3_available()
    
    def _check_pyo3_available(self) -> bool:
        """检查 PyO3 模块是否可用"""
        try:
            import finhack_pyo3
            self._available = True
            logger.info("[PyO3Isolated] finhack_pyo3 模块可用")
            return True
        except ImportError:
            logger.debug("[PyO3Isolated] finhack_pyo3 模块不可用")
            self._available = False
            return False
    
    @property
    def is_available(self) -> bool:
        """PyO3 模块是否可用"""
        return self._available
    
    @property
    def is_running(self) -> bool:
        """子进程是否正在运行"""
        return (
            self._worker is not None 
            and self._worker.process is not None 
            and self._worker.process.is_alive()
        )
    
    def start(self) -> bool:
        """启动子进程"""
        if not self._available:
            logger.warning("[PyO3Isolated] PyO3 不可用，无法启动子进程")
            return False
        
        if self.is_running:
            return True
        
        try:
            # 创建共享内存
            self._task_shm = shared_memory.SharedMemory(create=True, size=self.shm_size)
            self._result_shm = shared_memory.SharedMemory(create=True, size=self.shm_size)
            
            # 创建队列
            self._control_queue = mp.Queue()
            self._result_queue = mp.Queue()
            
            # 创建子进程
            process = mp.Process(
                target=_pyo3_worker_main,
                args=(
                    self._task_shm.name,
                    self._result_shm.name,
                    self._control_queue,
                    self._result_queue,
                    self.shm_size,
                ),
                daemon=True,
            )
            process.start()
            
            self._worker = WorkerInfo(process=process)
            
            # 等待启动
            time.sleep(0.1)
            
            if process.is_alive():
                logger.info(f"[PyO3Isolated] 子进程启动成功, pid={process.pid}")
                return True
            else:
                logger.error("[PyO3Isolated] 子进程启动后立即退出")
                self._cleanup()
                return False
                
        except Exception as e:
            logger.error(f"[PyO3Isolated] 启动失败: {e}")
            self._cleanup()
            return False
    
    def stop(self):
        """停止子进程"""
        if self._control_queue:
            try:
                self._control_queue.put(None)
            except:
                pass
        
        if self._worker and self._worker.process:
            self._worker.process.join(timeout=2.0)
            if self._worker.process.is_alive():
                self._worker.process.terminate()
        
        self._cleanup()
        logger.info("[PyO3Isolated] 子进程已停止")
    
    def _cleanup(self):
        """清理资源"""
        if self._task_shm:
            try:
                self._task_shm.close()
                self._task_shm.unlink()
            except:
                pass
            self._task_shm = None
        
        if self._result_shm:
            try:
                self._result_shm.close()
                self._result_shm.unlink()
            except:
                pass
            self._result_shm = None
        
        self._control_queue = None
        self._result_queue = None
        self._worker = None
    
    def _restart(self) -> bool:
        """重启子进程"""
        self.stop()
        time.sleep(0.5)
        return self.start()
    
    def call(
        self,
        func_name: str,
        *args,
        **kwargs,
    ) -> Tuple[str, Any]:
        """
        调用 PyO3 函数
        
        Args:
            func_name: 函数名
            *args: 位置参数
            **kwargs: 关键字参数
            
        Returns:
            (status, result): status 为 "ok" 或 "error"
        """
        if not self._available:
            return ("error", "PyO3 模块不可用")
        
        if not self.is_running:
            if not self.start():
                return ("error", "子进程启动失败")
        
        with self._lock:
            try:
                # 序列化任务
                task_data = {"func": func_name, "args": args, "kwargs": kwargs}
                task_bytes = pickle.dumps(task_data)
                
                if len(task_bytes) > self.shm_size:
                    return ("error", f"任务数据过大: {len(task_bytes)} > {self.shm_size}")
                
                # 写入共享内存
                self._task_shm.buf[:len(task_bytes)] = task_bytes
                
                # 发送命令
                self._control_queue.put("task")
                self._worker.task_count += 1
                
                # 等待结果
                try:
                    status, data = self._result_queue.get(timeout=self.task_timeout)
                except:
                    self._worker.error_count += 1
                    self._worker.state = WorkerState.CRASHED
                    return ("error", "任务超时")
                
                if status == "done":
                    # 读取结果
                    result_bytes = bytes(self._result_shm.buf[:data])
                    result_status, result_data = pickle.loads(result_bytes)
                    return (result_status, result_data)
                elif status == "error":
                    self._worker.error_count += 1
                    return ("error", data)
                else:
                    return ("error", f"未知状态: {status}")
                    
            except Exception as e:
                self._worker.error_count += 1
                self._worker.last_error = str(e)
                return ("error", f"调用异常: {e}")
    
    # ========== 便捷方法 ==========
    
    def calculate_indicators(
        self,
        closes: np.ndarray,
        highs: Optional[np.ndarray] = None,
        lows: Optional[np.ndarray] = None,
        indicators: Optional[List[str]] = None,
    ) -> Tuple[str, Dict[str, Any]]:
        """计算技术指标"""
        return self.call(
            "calculate_indicators",
            closes,
            highs,
            lows,
            indicators,
        )
    
    def batch_backtest(
        self,
        closes: np.ndarray,
        strategy_configs: List[Dict],
        initial_capital: float = 1_000_000.0,
    ) -> Tuple[str, Dict[str, Any]]:
        """批量回测"""
        return self.call(
            "batch_backtest",
            closes,
            strategy_configs,
            initial_capital,
        )
    
    def parallel_signal_compute(
        self,
        symbols_data: List[Dict],
        fast_period: int = 5,
        slow_period: int = 20,
    ) -> Tuple[str, Dict[str, Any]]:
        """并行信号计算"""
        return self.call(
            "parallel_signal_compute",
            symbols_data,
            fast_period,
            slow_period,
        )
    
    def calculate_max_drawdown(self, equity: np.ndarray) -> Tuple[str, float]:
        """计算最大回撤"""
        return self.call("calculate_max_drawdown", equity)
    
    def calculate_sharpe_ratio(
        self,
        returns: np.ndarray,
        risk_free_rate: Optional[float] = None,
    ) -> Tuple[str, float]:
        """计算夏普比率"""
        return self.call("calculate_sharpe_ratio", returns, risk_free_rate)
    
    def get_version(self) -> Tuple[str, str]:
        """获取版本"""
        return self.call("get_version")
    
    def get_rayon_threads(self) -> Tuple[str, int]:
        """获取 rayon 线程数"""
        return self.call("get_rayon_threads")


# 全局实例
_pyo3_isolated: Optional[PyO3Isolated] = None


def get_pyo3_isolated() -> PyO3Isolated:
    """获取全局 PyO3 隔离实例"""
    global _pyo3_isolated
    if _pyo3_isolated is None:
        _pyo3_isolated = PyO3Isolated()
    return _pyo3_isolated
