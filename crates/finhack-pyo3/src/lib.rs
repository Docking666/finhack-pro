//! FinHack Pro PyO3 Bindings
//!
//! 零拷贝 Python-Rust 桥接，提供高性能计算能力。
//! 所有函数都使用 catch_unwind 保护，防止 Rust panic 导致 Python 进程崩溃。
//!
//! 兼容 pyo3 0.24 / numpy 0.24 API（Bound API）。

use pyo3::prelude::*;
use pyo3::exceptions::{PyRuntimeError, PyValueError};
use pyo3::types::{PyDict, PyList};
use numpy::{PyReadonlyArray1};
use rayon::prelude::*;
use std::panic::{catch_unwind, AssertUnwindSafe};
use std::time::Instant;

// ============================================================================
// Panic 保护包装器
// ============================================================================

/// 将 Rust panic 转换为 Python 异常。
/// 注意：调用方需先 `?` 解开内层 Result（PyErr 传播），再交给本函数处理 panic。
fn unwrap_panic<T>(result: Result<T, Box<dyn std::any::Any + Send>>) -> PyResult<T> {
    match result {
        Ok(value) => Ok(value),
        Err(panic_info) => {
            let msg = match panic_info.downcast_ref::<&str>() {
                Some(s) => s.to_string(),
                None => match panic_info.downcast_ref::<String>() {
                    Some(s) => s.clone(),
                    None => "Unknown panic".to_string(),
                },
            };
            Err(PyRuntimeError::new_err(format!("Rust panic: {}", msg)))
        }
    }
}

/// 包装一个可能 panic 的闭包，把 Result<T, PyErr> 双层解包为 PyResult<T>
/// 闭包返回 Result<U, PyErr>：catch_unwind → Result<Result<U, PyErr>, Box<dyn Any+Send>>
/// unwrap_panic 处理 panic 层（转 PyRuntimeError），外层 `?` 再解开 PyResult 层
macro_rules! safe_call {
    ($closure:expr) => {
        unwrap_panic(catch_unwind(AssertUnwindSafe($closure)))?
    };
}

// ============================================================================
// 技术指标计算（纯 Rust 实现）
// ============================================================================

/// RSI 计算
fn calculate_rsi_impl(closes: &[f64], period: usize) -> Vec<Option<f64>> {
    let n = closes.len();
    let mut result = vec![None; n];
    if n < period + 1 {
        return result;
    }

    let mut gains = 0.0_f64;
    let mut losses = 0.0_f64;

    for i in 1..=period {
        let change = closes[i] - closes[i - 1];
        if change > 0.0 {
            gains += change;
        } else {
            losses += change.abs();
        }
    }

    let avg_gain = gains / period as f64;
    let avg_loss = losses / period as f64;

    if avg_loss == 0.0 {
        result[period] = Some(100.0);
    } else {
        let rs = avg_gain / avg_loss;
        result[period] = Some(100.0 - 100.0 / (1.0 + rs));
    }

    let mut prev_avg_gain = avg_gain;
    let mut prev_avg_loss = avg_loss;

    for i in (period + 1)..n {
        let change = closes[i] - closes[i - 1];
        let gain = if change > 0.0 { change } else { 0.0 };
        let loss = if change < 0.0 { change.abs() } else { 0.0 };

        let curr_avg_gain = (prev_avg_gain * (period as f64 - 1.0) + gain) / period as f64;
        let curr_avg_loss = (prev_avg_loss * (period as f64 - 1.0) + loss) / period as f64;

        if curr_avg_loss == 0.0 {
            result[i] = Some(100.0);
        } else {
            let rs = curr_avg_gain / curr_avg_loss;
            result[i] = Some(100.0 - 100.0 / (1.0 + rs));
        }

        prev_avg_gain = curr_avg_gain;
        prev_avg_loss = curr_avg_loss;
    }

    result
}

/// EMA 计算
fn calculate_ema_impl(closes: &[f64], period: usize) -> Vec<Option<f64>> {
    let n = closes.len();
    let mut result = vec![None; n];
    if n < period {
        return result;
    }

    let sum: f64 = closes[..period].iter().sum();
    let mut ema = sum / period as f64;
    result[period - 1] = Some(ema);

    let multiplier = 2.0 / (period as f64 + 1.0);
    for i in period..n {
        ema = (closes[i] - ema) * multiplier + ema;
        result[i] = Some(ema);
    }

    result
}

/// MACD 计算
fn calculate_macd_impl(closes: &[f64]) -> Vec<Option<f64>> {
    let ema12 = calculate_ema_impl(closes, 12);
    let ema26 = calculate_ema_impl(closes, 26);
    let n = closes.len();
    let mut macd_line = vec![None; n];

    for i in 0..n {
        if let (Some(e12), Some(e26)) = (ema12[i], ema26[i]) {
            macd_line[i] = Some(e12 - e26);
        }
    }

    let macd_values: Vec<f64> = macd_line.iter().filter_map(|&x| x).collect();
    if macd_values.len() < 9 {
        return macd_line;
    }

    let signal = calculate_ema_impl(&macd_values, 9);
    let mut result = vec![None; n];
    let mut sig_idx = 0;

    for i in 0..n {
        if macd_line[i].is_some() {
            if let Some(sv) = signal.get(sig_idx) {
                if let (Some(m), Some(s)) = (macd_line[i], sv) {
                    result[i] = Some(m - s);
                }
            }
            sig_idx += 1;
        }
    }

    result
}

/// 布林带计算
fn calculate_bollinger_impl(closes: &[f64], period: usize, std_dev: f64)
    -> (Vec<Option<f64>>, Vec<Option<f64>>, Vec<Option<f64>>)
{
    let n = closes.len();
    let mut upper = vec![None; n];
    let mut middle = vec![None; n];
    let mut lower = vec![None; n];

    if n < period {
        return (upper, middle, lower);
    }

    for i in (period - 1)..n {
        let slice = &closes[i + 1 - period..=i];
        let mean: f64 = slice.iter().sum::<f64>() / period as f64;
        let variance: f64 = slice.iter().map(|x| (x - mean).powi(2)).sum::<f64>() / period as f64;
        let sd = variance.sqrt();

        middle[i] = Some(mean);
        upper[i] = Some(mean + std_dev * sd);
        lower[i] = Some(mean - std_dev * sd);
    }

    (upper, middle, lower)
}

/// ATR 计算
fn calculate_atr_impl(highs: &[f64], lows: &[f64], closes: &[f64], period: usize) -> Vec<Option<f64>> {
    let n = closes.len();
    let mut result = vec![None; n];

    if n < 2 || n < period {
        return result;
    }

    let mut tr: Vec<f64> = Vec::with_capacity(n);
    tr.push(highs[0] - lows[0]);
    for i in 1..n {
        let tr_val = (highs[i] - lows[i])
            .max((highs[i] - closes[i - 1]).abs())
            .max((lows[i] - closes[i - 1]).abs());
        tr.push(tr_val);
    }

    let sum: f64 = tr[..period].iter().sum();
    let mut atr = sum / period as f64;
    result[period - 1] = Some(atr);

    for i in period..n {
        atr = (atr * (period as f64 - 1.0) + tr[i]) / period as f64;
        result[i] = Some(atr);
    }

    result
}

// ============================================================================
// 简单回测引擎
// ============================================================================

#[derive(Debug, Clone)]
struct BacktestResult {
    total_return: f64,
    max_drawdown: f64,
    sharpe_ratio: f64,
    total_trades: u64,
    winning_trades: u64,
    losing_trades: u64,
}

fn run_single_backtest_impl(
    closes: &[f64],
    fast_period: usize,
    slow_period: usize,
    initial_capital: f64,
) -> BacktestResult {
    let n = closes.len();
    let default_result = BacktestResult {
        total_return: 0.0,
        max_drawdown: 0.0,
        sharpe_ratio: 0.0,
        total_trades: 0,
        winning_trades: 0,
        losing_trades: 0,
    };

    if n < slow_period + 1 {
        return default_result;
    }

    let mut cash = initial_capital;
    let mut position: i64 = 0;
    let mut position_cost = 0.0;
    let mut peak = initial_capital;
    let mut max_dd = 0.0;
    let mut total_trades: u64 = 0;
    let mut winning: u64 = 0;
    let mut losing: u64 = 0;
    let mut daily_returns: Vec<f64> = Vec::new();
    let mut prev_value = initial_capital;

    let commission_rate = 0.0003_f64;
    let slippage = 0.001_f64;

    // 循环起点取 slow/fast 的最大值，避免 fast > slow 时
    // prev 窗口索引下溢（usize 减法为负 → panic）
    let loop_start = slow_period.max(fast_period);
    if loop_start >= n {
        return default_result;
    }

    for i in loop_start..n {
        let fast_ma: f64 = closes[i + 1 - fast_period..=i].iter().sum::<f64>() / fast_period as f64;
        let slow_ma: f64 = closes[i + 1 - slow_period..=i].iter().sum::<f64>() / slow_period as f64;

        let prev_fast_ma: f64 = closes[i - fast_period..i].iter().sum::<f64>() / fast_period as f64;
        let prev_slow_ma: f64 = closes[i - slow_period..i].iter().sum::<f64>() / slow_period as f64;

        // 金叉买入
        if fast_ma > slow_ma && prev_fast_ma <= prev_slow_ma && position == 0 {
            let price = closes[i] * (1.0 + slippage);
            let available = cash * 0.9;
            let vol = (available / price / 100.0).floor() as i64 * 100;
            if vol > 0 {
                let cost = vol as f64 * price;
                let comm = cost * commission_rate;
                cash -= cost + comm;
                position = vol;
                position_cost = price;
                total_trades += 1;
            }
        }
        // 死叉卖出
        else if fast_ma < slow_ma && prev_fast_ma >= prev_slow_ma && position > 0 {
            let price = closes[i] * (1.0 - slippage);
            let revenue = position as f64 * price;
            let comm = revenue * commission_rate;
            let tax = revenue * 0.001;
            let pnl = revenue - position as f64 * position_cost - comm - tax;
            cash += revenue - comm - tax;

            if pnl > 0.0 {
                winning += 1;
            } else {
                losing += 1;
            }

            position = 0;
            position_cost = 0.0;
            total_trades += 1;
        }

        let current_value = cash + position as f64 * closes[i];
        if current_value > peak {
            peak = current_value;
        }
        let dd = (peak - current_value) / peak;
        if dd > max_dd {
            max_dd = dd;
        }

        if prev_value > 0.0 {
            daily_returns.push((current_value - prev_value) / prev_value);
        }
        prev_value = current_value;
    }

    let final_value = cash + position as f64 * closes[n - 1];
    let total_return = (final_value - initial_capital) / initial_capital;

    let sharpe = if daily_returns.len() > 1 {
        let mean_r: f64 = daily_returns.iter().sum::<f64>() / daily_returns.len() as f64;
        let variance: f64 = daily_returns
            .iter()
            .map(|r| (r - mean_r).powi(2))
            .sum::<f64>()
            / (daily_returns.len() - 1) as f64;
        let std_r = variance.sqrt();
        if std_r > 0.0 {
            mean_r / std_r * 252.0_f64.sqrt()
        } else {
            0.0
        }
    } else {
        0.0
    };

    BacktestResult {
        total_return,
        max_drawdown: max_dd,
        sharpe_ratio: sharpe,
        total_trades,
        winning_trades: winning,
        losing_trades: losing,
    }
}

/// 带精度约束的回测结果
#[derive(Debug, Clone)]
struct ConstrainedBacktestResult {
    total_return: f64,
    max_drawdown: f64,
    sharpe_ratio: f64,
    total_trades: u64,
    winning_trades: u64,
    losing_trades: u64,
    rejected_trades: u64,   // 因涨跌停等约束被拒的交易数
}

/// 带 A 股精度约束的双均线回测（涨跌停）
///
/// 在撮合阶段检查涨跌停：涨停拒绝买单、跌停拒绝卖单。
/// pre_closes 为昨收价数组（与 closes 等长，首元素可填 0 表示无昨收）。
///
/// 与 Python 侧 ExecutionGate 的涨跌停逻辑保持一致：
/// limit_up = round(pre_close * (1 + limit_pct), 2)
fn run_backtest_constrained_impl(
    closes: &[f64],
    pre_closes: &[f64],
    fast_period: usize,
    slow_period: usize,
    initial_capital: f64,
    commission_rate: f64,
    slippage: f64,
    limit_pct: f64,
    enable_limit_up_down: bool,
) -> ConstrainedBacktestResult {
    let n = closes.len();
    let default_result = ConstrainedBacktestResult {
        total_return: 0.0,
        max_drawdown: 0.0,
        sharpe_ratio: 0.0,
        total_trades: 0,
        winning_trades: 0,
        losing_trades: 0,
        rejected_trades: 0,
    };

    if n < slow_period.max(fast_period) + 1 {
        return default_result;
    }

    let mut cash = initial_capital;
    let mut position: i64 = 0;
    let mut position_cost = 0.0;
    let mut peak = initial_capital;
    let mut max_dd = 0.0;
    let mut total_trades: u64 = 0;
    let mut winning: u64 = 0;
    let mut losing: u64 = 0;
    let mut rejected: u64 = 0;
    let mut daily_returns: Vec<f64> = Vec::new();
    let mut prev_value = initial_capital;

    let loop_start = slow_period.max(fast_period);
    if loop_start >= n {
        return default_result;
    }

    for i in loop_start..n {
        let fast_ma: f64 = closes[i + 1 - fast_period..=i].iter().sum::<f64>() / fast_period as f64;
        let slow_ma: f64 = closes[i + 1 - slow_period..=i].iter().sum::<f64>() / slow_period as f64;
        let prev_fast_ma: f64 = closes[i - fast_period..i].iter().sum::<f64>() / fast_period as f64;
        let prev_slow_ma: f64 = closes[i - slow_period..i].iter().sum::<f64>() / slow_period as f64;

        // 涨跌停判断（与 Python ExecutionGate 一致）
        let pre_close = if i < pre_closes.len() { pre_closes[i] } else { 0.0 };
        let mut is_limit_up = false;
        let mut is_limit_down = false;
        if enable_limit_up_down && pre_close > 0.0 {
            let limit_up = (pre_close * (1.0 + limit_pct) * 100.0).round() / 100.0;
            let limit_down = (pre_close * (1.0 - limit_pct) * 100.0).round() / 100.0;
            is_limit_up = closes[i] >= limit_up - 1e-9;
            is_limit_down = closes[i] <= limit_down + 1e-9;
        }

        // 金叉买入（涨停拒买）
        if fast_ma > slow_ma && prev_fast_ma <= prev_slow_ma && position == 0 {
            if is_limit_up {
                rejected += 1;
            } else {
                let price = closes[i] * (1.0 + slippage);
                let available = cash * 0.9;
                let vol = (available / price / 100.0).floor() as i64 * 100;
                if vol > 0 {
                    let cost = vol as f64 * price;
                    let comm = cost * commission_rate;
                    cash -= cost + comm;
                    position = vol;
                    position_cost = price;
                    total_trades += 1;
                }
            }
        }
        // 死叉卖出（跌停拒卖）
        else if fast_ma < slow_ma && prev_fast_ma >= prev_slow_ma && position > 0 {
            if is_limit_down {
                rejected += 1;
            } else {
                let price = closes[i] * (1.0 - slippage);
                let revenue = position as f64 * price;
                let comm = revenue * commission_rate;
                let tax = revenue * 0.001;
                let pnl = revenue - position as f64 * position_cost - comm - tax;
                cash += revenue - comm - tax;

                if pnl > 0.0 {
                    winning += 1;
                } else {
                    losing += 1;
                }
                position = 0;
                position_cost = 0.0;
                total_trades += 1;
            }
        }

        let current_value = cash + position as f64 * closes[i];
        if current_value > peak {
            peak = current_value;
        }
        let dd = (peak - current_value) / peak;
        if dd > max_dd {
            max_dd = dd;
        }

        if prev_value > 0.0 {
            daily_returns.push((current_value - prev_value) / prev_value);
        }
        prev_value = current_value;
    }

    let final_value = cash + position as f64 * closes[n - 1];
    let total_return = (final_value - initial_capital) / initial_capital;

    let sharpe = if daily_returns.len() > 1 {
        let mean_r: f64 = daily_returns.iter().sum::<f64>() / daily_returns.len() as f64;
        let variance: f64 = daily_returns
            .iter()
            .map(|r| (r - mean_r).powi(2))
            .sum::<f64>()
            / (daily_returns.len() - 1) as f64;
        let std_r = variance.sqrt();
        if std_r > 0.0 {
            mean_r / std_r * 252.0_f64.sqrt()
        } else {
            0.0
        }
    } else {
        0.0
    };

    ConstrainedBacktestResult {
        total_return,
        max_drawdown: max_dd,
        sharpe_ratio: sharpe,
        total_trades,
        winning_trades: winning,
        losing_trades: losing,
        rejected_trades: rejected,
    }
}

// ============================================================================
// PyO3 函数定义
// ============================================================================

/// 获取模块版本信息
#[pyfunction]
fn get_version() -> PyResult<&'static str> {
    Ok(env!("CARGO_PKG_VERSION"))
}

/// 获取 rayon 线程数
#[pyfunction]
fn get_rayon_threads() -> PyResult<usize> {
    Ok(rayon::current_num_threads())
}

/// 批量计算技术指标
///
/// Args:
///     closes: 收盘价数组
///     highs: 最高价数组（ATR 需要）
///     lows: 最低价数组（ATR 需要）
///     indicators: 需要计算的指标列表 ["rsi", "macd", "bollinger", "atr"]
///
/// Returns:
///     dict: 各指标计算结果
#[pyfunction]
#[pyo3(signature = (closes, highs=None, lows=None, indicators=None))]
fn calculate_indicators(
    py: Python<'_>,
    closes: PyReadonlyArray1<f64>,
    highs: Option<PyReadonlyArray1<f64>>,
    lows: Option<PyReadonlyArray1<f64>>,
    indicators: Option<Vec<String>>,
) -> PyResult<PyObject> {
    safe_call!(|| -> PyResult<PyObject> {
        let closes_slice = closes.as_slice().map_err(|e| PyValueError::new_err(e.to_string()))?;
        let indicators = indicators.unwrap_or_else(|| vec!["rsi".into(), "macd".into()]);
        let start = Instant::now();

        let dict = PyDict::new(py);

        let has_rsi = indicators.contains(&"rsi".to_string());
        let has_macd = indicators.contains(&"macd".to_string());
        let has_bb = indicators.contains(&"bollinger".to_string());
        let has_atr = indicators.contains(&"atr".to_string());

        // rayon 并行：两两分组。
        // 注意：PyReadonlyArray 非 Sync，必须先提取 &[f64] 切片
        // 再进入 rayon 闭包（&[f64] 是 Sync 的）。
        let (rsi_result, macd_result) = rayon::join(
            || if has_rsi { Some(calculate_rsi_impl(closes_slice, 14)) } else { None },
            || if has_macd { Some(calculate_macd_impl(closes_slice)) } else { None },
        );

        let h_slice_opt: Option<&[f64]> = highs.as_ref().and_then(|h| h.as_slice().ok());
        let l_slice_opt: Option<&[f64]> = lows.as_ref().and_then(|l| l.as_slice().ok());

        let (bb_result, atr_result) = rayon::join(
            || if has_bb { Some(calculate_bollinger_impl(closes_slice, 20, 2.0)) } else { None },
            || {
                if has_atr {
                    if let (Some(hs), Some(ls)) = (h_slice_opt, l_slice_opt) {
                        if hs.len() == closes_slice.len() && ls.len() == closes_slice.len() {
                            return Some(calculate_atr_impl(hs, ls, closes_slice, 14));
                        }
                    }
                    None
                } else {
                    None
                }
            },
        );

        if let Some(rsi) = rsi_result {
            dict.set_item("rsi", rsi)?;
        }
        if let Some(macd) = macd_result {
            dict.set_item("macd", macd)?;
        }
        if let Some((upper, middle, lower)) = bb_result {
            dict.set_item("bb_upper", upper)?;
            dict.set_item("bb_middle", middle)?;
            dict.set_item("bb_lower", lower)?;
        }
        if let Some(atr) = atr_result {
            dict.set_item("atr", atr)?;
        }

        dict.set_item("computation_time_ms", start.elapsed().as_secs_f64() * 1000.0)?;

        Ok(dict.into_any().unbind())
    })
}

/// 批量回测
///
/// Args:
///     closes: 收盘价数组
///     strategy_configs: 策略配置列表 [{"fast_period": 5, "slow_period": 20}, ...]
///     initial_capital: 初始资金
///
/// Returns:
///     dict: {"results": [...], "total_time_ms": ...}
#[pyfunction]
#[pyo3(signature = (closes, strategy_configs, initial_capital=1000000.0))]
fn batch_backtest(
    py: Python<'_>,
    closes: PyReadonlyArray1<f64>,
    strategy_configs: Vec<Py<PyDict>>,
    initial_capital: f64,
) -> PyResult<PyObject> {
    safe_call!(|| -> PyResult<PyObject> {
        let closes_slice = closes.as_slice().map_err(|e| PyValueError::new_err(e.to_string()))?;
        let start = Instant::now();

        // 解析策略配置（宽松解析：缺失字段用默认值）
        let configs: Vec<(usize, usize)> = strategy_configs
            .iter()
            .map(|cfg| {
                let dict = cfg.bind(py);
                let fast = dict
                    .get_item("fast_period")
                    .ok()
                    .flatten()
                    .and_then(|v| v.extract::<usize>().ok())
                    .unwrap_or(5);
                let slow = dict
                    .get_item("slow_period")
                    .ok()
                    .flatten()
                    .and_then(|v| v.extract::<usize>().ok())
                    .unwrap_or(20);
                (fast, slow)
            })
            .collect();

        // rayon 并行回测
        let results: Vec<BacktestResult> = configs
            .par_iter()
            .map(|&(fast, slow)| {
                run_single_backtest_impl(closes_slice, fast, slow, initial_capital)
            })
            .collect();

        // 转换为 Python 对象
        let py_results = PyList::new(py, results.iter().map(|r| {
            let dict = PyDict::new(py);
            dict.set_item("total_return", r.total_return).unwrap();
            dict.set_item("max_drawdown", r.max_drawdown).unwrap();
            dict.set_item("sharpe_ratio", r.sharpe_ratio).unwrap();
            dict.set_item("total_trades", r.total_trades).unwrap();
            dict.set_item("winning_trades", r.winning_trades).unwrap();
            dict.set_item("losing_trades", r.losing_trades).unwrap();
            dict
        }))?;

        let output = PyDict::new(py);
        output.set_item("results", py_results)?;
        output.set_item("total_time_ms", start.elapsed().as_secs_f64() * 1000.0)?;

        Ok(output.into_any().unbind())
    })
}

/// 并行信号计算（分治-聚合模式）
///
/// Args:
///     symbols_data: 多标的数据 [{"symbol": "XXX", "closes": [...]}, ...]
///     fast_period: 快线周期
///     slow_period: 慢线周期
///
/// Returns:
///     dict: {"results": [...], "total_time_ms": ...}
#[pyfunction]
#[pyo3(signature = (symbols_data, fast_period=5, slow_period=20))]
fn parallel_signal_compute(
    py: Python<'_>,
    symbols_data: Vec<Py<PyDict>>,
    fast_period: usize,
    slow_period: usize,
) -> PyResult<PyObject> {
    safe_call!(|| -> PyResult<PyObject> {
        let start = Instant::now();

        // 解析输入数据
        let parsed: Vec<(String, Vec<f64>)> = symbols_data
            .iter()
            .map(|data| {
                let dict = data.bind(py);
                let symbol = dict
                    .get_item("symbol")
                    .ok()
                    .flatten()
                    .and_then(|v| v.extract::<String>().ok())
                    .unwrap_or_else(|| "UNKNOWN".to_string());

                let closes = dict
                    .get_item("closes")
                    .ok()
                    .flatten()
                    .and_then(|v| v.extract::<Vec<f64>>().ok())
                    .unwrap_or_default();

                (symbol, closes)
            })
            .collect();

        // rayon 并行计算
        let results: Vec<(String, BacktestResult)> = parsed
            .par_iter()
            .map(|(symbol, closes)| {
                let result = run_single_backtest_impl(closes, fast_period, slow_period, 1_000_000.0);
                (symbol.clone(), result)
            })
            .collect();

        // 转换为 Python 对象
        let py_results = PyList::new(py, results.iter().map(|(symbol, r)| {
            let dict = PyDict::new(py);
            dict.set_item("symbol", symbol).unwrap();
            dict.set_item("total_return", r.total_return).unwrap();
            dict.set_item("max_drawdown", r.max_drawdown).unwrap();
            dict.set_item("sharpe_ratio", r.sharpe_ratio).unwrap();
            dict.set_item("total_trades", r.total_trades).unwrap();
            dict
        }))?;

        let output = PyDict::new(py);
        output.set_item("results", py_results)?;
        output.set_item("total_time_ms", start.elapsed().as_secs_f64() * 1000.0)?;

        Ok(output.into_any().unbind())
    })
}

/// 计算最大回撤
#[pyfunction]
fn calculate_max_drawdown(equity: PyReadonlyArray1<f64>) -> PyResult<f64> {
    safe_call!(|| -> PyResult<f64> {
        let slice = equity.as_slice().map_err(|e| PyValueError::new_err(e.to_string()))?;
        if slice.is_empty() {
            return Ok(0.0);
        }

        let mut peak = slice[0];
        let mut max_dd = 0.0;

        for &value in slice {
            if value > peak {
                peak = value;
            }
            if peak > 0.0 {
                let dd = (peak - value) / peak;
                if dd > max_dd {
                    max_dd = dd;
                }
            }
        }

        Ok(max_dd)
    })
}

/// 计算夏普比率
#[pyfunction]
fn calculate_sharpe_ratio(returns: PyReadonlyArray1<f64>, risk_free_rate: Option<f64>) -> PyResult<f64> {
    safe_call!(|| -> PyResult<f64> {
        let slice = returns.as_slice().map_err(|e| PyValueError::new_err(e.to_string()))?;
        if slice.len() < 2 {
            return Ok(0.0);
        }

        let rf = risk_free_rate.unwrap_or(0.0);
        let excess_returns: Vec<f64> = slice.iter().map(|r| r - rf).collect();

        let mean: f64 = excess_returns.iter().sum::<f64>() / excess_returns.len() as f64;
        let variance: f64 = excess_returns
            .iter()
            .map(|r| (r - mean).powi(2))
            .sum::<f64>()
            / (excess_returns.len() - 1) as f64;
        let std = variance.sqrt();

        if std == 0.0 {
            Ok(0.0)
        } else {
            Ok(mean / std * 252.0_f64.sqrt())
        }
    })
}

/// 带 A 股精度约束（涨跌停）的双均线回测
///
/// Args:
///     closes: 收盘价数组
///     pre_closes: 昨收价数组（与 closes 等长，无昨收处填 0）
///     fast_period: 快线周期
///     slow_period: 慢线周期
///     initial_capital: 初始资金
///     commission_rate: 佣金率
///     slippage: 滑点比例
///     limit_pct: 涨跌停幅度（如 0.10）
///     enable_limit_up_down: 是否启用涨跌停约束
///
/// Returns:
///     dict: 含 total_return / max_drawdown / sharpe_ratio / total_trades /
///           winning_trades / losing_trades / rejected_trades
#[pyfunction]
#[pyo3(signature = (
    closes, pre_closes, fast_period=5, slow_period=20,
    initial_capital=1000000.0, commission_rate=0.0003, slippage=0.001,
    limit_pct=0.10, enable_limit_up_down=false
))]
fn backtest_ma_constrained(
    py: Python<'_>,
    closes: PyReadonlyArray1<f64>,
    pre_closes: PyReadonlyArray1<f64>,
    fast_period: usize,
    slow_period: usize,
    initial_capital: f64,
    commission_rate: f64,
    slippage: f64,
    limit_pct: f64,
    enable_limit_up_down: bool,
) -> PyResult<PyObject> {
    safe_call!(|| -> PyResult<PyObject> {
        let closes_slice = closes.as_slice().map_err(|e| PyValueError::new_err(e.to_string()))?;
        let pre_slice = pre_closes.as_slice().map_err(|e| PyValueError::new_err(e.to_string()))?;
        let start = Instant::now();

        let result = run_backtest_constrained_impl(
            closes_slice,
            pre_slice,
            fast_period,
            slow_period,
            initial_capital,
            commission_rate,
            slippage,
            limit_pct,
            enable_limit_up_down,
        );

        let dict = PyDict::new(py);
        dict.set_item("total_return", result.total_return)?;
        dict.set_item("max_drawdown", result.max_drawdown)?;
        dict.set_item("sharpe_ratio", result.sharpe_ratio)?;
        dict.set_item("total_trades", result.total_trades)?;
        dict.set_item("winning_trades", result.winning_trades)?;
        dict.set_item("losing_trades", result.losing_trades)?;
        dict.set_item("rejected_trades", result.rejected_trades)?;
        dict.set_item("total_time_ms", start.elapsed().as_secs_f64() * 1000.0)?;

        Ok(dict.into_any().unbind())
    })
}

// ============================================================================
// 模块定义
// ============================================================================

#[pymodule]
fn finhack_pyo3(_py: Python<'_>, m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(get_version, m)?)?;
    m.add_function(wrap_pyfunction!(get_rayon_threads, m)?)?;
    m.add_function(wrap_pyfunction!(calculate_indicators, m)?)?;
    m.add_function(wrap_pyfunction!(batch_backtest, m)?)?;
    m.add_function(wrap_pyfunction!(parallel_signal_compute, m)?)?;
    m.add_function(wrap_pyfunction!(calculate_max_drawdown, m)?)?;
    m.add_function(wrap_pyfunction!(calculate_sharpe_ratio, m)?)?;
    m.add_function(wrap_pyfunction!(backtest_ma_constrained, m)?)?;

    m.add("__version__", env!("CARGO_PKG_VERSION"))?;

    Ok(())
}
