"""流水线任务注册表：状态查询 / 磁盘恢复 / 取消机制测试

覆盖 AgentService.list_pipeline_runs / get_pipeline_run / cancel_pipeline，
以及 coordinator 协作式取消（cancel_check → PipelineCancelledError → cancelled 落盘）。
"""

import asyncio
import json

import pytest

from finhack_pro.agents.coordinator import AgentCoordinator, PipelineCancelledError
from finhack_pro.webui.models import PipelineRunResult, PipelineStepResult
from finhack_pro.webui.services import AgentService


# ============================================================
# AgentService 任务注册表
# ============================================================


class TestPipelineRegistry:
    def test_cancel_pipeline_unknown_returns_false(self):
        """取消不存在的任务 → False"""
        svc = AgentService()
        assert svc.cancel_pipeline("nonexistent") is False

    @pytest.mark.asyncio
    async def test_cancel_pipeline_sets_flag_and_cancels_task(self):
        """取消运行中任务：置协作式标志 + 即时 task.cancel()"""
        svc = AgentService()
        flag = {"cancelled": False}
        task = asyncio.create_task(asyncio.sleep(60))
        svc._running_pipelines["run_1"] = {
            "result": PipelineRunResult(run_id="run_1", symbol="600519.SH", status="running"),
            "request": None,
            "task": task,
            "cancel_flag": flag,
        }
        assert svc.cancel_pipeline("run_1") is True
        assert flag["cancelled"] is True
        # task.cancel() 是异步取消：tick 事件循环后进入 cancelled 终态
        await asyncio.sleep(0)
        assert task.cancelled()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

    def test_list_pipeline_runs_empty(self, tmp_path):
        """无运行中/无历史/无磁盘检查点 → 空列表（磁盘扫描隔离到 tmp 目录）"""
        svc = AgentService()
        coord = object.__new__(AgentCoordinator)
        coord.config = {"pipeline": {"output_dir": str(tmp_path)}}
        svc.set_coordinator(coord)
        assert svc.list_pipeline_runs() == []

    def test_list_pipeline_runs_merges_memory_running_and_history(self, tmp_path):
        """合并运行中 + 已完成历史，去重按 run_id（磁盘扫描隔离到 tmp 目录）"""
        svc = AgentService()
        coord = object.__new__(AgentCoordinator)
        coord.config = {"pipeline": {"output_dir": str(tmp_path)}}
        svc.set_coordinator(coord)
        svc._running_pipelines["run_2"] = {
            "result": PipelineRunResult(
                run_id="run_2", symbol="000001.SZ", status="running",
                steps=[PipelineStepResult(step=1, agent_name="市场分析(技术面)", status="completed")],
            ),
            "request": None, "task": None, "cancel_flag": {"cancelled": False},
        }
        svc._pipeline_history.append({
            "run_id": "run_1", "symbol": "600519.SH", "status": "completed",
            "error": None, "steps_completed": 7, "steps_total": 7, "steps": [],
            "final_signal": None, "start_time": "2026-08-26T00:00:00", "end_time": None,
        })
        runs = svc.list_pipeline_runs()
        ids = {r["run_id"] for r in runs}
        assert ids == {"run_1", "run_2"}
        by_id = {r["run_id"]: r for r in runs}
        assert by_id["run_2"]["status"] == "running"
        assert by_id["run_2"]["steps_completed"] == 1

    def test_scan_disk_pipeline_runs_recovers_checkpoints(self, tmp_path):
        """磁盘检查点扫描：状态推断 + done 步骤计数（刷新/重启后恢复的关键）"""
        run_dir = tmp_path / "pipeline_abc"
        run_dir.mkdir()
        (run_dir / "pipeline_state.json").write_text(
            json.dumps({"status": "running", "terminal": None, "updated_at": 1}), encoding="utf-8")
        (run_dir / "step1.done").write_text(json.dumps({"step": 1}), encoding="utf-8")
        (run_dir / "step2.done").write_text(json.dumps({"step": 2}), encoding="utf-8")

        coord = object.__new__(AgentCoordinator)
        coord.config = {"pipeline": {"output_dir": str(tmp_path)}}
        svc = AgentService()
        svc.set_coordinator(coord)

        runs = svc._scan_disk_pipeline_runs()
        assert len(runs) == 1
        r = runs[0]
        assert r["run_id"] == "pipeline_abc"
        assert r["status"] == "running"
        assert r["steps_completed"] == 2
        assert r["steps_total"] == 7

    def test_get_pipeline_run_found_and_missing(self):
        """单任务查询：命中历史 / 未命中返回 None"""
        svc = AgentService()
        assert svc.get_pipeline_run("nope") is None
        svc._pipeline_history.append({"run_id": "run_9", "status": "completed"})
        found = svc.get_pipeline_run("run_9")
        assert found is not None
        assert found["run_id"] == "run_9"


# ============================================================
# coordinator 协作式取消
# ============================================================


def _make_light_coordinator(output_dir: str) -> AgentCoordinator:
    """轻量实例：跳过重型 __init__，仅设置取消路径所需属性"""
    from finhack_pro.utils.logger import get_logger

    coord = object.__new__(AgentCoordinator)
    coord.config = {"pipeline": {"output_dir": output_dir}}
    coord._pipeline_active = False
    coord._logger = get_logger("test_coordinator")
    coord._agents = {}
    return coord


class TestCooperativeCancel:
    @pytest.mark.asyncio
    async def test_run_step_cancel_check_raises(self, tmp_path):
        """cancel_check 已取消 → _run_step 在步骤边界抛 PipelineCancelledError，不写 done"""
        coord = _make_light_coordinator(str(tmp_path))
        run_id = "run_cancel"

        def _cancel():
            raise PipelineCancelledError("用户取消流水线")

        with pytest.raises(PipelineCancelledError):
            await coord._run_step(run_id, 1, "market_analysis", lambda: None, cancel_check=_cancel)
        assert not (tmp_path / run_id / "step1.done").exists()

    @pytest.mark.asyncio
    async def test_run_analysis_pipeline_facade_passes_cancel_and_resets(self, tmp_path):
        """门面透传 cancel_check；取消异常传播后 _pipeline_active 复位"""
        coord = _make_light_coordinator(str(tmp_path))

        async def _fake_impl(**kwargs):
            cc = kwargs.get("cancel_check")
            if cc is not None:
                cc()
            return {"symbol": kwargs.get("symbol", ""), "run_id": kwargs.get("run_id")}

        coord._run_analysis_pipeline_impl = _fake_impl

        def _cancel():
            raise PipelineCancelledError("用户取消")

        with pytest.raises(PipelineCancelledError):
            await coord.run_analysis_pipeline(
                symbol="600519.SH", run_id="run_c", resume=True, cancel_check=_cancel,
            )
        assert coord._pipeline_active is False

    @pytest.mark.asyncio
    async def test_run_analysis_pipeline_normal_completion_resets(self, tmp_path):
        """正常完成：结果返回 + _pipeline_active 复位"""
        coord = _make_light_coordinator(str(tmp_path))

        async def _fake_impl(**kwargs):
            return {"symbol": kwargs.get("symbol", "")}

        coord._run_analysis_pipeline_impl = _fake_impl
        result = await coord.run_analysis_pipeline(symbol="600519.SH", run_id="run_ok", resume=True)
        assert result["symbol"] == "600519.SH"
        assert coord._pipeline_active is False

    def test_cancel_saves_cancelled_state(self, tmp_path):
        """取消落盘：_save_pipeline_state(run_id, 'cancelled') 写入磁盘，扫描可推断"""
        coord = _make_light_coordinator(str(tmp_path))
        coord._save_pipeline_state("run_x", "cancelled")
        state_path = tmp_path / "run_x" / "pipeline_state.json"
        assert state_path.exists()
        data = json.loads(state_path.read_text(encoding="utf-8"))
        assert data["status"] == "cancelled"

        svc = AgentService()
        svc.set_coordinator(coord)
        runs = svc._scan_disk_pipeline_runs()
        assert any(r["run_id"] == "run_x" and r["status"] == "cancelled" for r in runs)


# ============================================================
# 环境指纹漂移与 resume_on_drift
# ============================================================


class TestEnvFingerprint:
    def test_fingerprint_diff_reports_drift_fields(self, tmp_path):
        """_fingerprint_diff 列出漂移字段（agent.字段: 旧值 → 新值）"""
        import finhack_pro.agents.coordinator as _mod

        coord = _make_light_coordinator(str(tmp_path))
        run_dir = tmp_path / "run_drift"
        run_dir.mkdir()

        # 保存的旧指纹：deepseek 配置
        (run_dir / "env_fingerprint.json").write_text(json.dumps({
            "version": 1,
            "agents": {
                "market_analyzer": {
                    "model": "deepseek-v4-flash", "temperature": 0.0,
                    "provider": "openai", "base_url": "https://api.deepseek.com/v1",
                },
                "news_analyst": {
                    "model": "deepseek-v4-flash", "temperature": 0.3,
                    "provider": "openai", "base_url": "https://api.deepseek.com/v1",
                },
            },
            "prompts": {},
        }), encoding="utf-8")

        # 当前配置：orcarouter + 温度 0.0（与旧指纹 model/base_url/temperature 均不同）
        current = {
            "version": 1,
            "agents": {
                "market_analyzer": {
                    "model": "orcarouter/free", "temperature": 0.0,
                    "provider": "openai", "base_url": "https://api.orcarouter.ai/v1",
                },
                "news_analyst": {
                    "model": "orcarouter/free", "temperature": 0.0,
                    "provider": "openai", "base_url": "https://api.orcarouter.ai/v1",
                },
            },
            "prompts": {},
        }

        def _fake_compute(self):
            return current

        # 用 mock 替换 _compute_env_fingerprint（避免依赖真实 agents），用后恢复
        _orig = _mod.AgentCoordinator._compute_env_fingerprint
        _mod.AgentCoordinator._compute_env_fingerprint = _fake_compute
        try:
            diff = coord._fingerprint_diff("run_drift")
        finally:
            _mod.AgentCoordinator._compute_env_fingerprint = _orig

        assert "market_analyzer.model" in diff
        assert "deepseek-v4-flash" in diff and "orcarouter/free" in diff
        assert "news_analyst.temperature" in diff

    @pytest.mark.asyncio
    async def test_run_analysis_pipeline_passes_resume_on_drift(self, tmp_path):
        """resume_on_drift 请求级参数透传到 impl"""
        coord = _make_light_coordinator(str(tmp_path))
        captured = {}

        async def _fake_impl(**kwargs):
            captured["resume_on_drift"] = kwargs.get("resume_on_drift")
            return {"symbol": kwargs.get("symbol", "")}

        coord._run_analysis_pipeline_impl = _fake_impl
        await coord.run_analysis_pipeline(symbol="600519.SH", run_id="run_r", resume=True, resume_on_drift=True)
        assert captured["resume_on_drift"] is True
        assert coord._pipeline_active is False
