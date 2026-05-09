//! FinHack Pro Python-Rust 桥接服务
//!
//! 轻量级HTTP服务，为Python端提供Rust高性能计算能力:
//! - GET  /health                    健康检查
//! - POST /bridge/indicators         批量技术指标计算 (RSI/MACD/布林带/ATR)
//! - POST /bridge/batch_backtest     批量回测 (MA交叉策略)
//! - POST /bridge/parallel_signals   并行信号计算 (分治-聚合模式)

use axum::{
    extract::Json,
    response::IntoResponse,
    routing::{get, post},
    Router,
};
use rayon::prelude::*;
use serde::{Deserialize, Serialize};
use std::time::Instant;
use tracing::info;

// ============================================================================
// 请求/响应类型
// ============================================================================

/// 通用响应
#[derive(Debug, Serialize)]
struct ApiResponse<T: Serialize> {
    code: i32,
    message: String,
    data: Option<T>,
}

impl<T: Serialize> ApiResponse<T> {
    fn ok(data: T) -> Self {
        Self {
            code: 0,
            message: "success".into(),
            data: Some(data),
        }
    }
    fn error(code: i32, msg: impl Into<String>) -> Self {
        Self {
            code,
            message: msg.into(),
            data: None,
        }
    }
}

/// 健康检查响应
#[derive(Debug, Serialize)]
struct HealthInfo {
    status: String,
    version: String,
    rust_version: String,
    rayon_threads: usize,
    timestamp: String,
}

/// 指标计算请求
#[derive(Debug, Deserialize)]
struct IndicatorsRequest {
    /// OHLCV 数据
    data: Vec<OhlcvBar>,
    /// 需要计算的指标列表
    indicators: Vec<String>,
}

/// OHLCV K线
#[derive(Debug, Deserialize, Serialize, Clone)]
struct OhlcvBar {
    open: f64,
    high: f64,
    low: f64,
    close: f64,
    volume: f64,
}

/// 指标计算结果
#[derive(Debug, Serialize)]
struct IndicatorsResult {
    rsi: Option<Vec<Option<f64>>>,
    macd: Option<Vec<Option<f64>>>,
    bb_upper: Option<Vec<Option<f64>>>,
    bb_middle: Option<Vec<Option<f64>>>,
    bb_lower: Option<Vec<Option<f64>>>,
    atr: Option<Vec<Option<f64>>>,
    computation_time_ms: f64,
}

/// 批量回测请求
#[derive(Debug, Deserialize)]
struct BatchBacktestRequest {
    /// 策略配置列表
    strategy_configs: Vec<StrategyConfig>,
    /// 行情数据
    data: Vec<OhlcvBar>,
    /// 初始资金
    initial_capital: f64,
}

/// 策略配置
#[derive(Debug, Deserialize)]
struct StrategyConfig {
    name: String,
    fast_period: usize,
    slow_period: usize,
}

/// 单个回测结果
#[derive(Debug, Serialize)]
struct BacktestOutput {
    strategy_name: String,
    total_return: f64,
    max_drawdown: f64,
    sharpe_ratio: f64,
    total_trades: u64,
    winning_trades: u64,
    losing_trades: u64,
    computation_time_ms: f64,
}

/// 批量回测响应
#[derive(Debug, Serialize)]
struct BatchBacktestResult {
    results: Vec<BacktestOutput>,
    total_time_ms: f64,
}

/// 并行信号计算请求
#[derive(Debug, Deserialize)]
struct ParallelSignalsRequest {
    /// 多标的行情数据 (每个元素代表一个标的的K线序列)
    symbols_data: Vec<SymbolData>,
    /// 策略参数
    fast_period: usize,
    slow_period: usize,
}

/// 单标的行情数据
#[derive(Debug, Deserialize)]
struct SymbolData {
    symbol: String,
    bars: Vec<OhlcvBar>,
}

/// 单标的信号计算结果
#[derive(Debug, Serialize)]
struct SignalOutput {
    symbol: String,
    total_return: f64,
    sharpe_ratio: f64,
    total_trades: u64,
    computation_time_ms: f64,
    error: Option<String>,
}

// ============================================================================
// 技术指标计算 (纯Rust实现)
// ============================================================================

/// RSI (Relative Strength Index)
fn calculate_rsi(closes: &[f64], period: usize) -> Vec<Option<f64>> {
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

/// EMA (Exponential Moving Average)
fn calculate_ema(closes: &[f64], period: usize) -> Vec<Option<f64>> {
    let n = closes.len();
    let mut result = vec![None; n];
    if n < period {
        return result;
    }

    // 初始SMA
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

/// MACD
fn calculate_macd(closes: &[f64]) -> Vec<Option<f64>> {
    let ema12 = calculate_ema(closes, 12);
    let ema26 = calculate_ema(closes, 26);
    let n = closes.len();
    let mut macd_line = vec![None; n];

    for i in 0..n {
        if let (Some(e12), Some(e26)) = (ema12[i], ema26[i]) {
            macd_line[i] = Some(e12 - e26);
        }
    }

    // Signal line (9-period EMA of MACD)
    let macd_values: Vec<f64> = macd_line.iter().filter_map(|&x| x).collect();
    if macd_values.len() < 9 {
        return macd_line;
    }

    let signal = calculate_ema(&macd_values, 9);
    let mut result = vec![None; n];
    let mut sig_idx = 0;

    for i in 0..n {
        if macd_line[i].is_some() {
            if let Some(s) = signal.get(sig_idx) {
                if let (Some(m), Some(sv)) = (macd_line[i], s) {
                    result[i] = Some(m - sv);
                }
            }
            sig_idx += 1;
        }
    }

    result
}

/// Bollinger Bands
fn calculate_bollinger(closes: &[f64], period: usize, std_dev: f64) -> (Vec<Option<f64>>, Vec<Option<f64>>, Vec<Option<f64>>) {
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

/// ATR (Average True Range)
fn calculate_atr(highs: &[f64], lows: &[f64], closes: &[f64], period: usize) -> Vec<Option<f64>> {
    let n = closes.len();
    let mut result = vec![None; n];

    if n < 2 || n < period {
        return result;
    }

    // True Range
    let mut tr: Vec<f64> = Vec::with_capacity(n);
    tr.push(highs[0] - lows[0]);
    for i in 1..n {
        let tr_val = (highs[i] - lows[i])
            .max((highs[i] - closes[i - 1]).abs())
            .max((lows[i] - closes[i - 1]).abs());
        tr.push(tr_val);
    }

    // Initial ATR = SMA of first `period` TRs
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
// 简单回测引擎 (MA交叉)
// ============================================================================

fn run_single_backtest(
    bars: &[OhlcvBar],
    fast_period: usize,
    slow_period: usize,
    initial_capital: f64,
) -> BacktestOutput {
    let start = Instant::now();
    let n = bars.len();

    if n < slow_period + 1 {
        return BacktestOutput {
            strategy_name: format!("MA_{}_{}", fast_period, slow_period),
            total_return: 0.0,
            max_drawdown: 0.0,
            sharpe_ratio: 0.0,
            total_trades: 0,
            winning_trades: 0,
            losing_trades: 0,
            computation_time_ms: start.elapsed().as_secs_f64() * 1000.0,
        };
    }

    let closes: Vec<f64> = bars.iter().map(|b| b.close).collect();
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

    for i in slow_period..n {
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

        // 更新权益
        let current_value = cash + position as f64 * closes[i];
        if current_value > peak {
            peak = current_value;
        }
        let dd = (peak - current_value) / peak;
        if dd > max_dd {
            max_dd = dd;
        }

        // 日收益率 (简化: 每个bar作为一个周期)
        if prev_value > 0.0 {
            daily_returns.push((current_value - prev_value) / prev_value);
        }
        prev_value = current_value;
    }

    let final_value = cash + position as f64 * closes[n - 1];
    let total_return = (final_value - initial_capital) / initial_capital;

    // 夏普比率
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

    BacktestOutput {
        strategy_name: format!("MA_{}_{}", fast_period, slow_period),
        total_return,
        max_drawdown: max_dd,
        sharpe_ratio: sharpe,
        total_trades,
        winning_trades: winning,
        losing_trades: losing,
        computation_time_ms: start.elapsed().as_secs_f64() * 1000.0,
    }
}

// ============================================================================
// HTTP 处理函数
// ============================================================================

async fn health_check() -> impl IntoResponse {
    Json(ApiResponse::ok(HealthInfo {
        status: "healthy".into(),
        version: env!("CARGO_PKG_VERSION").to_string(),
        rust_version: "1.95.0".into(),
        rayon_threads: rayon::current_num_threads(),
        timestamp: chrono::Utc::now().to_rfc3339(),
    }))
}

async fn calculate_indicators(Json(req): Json<IndicatorsRequest>) -> impl IntoResponse {
    let start = Instant::now();
    let closes: Vec<f64> = req.data.iter().map(|b| b.close).collect();
    let highs: Vec<f64> = req.data.iter().map(|b| b.high).collect();
    let lows: Vec<f64> = req.data.iter().map(|b| b.low).collect();

    let mut result = IndicatorsResult {
        rsi: None,
        macd: None,
        bb_upper: None,
        bb_middle: None,
        bb_lower: None,
        atr: None,
        computation_time_ms: 0.0,
    };

    // 使用rayon并行计算多个指标 (join只接受2个参数，嵌套使用)
    let indicators = req.indicators.clone();
    let (rsi, macd) = rayon::join(
        || {
            if indicators.contains(&"rsi".to_string()) {
                Some(calculate_rsi(&closes, 14))
            } else {
                None
            }
        },
        || {
            if indicators.contains(&"macd".to_string()) {
                Some(calculate_macd(&closes))
            } else {
                None
            }
        },
    );
    let (bb, atr) = rayon::join(
        || {
            if indicators.contains(&"bollinger".to_string()) {
                Some(calculate_bollinger(&closes, 20, 2.0))
            } else {
                None
            }
        },
        || {
            if indicators.contains(&"atr".to_string()) {
                Some(calculate_atr(&highs, &lows, &closes, 14))
            } else {
                None
            }
        },
    );

    result.rsi = rsi;
    result.macd = macd;
    if let Some((u, m, l)) = bb {
        result.bb_upper = Some(u);
        result.bb_middle = Some(m);
        result.bb_lower = Some(l);
    }
    result.atr = atr;
    result.computation_time_ms = start.elapsed().as_secs_f64() * 1000.0;

    Json(ApiResponse::ok(result))
}

async fn batch_backtest(Json(req): Json<BatchBacktestRequest>) -> impl IntoResponse {
    let start = Instant::now();

    // 使用rayon并行运行多个策略回测
    let results: Vec<BacktestOutput> = req
        .strategy_configs
        .par_iter()
        .map(|config| {
            run_single_backtest(
                &req.data,
                config.fast_period,
                config.slow_period,
                req.initial_capital,
            )
        })
        .collect();

    Json(ApiResponse::ok(BatchBacktestResult {
        results,
        total_time_ms: start.elapsed().as_secs_f64() * 1000.0,
    }))
}

async fn parallel_signals(Json(req): Json<ParallelSignalsRequest>) -> impl IntoResponse {
    let start = Instant::now();

    // 分治-聚合: rayon并行计算各标的信号
    let results: Vec<SignalOutput> = req
        .symbols_data
        .par_iter()
        .map(|sd| {
            let bt = run_single_backtest(
                &sd.bars,
                req.fast_period,
                req.slow_period,
                1_000_000.0,
            );
            SignalOutput {
                symbol: sd.symbol.clone(),
                total_return: bt.total_return,
                sharpe_ratio: bt.sharpe_ratio,
                total_trades: bt.total_trades,
                computation_time_ms: bt.computation_time_ms,
                error: None,
            }
        })
        .collect();

    Json(ApiResponse::ok(serde_json::json!({
        "results": results,
        "total_time_ms": start.elapsed().as_secs_f64() * 1000.0,
    })))
}

// ============================================================================
// 主函数
// ============================================================================

#[tokio::main]
async fn main() {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| "info".into()),
        )
        .init();

    let app = Router::new()
        .route("/health", get(health_check))
        .route("/bridge/indicators", post(calculate_indicators))
        .route("/bridge/batch_backtest", post(batch_backtest))
        .route("/bridge/parallel_signals", post(parallel_signals));

    let host = std::env::var("BRIDGE_HOST").unwrap_or_else(|_| "0.0.0.0".to_string());
    let port: u16 = std::env::var("BRIDGE_PORT")
        .ok()
        .and_then(|p| p.parse().ok())
        .unwrap_or(8080);

    let addr = format!("{}:{}", host, port);
    info!(addr = %addr, threads = rayon::current_num_threads(), "FinHack Bridge 服务启动中...");

    let listener = tokio::net::TcpListener::bind(&addr).await.unwrap();
    info!(addr = %addr, "FinHack Bridge 服务已启动");

    axum::serve(listener, app).await.unwrap();
}
