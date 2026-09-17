// V3.9.17 EMA保护SL与退出距离解耦实验版
// EMA/BOTH保护SL保持信号K高低点±2USD
// EMA/BOTH TP1/TP2改用V3.9.14旧局部止损对应风险距离作为ExitUnit
// FIB_PA保持原V3.9.14/V3.9.16逻辑
// 保留：V3.7全部交易逻辑、挂单状态修复、M5专用、周末与换日风控。
// 新增：只读M5/M15/H1/H4快照、策略事件、USD高影响经济日历CSV。
// AI盯盘和每日复盘由外部Python程序完成，不参与任何交易决策。
// 部署：复制本文件到 MQL5/Experts 后使用 MetaEditor 编译。
// 仅依赖 MT5 自带标准库 <Trade/Trade.mqh>。

// ===== BEGIN INLINE: XAUUSD_M5_AI_Pullback_V3_8_AI_MONITOR.mq5 =====
#property strict
#property version "3.109"
#property description "XAUUSD V3.10.9-C33-TF1 Pure Trend Frequency / M15 Early Trend / M5 Execution"
#include "Include/XAUAI/V3109C33TF1/Strategy01Types.mqh"
#include "Include/XAUAI/V3109C33TF1/M15TrendState.mqh"
#include "Include/XAUAI/V3109C33TF1/M15BullContext.mqh"
#include "Include/XAUAI/V3109C33TF1/M15BearContext.mqh"
#include "Include/XAUAI/V3109C33TF1/M15TrendReplay.mqh"
#include "Include/XAUAI/V3109C33TF1/M15EarlyTrend.mqh"
#include "Include/XAUAI/V3109C33TF1/M5PullbackState.mqh"
#include "Include/XAUAI/V3109C33TF1/M5BearPullbackState.mqh"
#include "Include/XAUAI/V3109C33TF1/BullSignalBar.mqh"
#include "Include/XAUAI/V3109C33TF1/BearSignalBar.mqh"
#include "Include/XAUAI/V3109C33TF1/M5EMARecovery.mqh"
#include "Include/XAUAI/V3109C33TF1/M5CompressionBreak.mqh"
#include "Include/XAUAI/V3109C33TF1/M5RouteSelection.mqh"
#include "Include/XAUAI/V3109C33TF1/M5TargetStructure.mqh"
#include "Include/XAUAI/V3109C33TF1/M5StructureStop.mqh"
#include "Include/XAUAI/V3109C33TF1/Strategy01Planner.mqh"
#include "Include/XAUAI/V3109C33TF1/Strategy01SellPlanner.mqh"
#include "Include/XAUAI/V3109C33TF1/M5AsymmetricTrendPolicy.mqh"
#include "Include/XAUAI/V3109C33TF1/M5MicroCyclePolicy.mqh"
#include "Include/XAUAI/V3109C33TF1/TradeSlotPolicy.mqh"
// V3.9.20: keep V19 hybrid-SL selection + risk/protection framework, but formally adopt
// V18 frozen Legacy ExitUnit for ALL routes. TP anchor = ActualEntry, TP distance = ExitUnit.


// ===== BEGIN INLINE: Include/XAUAI/StrategyTypes.mqh =====
#ifndef XAUAI_STRATEGY_TYPES_MQH
#define XAUAI_STRATEGY_TYPES_MQH

enum ENUM_TRADE_DIRECTION
{
   DIR_NONE=-1,
   DIR_BUY=0,
   DIR_SELL=1
};

enum ENUM_SIGNAL_ROUTE
{
   SIGNAL_ROUTE_NONE=0,
   SIGNAL_ROUTE_FIB_PA=1,
   SIGNAL_ROUTE_EMA_H23=2,
   SIGNAL_ROUTE_BOTH=3,
   SIGNAL_ROUTE_STRATEGY01_H2=4
};

enum ENUM_AI_MODE
{
   AI_OFF=0,
   AI_LIVE=1,
   AI_REPLAY=2
};

enum ENUM_API_KEY_SOURCE
{
   KEY_FROM_FILE=0,
   KEY_FROM_INPUT=1
};

enum ENUM_LARK_SECRET_SOURCE
{
   LARK_SECRET_FROM_FILE=0,
   LARK_SECRET_FROM_INPUT=1
};

enum ENUM_DIVERGENCE_MODE
{
   DIVERGENCE_OFF=0,
   DIVERGENCE_RSI=1,
   DIVERGENCE_MACD=2
};

enum ENUM_EA_STATE
{
   STATE_IDLE,
   STATE_CANDIDATE_FOUND,
   STATE_WAIT_AI,
   STATE_AI_APPROVED,
   STATE_PENDING_ORDER,
   STATE_POSITION_OPEN,
   STATE_TP1_REACHED,
   STATE_RUNNER,
   STATE_LOCKED
};

enum ENUM_PENDING_DISTANCE_STATUS
{
   PENDING_DISTANCE_INVALID=0,
   PENDING_DISTANCE_READY=1,
   PENDING_DISTANCE_TOO_CLOSE=2,
   PENDING_DISTANCE_ENTRY_PASSED=3
};

enum ENUM_EXIT_MODE
{
   EXIT_MODE_UNKNOWN=0,
   EXIT_MODE_LEGACY=1,
   EXIT_MODE_STAGED=2,
   EXIT_MODE_PROTECT_ONLY=3
};

enum ENUM_EXIT_STAGE
{
   EXIT_STAGE_NONE=0,
   EXIT_STAGE_TP1=1,
   EXIT_STAGE_TP2=2,
   EXIT_STAGE_LEGACY_TP1=3
};

enum ENUM_SCAN_OUTCOME
{
   SCAN_PASS=0,
   SCAN_REJECT=1,
   SCAN_SKIP=2
};

enum ENUM_SCAN_STAGE
{
   STAGE_ENVIRONMENT=0,
   STAGE_EXPOSURE,
   STAGE_TREND,
   STAGE_STRUCTURE,
   STAGE_FIB,
   STAGE_PRICE_ACTION,
   STAGE_SIGNAL_BAR,
   STAGE_RISK_LOCK,
   STAGE_SPREAD,
   STAGE_DUPLICATE,
   STAGE_CANDIDATE,
   STAGE_AI,
   STAGE_ORDER
};

struct ScanResult
{
   datetime          bar_time;
   ENUM_TIMEFRAMES   timeframe;
   ENUM_SCAN_OUTCOME outcome;
   ENUM_SCAN_STAGE   stage;
   string            reason;
};

bool CanInitializeEnvironment(const bool is_demo,const bool is_tester,
                              const ENUM_AI_MODE ai_mode)
{
   if(is_tester) return ai_mode!=AI_LIVE;
   return is_demo;
}

bool IsSupportedSignalTimeframe(const ENUM_TIMEFRAMES timeframe)
{
   return timeframe==PERIOD_M5;
}

string SignalTimeframeLabel(const ENUM_TIMEFRAMES timeframe)
{
   if(timeframe==PERIOD_M5) return "M5";
   return "UNSUPPORTED";
}

bool CanInitializeSignalTimeframe(const ENUM_TIMEFRAMES chart_timeframe)
{
   return chart_timeframe==PERIOD_M5;
}

struct SwingPoint
{
   datetime time;
   int      bar_index;
   double   price;
   bool     is_high;
};

struct MarketSnapshot
{
   datetime time;
   double   open;
   double   high;
   double   low;
   double   close;
   long     tick_volume;
   double   ema20;
   double   atr14;
   double   rsi14;
   double   macd_hist;
};

struct StructureResult
{
   bool       valid;
   bool       buy_major_valid;
   bool       sell_major_valid;
   bool       major_structure_valid;
   bool       pullback_structure_valid;
   SwingPoint last_low;
   SwingPoint previous_low;
   SwingPoint last_high;
   SwingPoint previous_high;
   SwingPoint impulse_low;
   SwingPoint impulse_high;
   SwingPoint pullback_low;
   SwingPoint pullback_high;
   string     structure_label;
};

struct EMAImpulseResult
{
   bool   valid;
   int    first_index;
   int    third_index;
   double move;
};

struct FibResult
{
   bool   valid;
   bool   context_valid;
   bool   structure_invalidated;
   bool   optimal_zone;
   bool   invalidated;
   bool   sr_confluence;
   bool   ma_confluence;
   double swing_low;
   double swing_high;
   double impulse_atr;
   double retracement;
   double pullback_extreme;
   double pullback_depth_atr;
   string zone;
};

struct PAResult
{
   bool   valid;
   bool   pin_bar;
   bool   hammer;
   bool   engulfing;
   bool   strong_reversal_bar;
   bool   h_near_ema;
   bool   secondary_pattern_valid;
   double ema_distance_usd;
   int    h_attempt;
   string primary_pattern;
};

struct HAttemptState
{
   string   impulse_id;
   int      count;
   datetime last_attempt_time;
   int      bars_since_attempt;
   bool     rearmed;
   double   rearm_extreme;
   double   rearm_ema20;
   double   rearm_ema_distance_usd;
   bool     rearm_near_ema;
};

enum ENUM_STOP_MODE
{
   STOP_MODE_LEGACY_ONLY=0,       // 纯FIB_PA或非混合路径，仅LegacySL
   STOP_MODE_EMA_SIGNAL_EXPANDED=1, // EMA/BOTH启用更宽的SignalSL
   STOP_MODE_LEGACY_FALLBACK=2      // EMA/BOTH回退LegacySL
};

enum ENUM_STOP_FALLBACK_REASON
{
   STOP_FALLBACK_NONE=0,
   STOP_FALLBACK_SIGNAL_NOT_WIDER=1,
   STOP_FALLBACK_EXPANSION_TOO_LARGE=2,
   STOP_FALLBACK_SIGNAL_GT_MAX_SL_ATR=3,
   STOP_FALLBACK_SIGNAL_RR_TOO_LOW=4,
   STOP_FALLBACK_LEGACY_PLAN_INVALID=5
};

enum ENUM_EXIT_DISTANCE_MODE
{
   EXIT_DISTANCE_ACTUAL_RISK=0,   // V14模式：成交后 ActualEntry -> InitialSL 重算
   EXIT_DISTANCE_LEGACY_FROZEN=1  // SignalSL专用：冻结 PlannedEntry -> LegacySL 距离
};

struct EmaHybridStats
{
   int      candidates;
   int      signal_sl_used;
   int      legacy_sl_used;
   int      not_wider;
   int      expansion_too_large;
   int      gt_max_sl_atr;
   int      rr_too_low;
   int      buy_signal_sl;
   int      sell_signal_sl;
   double   expansion_sum;
   double   expansion_max;
};

struct CandidateSignal
{
   string               signal_id;
   datetime             signal_time;
   ENUM_TRADE_DIRECTION direction;
   double               signal_high;
   double               signal_low;
   double               signal_open;
   double               signal_close;
   double               ema20;
   double               atr14;
   double               rsi14;
   double               macd_hist;
   double               swing_low;
   double               swing_high;
   double               fib_retracement;
   double               three_bar_move_usd;
   double               ema_distance_usd;
   double               impulse_atr;
   double               pullback_depth_atr;
   string               fib_zone;
   ENUM_SIGNAL_ROUTE    signal_route;
   bool                 fib_path_valid;
   bool                 ema_h23_path_valid;
   string               three_bar_failure_reason;
   bool                 major_structure_valid;
   bool                 three_bar_impulse_valid;
   bool                 h_near_ema;
   bool                 secondary_pattern_valid;
   bool                 sr_confluence;
   bool                 ma_confluence;
   bool                 divergence;
   bool                 round_number_confluence;
   int                  h_attempt;
   bool                 pin_bar;
   bool                 hammer;
   bool                 engulfing;
   bool                 strong_reversal_bar;
   double               planned_entry;
   double               planned_sl;
   double               tp1;
   double               rr_to_tp1;
   double               legacy_sl;
   double               exit_unit;
   double               signal_sl;
   ENUM_STOP_MODE       stop_mode;
   double               expansion_ratio;
   ENUM_STOP_FALLBACK_REASON stop_fallback_reason;
};

struct AIDecision
{
   bool   valid;
   bool   allow_trade;
   bool   is_error;
   int    confidence;
   int    http_status;
   int    response_time_ms;
   string reason;
   string model;
   string prompt_version;
   string error_code;
};

struct PendingSignalWaitState
{
   bool            active;
   int             slot_id;
   CandidateSignal candidate;
   AIDecision      decision;
   datetime        created_at;
   datetime        expiry;
};

struct LarkTradeContext
{
   string               signal_id;
   ulong                order_ticket;
   datetime             created_at;
   ENUM_TRADE_DIRECTION direction;
   ENUM_SIGNAL_ROUTE    signal_route;
   double               planned_entry;
   double               planned_sl;
   double               rr_to_tp1;
   double               fib_retracement;
   int                  h_attempt;
   double               ema_distance_usd;
   bool                 sr_confluence;
   bool                 ma_confluence;
   bool                 pin_bar;
   bool                 hammer;
   bool                 engulfing;
   bool                 strong_reversal_bar;
   bool                 ai_allow_trade;
   bool                 ai_is_error;
   int                  ai_confidence;
   string               ai_reason;
};

struct AIConnectionResult
{
   bool   attempted;
   bool   connected;
   int    http_status;
   int    mt5_error;
   int    response_time_ms;
   string model;
   string reason;
};

struct GuardResult
{
   bool   allowed;
   string code;
   string reason;
};

struct SymbolTradeSnapshot
{
   double bid;
   double ask;
   double point;
   double tick_size;
   double tick_value_loss;
   double volume_min;
   double volume_max;
   double volume_step;
   int    stops_level;
   int    freeze_level;
   bool   trade_allowed;
};

struct TradeRuntimeState
{
   ENUM_EA_STATE         state;
   string                signal_id;
   ulong                 order_ticket;
   ulong                 position_id;
   ENUM_TRADE_DIRECTION  direction;
   ENUM_SIGNAL_ROUTE     signal_route;
   datetime              pending_created;
   datetime              pending_expiry;
   double                entry;
   double                initial_sl;
   double                tp1;
   double                initial_volume;
   double                remaining_volume;
   double                stop_loss;
   double                last_tracked_structure;
   bool                  tp1_reached;
   bool                  final_exit_requested;
   int                   state_version;
   ENUM_EXIT_MODE        exit_mode;
   double                risk_distance;
   double                legacy_sl;
   double                exit_unit;
   double                tp1_price;
   double                tp2_price;
   double                structure_target;
   double                lock_price;
   bool                  tp1_done;
   bool                  tp2_done;
   bool                  break_even_done;
   bool                  break_even_pending;
   bool                  tp2_lock_done;
   bool                  tp2_lock_pending;
   bool                  server_tp_done;
   bool                  server_tp_pending;
   bool                  execution_check_pending;
   ENUM_EXIT_STAGE       execution_stage;
   double                execution_before_volume;
   double                execution_requested_volume;
   ulong                 execution_result_order;
   ulong                 execution_result_deal;
   datetime              execution_requested_at;
   bool                  runner_stop_pending;
   double                runner_pending_structure;
   double                runner_pending_buffer;
   ENUM_STOP_MODE        stop_mode;
   double                expansion_ratio;
};

struct StageClosePlan
{
   bool   valid;
   bool   close_all;
   double close_volume;
   double expected_remaining;
   string reason;
};

struct StopValidationResult
{
   bool   valid;
   double aligned_price;
   string reason;
};

struct ManagedPositionSnapshot
{
   bool                   valid;
   ulong                  ticket;
   ulong                  identifier;
   ENUM_POSITION_TYPE     type;
   double                 volume;
   double                 entry;
   double                 sl;
   double                 tp;
};

void ResetSwingPoint(SwingPoint &value)
{
   ZeroMemory(value);
}

void ResetStructureResult(StructureResult &value)
{
   ZeroMemory(value);
}

void ResetEMAImpulseResult(EMAImpulseResult &value)
{
   ZeroMemory(value);
   value.first_index=-1;
   value.third_index=-1;
}

void ResetFibResult(FibResult &value)
{
   ZeroMemory(value);
   value.zone="INVALID";
}

void ResetPAResult(PAResult &value)
{
   ZeroMemory(value);
   value.primary_pattern="NONE";
}

void ResetHAttemptState(HAttemptState &value)
{
   ZeroMemory(value);
}

void ResetCandidate(CandidateSignal &value)
{
   ZeroMemory(value);
   // ZeroMemory leaves MQL string members in the NULL state.  Normalize them
   // so a reset candidate is observably empty and serializes consistently.
   value.signal_id="";
   value.direction=DIR_NONE;
   value.fib_zone="INVALID";
   value.signal_route=SIGNAL_ROUTE_NONE;
   value.three_bar_failure_reason="";
}

void ResetAIDecision(AIDecision &value)
{
   ZeroMemory(value);
   value.allow_trade=false;
   value.valid=false;
   value.is_error=true;
}

void ResetPendingSignalWaitState(PendingSignalWaitState &value)
{
   value.active=false;
   value.slot_id=-1;
   ResetCandidate(value.candidate);
   ResetAIDecision(value.decision);
   value.created_at=0;
   value.expiry=0;
}

void ResetAIConnectionResult(AIConnectionResult &value)
{
   ZeroMemory(value);
   value.attempted=false;
   value.connected=false;
   value.http_status=0;
   value.mt5_error=0;
}

void ResetGuardResult(GuardResult &value)
{
   ZeroMemory(value);
   value.allowed=false;
}

void ResetTradeRuntimeState(TradeRuntimeState &value)
{
   ZeroMemory(value);
   value.signal_id="";
   value.state=STATE_IDLE;
   value.direction=DIR_NONE;
   value.signal_route=SIGNAL_ROUTE_NONE;
   value.state_version=2;
   value.exit_mode=EXIT_MODE_UNKNOWN;
   value.execution_stage=EXIT_STAGE_NONE;
}

#endif
// ===== END INLINE: Include/XAUAI/StrategyTypes.mqh =====

// ===== BEGIN INLINE: Include/XAUAI/ScanDiagnostics.mqh =====
#ifndef XAUAI_SCAN_DIAGNOSTICS_MQH
#define XAUAI_SCAN_DIAGNOSTICS_MQH

// [单文件合并] 已内联，跳过重复模块: StrategyTypes.mqh

void ResetScanResult(ScanResult &scan,const ENUM_TIMEFRAMES timeframe,
                     const datetime bar_time)
{
   ZeroMemory(scan);
   scan.timeframe=timeframe;
   scan.bar_time=bar_time;
   scan.outcome=SCAN_SKIP;
   scan.stage=STAGE_ENVIRONMENT;
   scan.reason="尚未扫描";
}

void SetScanResult(ScanResult &scan,const ENUM_SCAN_OUTCOME outcome,
                   const ENUM_SCAN_STAGE stage,const string reason)
{
   scan.outcome=outcome;
   scan.stage=stage;
   scan.reason=reason;
}

string ScanOutcomeLabel(const ENUM_SCAN_OUTCOME outcome)
{
   if(outcome==SCAN_PASS) return "PASS";
   if(outcome==SCAN_REJECT) return "REJECT";
   return "SKIP";
}

string ScanStageLabel(const ENUM_SCAN_STAGE stage)
{
   switch(stage)
   {
      case STAGE_ENVIRONMENT:  return "Environment";
      case STAGE_EXPOSURE:     return "Exposure";
      case STAGE_TREND:        return "Trend";
      case STAGE_STRUCTURE:    return "Structure";
      case STAGE_FIB:          return "Fib";
      case STAGE_PRICE_ACTION: return "PriceAction";
      case STAGE_SIGNAL_BAR:   return "SignalBar";
      case STAGE_RISK_LOCK:    return "RiskLock";
      case STAGE_SPREAD:       return "Spread";
      case STAGE_DUPLICATE:    return "Duplicate";
      case STAGE_CANDIDATE:    return "Candidate";
      case STAGE_AI:           return "AI";
      case STAGE_ORDER:        return "Order";
   }
   return "Environment";
}

string FormatScanResult(const ScanResult &scan)
{
   return "[扫描] TF="+SignalTimeframeLabel(scan.timeframe)+
          " | Bar="+TimeToString(scan.bar_time,TIME_DATE|TIME_MINUTES)+
          " | "+ScanOutcomeLabel(scan.outcome)+
          " | Stage="+ScanStageLabel(scan.stage)+
          " | Reason="+scan.reason;
}

string FormatSpreadReject(const double actual_ratio,const double maximum_ratio)
{
   return "点差"+DoubleToString(actual_ratio,2)+" ATR超过上限"+
          DoubleToString(maximum_ratio,2)+" ATR";
}

#endif
// ===== END INLINE: Include/XAUAI/ScanDiagnostics.mqh =====

// ===== BEGIN INLINE: Include/XAUAI/IndicatorManager.mqh =====
#ifndef XAUAI_INDICATOR_MANAGER_MQH
#define XAUAI_INDICATOR_MANAGER_MQH

// [单文件合并] 已内联，跳过重复模块: StrategyTypes.mqh

class CIndicatorManager
{
private:
   string m_symbol;
   ENUM_TIMEFRAMES m_timeframe;
   int    m_ma_handle;
   int    m_atr_handle;
   int    m_rsi_handle;
   int    m_macd_handle;

public:
   CIndicatorManager()
   {
      m_symbol="";
      m_timeframe=PERIOD_M5;
      m_ma_handle=INVALID_HANDLE;
      m_atr_handle=INVALID_HANDLE;
      m_rsi_handle=INVALID_HANDLE;
      m_macd_handle=INVALID_HANDLE;
   }

   bool Init(const string symbol,const ENUM_TIMEFRAMES timeframe,
             const int ma_period,const int atr_period,
             const int rsi_period,const int macd_fast,const int macd_slow,
             const int macd_signal,string &error)
   {
      Release();
      error="";
      m_symbol=symbol;
      m_timeframe=timeframe;
      m_ma_handle=iMA(symbol,m_timeframe,ma_period,0,MODE_EMA,PRICE_CLOSE);
      m_atr_handle=iATR(symbol,m_timeframe,atr_period);
      m_rsi_handle=iRSI(symbol,m_timeframe,rsi_period,PRICE_CLOSE);
      m_macd_handle=iMACD(symbol,m_timeframe,macd_fast,macd_slow,macd_signal,PRICE_CLOSE);
      if(m_ma_handle==INVALID_HANDLE || m_atr_handle==INVALID_HANDLE ||
         m_rsi_handle==INVALID_HANDLE || m_macd_handle==INVALID_HANDLE)
      {
         error="指标Handle创建失败";
         Release();
         return false;
      }
      return true;
   }

   bool Load(const int bars_count,MarketSnapshot &output[],string &error)
   {
      error="";
      ArrayResize(output,0);
      if(bars_count<1 || m_ma_handle==INVALID_HANDLE || m_atr_handle==INVALID_HANDLE ||
         m_rsi_handle==INVALID_HANDLE || m_macd_handle==INVALID_HANDLE)
      {
         error="指标管理器未初始化";
         return false;
      }

      MqlRates rates[];
      double ma[],atr[],rsi[],macd_main[],macd_signal[];
      ArrayResize(rates,bars_count);
      ArrayResize(ma,bars_count);
      ArrayResize(atr,bars_count);
      ArrayResize(rsi,bars_count);
      ArrayResize(macd_main,bars_count);
      ArrayResize(macd_signal,bars_count);

      if(CopyRates(m_symbol,m_timeframe,1,bars_count,rates)!=bars_count ||
         CopyBuffer(m_ma_handle,0,1,bars_count,ma)!=bars_count ||
         CopyBuffer(m_atr_handle,0,1,bars_count,atr)!=bars_count ||
         CopyBuffer(m_rsi_handle,0,1,bars_count,rsi)!=bars_count ||
         CopyBuffer(m_macd_handle,0,1,bars_count,macd_main)!=bars_count ||
         CopyBuffer(m_macd_handle,1,1,bars_count,macd_signal)!=bars_count)
      {
         error="CopyRates或CopyBuffer失败";
         return false;
      }

      ArrayResize(output,bars_count);
      for(int i=0;i<bars_count;i++)
      {
         if(atr[i]<=0.0 || !MathIsValidNumber(atr[i]))
         {
            ArrayResize(output,0);
            error="ATR无效";
            return false;
         }
         output[i].time=rates[i].time;
         output[i].open=rates[i].open;
         output[i].high=rates[i].high;
         output[i].low=rates[i].low;
         output[i].close=rates[i].close;
         output[i].tick_volume=rates[i].tick_volume;
         output[i].ema20=ma[i];
         output[i].atr14=atr[i];
         output[i].rsi14=rsi[i];
         output[i].macd_hist=macd_main[i]-macd_signal[i];
      }
      return true;
   }

   void Release()
   {
      if(m_ma_handle!=INVALID_HANDLE) IndicatorRelease(m_ma_handle);
      if(m_atr_handle!=INVALID_HANDLE) IndicatorRelease(m_atr_handle);
      if(m_rsi_handle!=INVALID_HANDLE) IndicatorRelease(m_rsi_handle);
      if(m_macd_handle!=INVALID_HANDLE) IndicatorRelease(m_macd_handle);
      m_ma_handle=INVALID_HANDLE;
      m_atr_handle=INVALID_HANDLE;
      m_rsi_handle=INVALID_HANDLE;
      m_macd_handle=INVALID_HANDLE;
   }
};

#endif
// ===== END INLINE: Include/XAUAI/IndicatorManager.mqh =====

// ===== BEGIN INLINE: Include/XAUAI/MarketStructure.mqh =====
#ifndef XAUAI_MARKET_STRUCTURE_MQH
#define XAUAI_MARKET_STRUCTURE_MQH

// [单文件合并] 已内联，跳过重复模块: StrategyTypes.mqh

bool IsConfirmedPivotHigh(const MarketSnapshot &bars[],const int center,
                          const int left,const int right)
{
   const int count=ArraySize(bars);
   if(left<1 || right<1 || center-left<0 || center+right>=count)
      return false;
   const double price=bars[center].high;
   for(int i=1;i<=left;i++)
      if(price<=bars[center-i].high)
         return false;
   for(int i=1;i<=right;i++)
      if(price<=bars[center+i].high)
         return false;
   return true;
}

bool IsConfirmedPivotLow(const MarketSnapshot &bars[],const int center,
                         const int left,const int right)
{
   const int count=ArraySize(bars);
   if(left<1 || right<1 || center-left<0 || center+right>=count)
      return false;
   const double price=bars[center].low;
   for(int i=1;i<=left;i++)
      if(price>=bars[center-i].low)
         return false;
   for(int i=1;i<=right;i++)
      if(price>=bars[center+i].low)
         return false;
   return true;
}

void AppendSwing(SwingPoint &swings[],const MarketSnapshot &bar,
                 const int index,const bool is_high)
{
   const int size=ArraySize(swings);
   ArrayResize(swings,size+1);
   swings[size].time=bar.time;
   swings[size].bar_index=index;
   swings[size].price=(is_high ? bar.high : bar.low);
   swings[size].is_high=is_high;
}

bool ClassifyStrictSwings(const SwingPoint &swings[],const int end_index,
                          StructureResult &result)
{
   ResetStructureResult(result);
   const int count=ArraySize(swings);
   if(count<1 || end_index<0 || end_index>=count)
      return false;

   int high_count=0;
   int low_count=0;
   for(int i=end_index;i>=0 && (high_count<2 || low_count<2);i--)
   {
      if(swings[i].is_high && high_count<2)
      {
         if(high_count==0) result.last_high=swings[i];
         else result.previous_high=swings[i];
         high_count++;
      }
      else if(!swings[i].is_high && low_count<2)
      {
         if(low_count==0) result.last_low=swings[i];
         else result.previous_low=swings[i];
         low_count++;
      }
   }
   if(high_count<2 || low_count<2)
      return false;

   result.valid=true;
   result.buy_major_valid=(result.last_high.price>result.previous_high.price &&
                           result.last_low.price>result.previous_low.price);
   result.sell_major_valid=(result.last_high.price<result.previous_high.price &&
                            result.last_low.price<result.previous_low.price);
   result.major_structure_valid=(result.buy_major_valid || result.sell_major_valid);
   result.pullback_structure_valid=result.major_structure_valid;
   if(result.buy_major_valid)
   {
      result.structure_label="HH_HL";
      result.impulse_high=result.last_high;
      for(int i=end_index;i>=0;i--)
      {
         if(!swings[i].is_high && swings[i].bar_index<result.impulse_high.bar_index)
         {
            result.impulse_low=swings[i];
            break;
         }
      }
      for(int i=end_index;i>=0;i--)
      {
         if(!swings[i].is_high && swings[i].bar_index>result.impulse_high.bar_index)
         {
            result.pullback_low=swings[i];
            break;
         }
      }
   }
   else if(result.sell_major_valid)
   {
      result.structure_label="LL_LH";
      result.impulse_low=result.last_low;
      for(int i=end_index;i>=0;i--)
      {
         if(swings[i].is_high && swings[i].bar_index<result.impulse_low.bar_index)
         {
            result.impulse_high=swings[i];
            break;
         }
      }
      for(int i=end_index;i>=0;i--)
      {
         if(swings[i].is_high && swings[i].bar_index>result.impulse_low.bar_index)
         {
            result.pullback_high=swings[i];
            break;
         }
      }
   }
   else result.structure_label="MIXED";
   return true;
}

bool IsTrendProtectionBroken(const ENUM_TRADE_DIRECTION direction,
                             const MarketSnapshot &bars[],
                             const SwingPoint &protective)
{
   const int count=ArraySize(bars);
   if(direction==DIR_NONE || protective.price<=0.0 ||
      protective.bar_index<0 || protective.bar_index>=count)
      return true;

   // 只用已收盘M5的Close判断结构破坏；单纯影线刺破不取消原趋势。
   for(int i=protective.bar_index+1;i<count;i++)
   {
      if(direction==DIR_BUY && bars[i].close<protective.price)
         return true;
      if(direction==DIR_SELL && bars[i].close>protective.price)
         return true;
   }
   return false;
}

bool ApplyTrendContinuation(const SwingPoint &swings[],
                            const MarketSnapshot &bars[],
                            StructureResult &result)
{
   const int swing_count=ArraySize(swings);
   if(swing_count<1 || !result.valid || result.major_structure_valid)
      return false;

   // 当前最新Swing组合为MIXED时，向前寻找最近一次已经确认的严格趋势。
   // 只认“最近一次”严格趋势；如果它的保护点已被收盘价破坏，不回退到更老趋势。
   for(int end=swing_count-2;end>=0;end--)
   {
      StructureResult prior;
      if(!ClassifyStrictSwings(swings,end,prior))
         continue;
      if(!prior.major_structure_valid)
         continue;

      const ENUM_TRADE_DIRECTION direction=(prior.buy_major_valid ? DIR_BUY : DIR_SELL);
      SwingPoint protective;
      if(direction==DIR_BUY)
      {
         protective=prior.last_low;
         // 趋势保持期间，保护位跟随到“最近一个已确认的Swing Low”。
         // 是否失效仍只看后续M5收盘价，影线刺破本身不算破坏。
         if(result.last_low.bar_index>protective.bar_index)
            protective=result.last_low;
      }
      else
      {
         protective=prior.last_high;
         // SELL镜像：使用最近一个已确认的Swing High作为保护位。
         if(result.last_high.bar_index>protective.bar_index)
            protective=result.last_high;
      }
      if(IsTrendProtectionBroken(direction,bars,protective))
         return false;

      result.buy_major_valid=(direction==DIR_BUY);
      result.sell_major_valid=(direction==DIR_SELL);
      result.major_structure_valid=true;
      result.pullback_structure_valid=true;
      result.impulse_low=prior.impulse_low;
      result.impulse_high=prior.impulse_high;
      result.structure_label=(direction==DIR_BUY ? "HH_HL_HOLD" : "LL_LH_HOLD");

      // 保留当前最新Swing信息，同时给结构跟踪提供当前推动后的最近回调Swing。
      if(direction==DIR_BUY)
      {
         ResetSwingPoint(result.pullback_low);
         for(int i=swing_count-1;i>=0;i--)
         {
            if(!swings[i].is_high && swings[i].bar_index>result.impulse_high.bar_index)
            {
               result.pullback_low=swings[i];
               break;
            }
         }
      }
      else
      {
         ResetSwingPoint(result.pullback_high);
         for(int i=swing_count-1;i>=0;i--)
         {
            if(swings[i].is_high && swings[i].bar_index>result.impulse_low.bar_index)
            {
               result.pullback_high=swings[i];
               break;
            }
         }
      }
      return true;
   }
   return false;
}

bool ClassifySwings(const SwingPoint &swings[],const MarketSnapshot &bars[],
                    StructureResult &result)
{
   const int swing_count=ArraySize(swings);
   if(!ClassifyStrictSwings(swings,swing_count-1,result))
      return false;

   // 严格HH/HL或LL/LH仍按原逻辑直接通过；只有MIXED时才启用趋势保持。
   if(!result.major_structure_valid)
      ApplyTrendContinuation(swings,bars,result);
   return true;
}

class CMarketStructure
{
public:
   bool Analyze(const MarketSnapshot &bars[],const int pivot_left,
                const int pivot_right,StructureResult &result,string &error)
   {
      ResetStructureResult(result);
      error="";
      const int count=ArraySize(bars);
      if(count<pivot_left+pivot_right+5)
      {
         error="Swing数据不足";
         return false;
      }

      SwingPoint swings[];
      for(int i=pivot_left;i<count-pivot_right;i++)
      {
         const bool high=IsConfirmedPivotHigh(bars,i,pivot_left,pivot_right);
         const bool low=IsConfirmedPivotLow(bars,i,pivot_left,pivot_right);
         if(high) AppendSwing(swings,bars[i],i,true);
         if(low) AppendSwing(swings,bars[i],i,false);
      }
      if(!ClassifySwings(swings,bars,result))
      {
         error="确认Swing数量不足";
         return false;
      }
      return true;
   }
};

#endif
// ===== END INLINE: Include/XAUAI/MarketStructure.mqh =====

// ===== BEGIN INLINE: Include/XAUAI/EMAImpulseFilter.mqh =====
#ifndef XAUAI_EMA_IMPULSE_FILTER_MQH
#define XAUAI_EMA_IMPULSE_FILTER_MQH

// [单文件合并] 已内联，跳过重复模块: StrategyTypes.mqh

bool IsThreeBarEMAImpulse(const ENUM_TRADE_DIRECTION direction,
                          const MarketSnapshot &first,
                          const MarketSnapshot &second,
                          const MarketSnapshot &third,
                          const double minimum_move,
                          double &move,string &reason)
{
   move=0.0;
   reason="";
   if(direction==DIR_BUY)
   {
      if(first.low<=first.ema20 || second.low<=second.ema20 || third.low<=third.ema20)
      {
         reason="三根推进K线必须全部严格位于EMA20上方";
         return false;
      }
      if(second.high<=first.high || third.high<=second.high ||
         second.low<=first.low || third.low<=second.low)
      {
         reason="三根推进K线未形成严格HH、HL";
         return false;
      }
      move=third.high-first.low;
   }
   else if(direction==DIR_SELL)
   {
      if(first.high>=first.ema20 || second.high>=second.ema20 || third.high>=third.ema20)
      {
         reason="三根推进K线必须全部严格位于EMA20下方";
         return false;
      }
      if(second.high>=first.high || third.high>=second.high ||
         second.low>=first.low || third.low>=second.low)
      {
         reason="三根推进K线未形成严格LH、LL";
         return false;
      }
      move=first.high-third.low;
   }
   else
   {
      reason="三根推进方向无效";
      return false;
   }

   if(minimum_move<0.0 || move<minimum_move)
   {
      reason="三根推进总幅度不足";
      return false;
   }
   return true;
}

bool FindThreeBarEMAImpulse(const ENUM_TRADE_DIRECTION direction,
                            const MarketSnapshot &bars[],
                            const StructureResult &structure,
                            const double minimum_move,
                            EMAImpulseResult &result,string &reason)
{
   ResetEMAImpulseResult(result);
   reason="";
   const int count=ArraySize(bars);
   if(!structure.valid || !structure.major_structure_valid || count<3)
   {
      reason="当前主结构或K线数据无效";
      return false;
   }
   if((direction==DIR_BUY && !structure.buy_major_valid) ||
      (direction==DIR_SELL && !structure.sell_major_valid))
   {
      reason="当前主结构与三根推进方向不一致";
      return false;
   }

   const int start=(direction==DIR_BUY ? structure.impulse_low.bar_index
                                       : structure.impulse_high.bar_index);
   const int end=(direction==DIR_BUY ? structure.impulse_high.bar_index
                                     : structure.impulse_low.bar_index);
   if(start<0 || end>=count || start>=end || end-start+1<3)
   {
      reason="当前推进腿范围不足三根K线";
      return false;
   }

   string check_reason="";
   for(int i=start;i<=end-2;i++)
   {
      double move=0.0;
      if(IsThreeBarEMAImpulse(direction,bars[i],bars[i+1],bars[i+2],
                              minimum_move,move,check_reason))
      {
         result.valid=true;
         result.first_index=i;
         result.third_index=i+2;
         result.move=move;
      }
   }
   if(!result.valid)
   {
      reason=(check_reason=="" ? "当前推进腿未找到合格的连续三根K线" : check_reason);
      return false;
   }
   return true;
}

#endif
// ===== END INLINE: Include/XAUAI/EMAImpulseFilter.mqh =====

// ===== BEGIN INLINE: Include/XAUAI/FibonacciModule.mqh =====
#ifndef XAUAI_FIBONACCI_MODULE_MQH
#define XAUAI_FIBONACCI_MODULE_MQH

// [单文件合并] 已内联，跳过重复模块: StrategyTypes.mqh
// [单文件合并] 已内联，跳过重复模块: MarketStructure.mqh

double CalcBuyFib(const double swing_low,const double swing_high,
                  const double pullback_low)
{
   const double range=swing_high-swing_low;
   if(range<=0.0) return -1.0;
   return (swing_high-pullback_low)/range*100.0;
}

double CalcSellFib(const double swing_high,const double swing_low,
                   const double pullback_high)
{
   const double range=swing_high-swing_low;
   if(range<=0.0) return -1.0;
   return (pullback_high-swing_low)/range*100.0;
}

bool IsFibRetracementValid(const double retracement,const double minimum,
                           const double maximum)
{
   return MathIsValidNumber(retracement) && retracement>=minimum &&
          retracement<=maximum;
}

string FibZoneLabel(const double retracement)
{
   if(retracement<38.2) return "SHALLOW";
   if(retracement<45.0) return "NORMAL";
   if(retracement<=55.0) return "OPTIMAL";
   if(retracement<=61.8) return "DEEP";
   return "INVALID";
}

bool HasHistoricalSR(const MarketSnapshot &bars[],const int before_index,
                     const double target,const double tolerance)
{
   if(tolerance<0.0) return false;
   for(int i=2;i<before_index-2;i++)
   {
      if(IsConfirmedPivotHigh(bars,i,2,2) &&
         MathAbs(bars[i].high-target)<=tolerance)
         return true;
      if(IsConfirmedPivotLow(bars,i,2,2) &&
         MathAbs(bars[i].low-target)<=tolerance)
         return true;
   }
   return false;
}

class CFibonacciModule
{
public:
   bool AnalyzeContext(const ENUM_TRADE_DIRECTION direction,
                       const StructureResult &structure,
                       const MarketSnapshot &bars[],
                       const double min_impulse_atr,
                       const double fib_min,
                       const double fib_max,
                       const double invalid_buffer_atr,
                       const double sr_tolerance_atr,
                       const double ema_near_usd,
                       FibResult &result,string &error)
   {
      ResetFibResult(result);
      error="";
      const int count=ArraySize(bars);
      if(!structure.valid || !structure.major_structure_valid || count<2)
      {
         error="主要结构无效";
         return false;
      }
      const double atr=bars[count-1].atr14;
      if(atr<=0.0 || !MathIsValidNumber(atr))
      {
         error="ATR无效";
         return false;
      }

      int anchor_index=-1;
      double range=0.0;
      const int impulse_low_index=structure.impulse_low.bar_index;
      const int impulse_high_index=structure.impulse_high.bar_index;
      bool anchor_order_valid=false;
      if(direction==DIR_BUY)
      {
         result.swing_low=structure.impulse_low.price;
         result.swing_high=structure.impulse_high.price;
         anchor_index=impulse_high_index;
         anchor_order_valid=(impulse_low_index<impulse_high_index);
         range=result.swing_high-result.swing_low;
      }
      else if(direction==DIR_SELL)
      {
         result.swing_high=structure.impulse_high.price;
         result.swing_low=structure.impulse_low.price;
         anchor_index=impulse_low_index;
         anchor_order_valid=(impulse_high_index<impulse_low_index);
         range=result.swing_high-result.swing_low;
      }
      else
      {
         error="交易方向无效";
         return false;
      }

      if(!MathIsValidNumber(result.swing_low) ||
         !MathIsValidNumber(result.swing_high) ||
         !MathIsValidNumber(range) || range<=0.0 ||
         impulse_low_index<0 || impulse_low_index>=count ||
         impulse_high_index<0 || impulse_high_index>=count ||
         !anchor_order_valid || anchor_index>=count-1)
      {
         error="推动波段无效或尚无已收盘回调";
         return false;
      }
      result.impulse_atr=range/atr;
      if(result.impulse_atr<min_impulse_atr)
      {
         error="推动波段小于最小ATR倍数";
         return false;
      }

      if(direction==DIR_BUY)
      {
         result.pullback_extreme=bars[anchor_index+1].low;
         for(int i=anchor_index+2;i<count;i++)
            result.pullback_extreme=MathMin(result.pullback_extreme,bars[i].low);
         result.retracement=CalcBuyFib(result.swing_low,result.swing_high,
                                       result.pullback_extreme);
         const double invalid_level=result.swing_high-range*fib_max/100.0-
                                    invalid_buffer_atr*atr;
         result.invalidated=(result.pullback_extreme<invalid_level);
      }
      else
      {
         result.pullback_extreme=bars[anchor_index+1].high;
         for(int i=anchor_index+2;i<count;i++)
            result.pullback_extreme=MathMax(result.pullback_extreme,bars[i].high);
         result.retracement=CalcSellFib(result.swing_high,result.swing_low,
                                        result.pullback_extreme);
         const double invalid_level=result.swing_low+range*fib_max/100.0+
                                    invalid_buffer_atr*atr;
         result.invalidated=(result.pullback_extreme>invalid_level);
      }

      result.structure_invalidated=
         (direction==DIR_BUY ? result.pullback_extreme<=result.swing_low
                             : result.pullback_extreme>=result.swing_high);
      result.optimal_zone=(result.retracement>=45.0 && result.retracement<=55.0);
      result.zone=FibZoneLabel(result.retracement);
      result.pullback_depth_atr=MathAbs((direction==DIR_BUY ? result.swing_high : result.swing_low)-
                                        result.pullback_extreme)/atr;
      result.sr_confluence=HasHistoricalSR(bars,anchor_index,result.pullback_extreme,
                                           sr_tolerance_atr*atr);
      result.ma_confluence=(MathAbs(result.pullback_extreme-bars[count-1].ema20)<=
                            ema_near_usd);
      result.valid=(!result.structure_invalidated && !result.invalidated &&
                    IsFibRetracementValid(result.retracement,fib_min,fib_max));
      if(result.structure_invalidated)
      {
         error=(result.invalidated ? "Fib超过61.8%缓冲并失效" : "价格未处于有效Fib区间");
         return false;
      }
      result.context_valid=true;
      return true;
   }

   bool Analyze(const ENUM_TRADE_DIRECTION direction,
                const StructureResult &structure,const MarketSnapshot &bars[],
                const double min_impulse_atr,const double fib_min,
                const double fib_max,const double invalid_buffer_atr,
                const double sr_tolerance_atr,const double ema_near_usd,
                FibResult &result,string &error)
   {
      if(!AnalyzeContext(direction,structure,bars,min_impulse_atr,fib_min,
                         fib_max,invalid_buffer_atr,sr_tolerance_atr,
                         ema_near_usd,result,error))
         return false;
      if(!result.valid)
      {
         error=(result.invalidated ? "Fib超过61.8%缓冲并失效" : "价格未处于有效Fib区间");
         return false;
      }
      return true;
   }
};

#endif
// ===== END INLINE: Include/XAUAI/FibonacciModule.mqh =====

// ===== BEGIN INLINE: Include/XAUAI/PriceAction.mqh =====
#ifndef XAUAI_PRICE_ACTION_MQH
#define XAUAI_PRICE_ACTION_MQH

// [单文件合并] 已内联，跳过重复模块: StrategyTypes.mqh

double CandleRange(const MarketSnapshot &bar)
{
   return bar.high-bar.low;
}

double CandleBody(const MarketSnapshot &bar)
{
   return MathAbs(bar.close-bar.open);
}

bool IsBullishPinBar(const MarketSnapshot &bar,const double wick_body_ratio)
{
   const double range=CandleRange(bar);
   const double body=CandleBody(bar);
   if(range<=0.0 || body<=0.0) return false;
   const double lower_wick=MathMin(bar.open,bar.close)-bar.low;
   const double upper_wick=bar.high-MathMax(bar.open,bar.close);
   return lower_wick>=body*wick_body_ratio && lower_wick>upper_wick &&
          bar.close>=bar.low+range*0.50;
}

bool IsBearishPinBar(const MarketSnapshot &bar,const double wick_body_ratio)
{
   const double range=CandleRange(bar);
   const double body=CandleBody(bar);
   if(range<=0.0 || body<=0.0) return false;
   const double lower_wick=MathMin(bar.open,bar.close)-bar.low;
   const double upper_wick=bar.high-MathMax(bar.open,bar.close);
   return upper_wick>=body*wick_body_ratio && upper_wick>lower_wick &&
          bar.close<=bar.high-range*0.50;
}

bool IsBullishHammer(const MarketSnapshot &bar)
{
   const double range=CandleRange(bar);
   const double body=CandleBody(bar);
   if(range<=0.0 || body<=0.0) return false;
   const double lower_wick=MathMin(bar.open,bar.close)-bar.low;
   const double upper_wick=bar.high-MathMax(bar.open,bar.close);
   return lower_wick>=body*2.0 && upper_wick<=range*0.25 &&
          bar.close>=bar.low+range*0.50;
}

bool IsBearishHammer(const MarketSnapshot &bar)
{
   const double range=CandleRange(bar);
   const double body=CandleBody(bar);
   if(range<=0.0 || body<=0.0) return false;
   const double lower_wick=MathMin(bar.open,bar.close)-bar.low;
   const double upper_wick=bar.high-MathMax(bar.open,bar.close);
   return upper_wick>=body*2.0 && lower_wick<=range*0.25 &&
          bar.close<=bar.high-range*0.50;
}

bool IsBullishEngulfing(const MarketSnapshot &signal,const MarketSnapshot &previous)
{
   return previous.close<previous.open && signal.close>signal.open &&
          signal.open<=previous.close && signal.close>=previous.open;
}

bool IsBearishEngulfing(const MarketSnapshot &signal,const MarketSnapshot &previous)
{
   return previous.close>previous.open && signal.close<signal.open &&
          signal.open>=previous.close && signal.close<=previous.open;
}

bool IsStrongBullReversal(const MarketSnapshot &bar,const double body_ratio)
{
   const double range=CandleRange(bar);
   if(range<=0.0 || bar.close<=bar.open) return false;
   return CandleBody(bar)/range>=body_ratio;
}

bool IsStrongBearReversal(const MarketSnapshot &bar,const double body_ratio)
{
   const double range=CandleRange(bar);
   if(range<=0.0 || bar.close>=bar.open) return false;
   return CandleBody(bar)/range>=body_ratio;
}

void RearmAfterPullback(HAttemptState &state,const int completed_bars,
                        const double extreme,const double ema20,
                        const double max_distance_usd)
{
   if(completed_bars<1) return;
   state.bars_since_attempt+=completed_bars;
   state.rearmed=true;
   state.rearm_extreme=extreme;
   state.rearm_ema20=ema20;
   state.rearm_ema_distance_usd=MathAbs(extreme-ema20);
   state.rearm_near_ema=(state.rearm_ema_distance_usd<=max_distance_usd);
}

bool HasDirectionalPA(const PAResult &pa)
{
   return pa.pin_bar || pa.hammer || pa.engulfing || pa.strong_reversal_bar;
}

bool IsEMAH23Trigger(const PAResult &pa)
{
   return pa.h_attempt>=2 && pa.h_attempt<=3 && pa.h_near_ema;
}

int RegisterBreakoutAttempt(HAttemptState &state,const datetime bar_time,
                            const int min_separation_bars)
{
   if(state.last_attempt_time==bar_time)
      return state.count;
   if(state.count>0 && (!state.rearmed ||
      state.bars_since_attempt<min_separation_bars))
      return state.count;
   state.count=MathMin(state.count+1,3);
   state.last_attempt_time=bar_time;
   state.bars_since_attempt=0;
   state.rearmed=false;
   return state.count;
}

bool HasBullishRSIDivergence(const double price_low_1,const double price_low_2,
                             const double rsi_low_1,const double rsi_low_2,
                             const double minimum_difference)
{
   return price_low_2<=price_low_1 &&
          rsi_low_2-rsi_low_1>=minimum_difference;
}

bool HasBearishRSIDivergence(const double price_high_1,const double price_high_2,
                             const double rsi_high_1,const double rsi_high_2,
                             const double minimum_difference)
{
   return price_high_2>=price_high_1 &&
          rsi_high_1-rsi_high_2>=minimum_difference;
}

class CPriceAction
{
private:
   HAttemptState m_buy_attempt;
   HAttemptState m_sell_attempt;

public:
   CPriceAction()
   {
      ResetHAttemptState(m_buy_attempt);
      ResetHAttemptState(m_sell_attempt);
   }

   void ResetAttempts()
   {
      ResetHAttemptState(m_buy_attempt);
      ResetHAttemptState(m_sell_attempt);
   }

   void SetImpulse(const ENUM_TRADE_DIRECTION direction,const string impulse_id)
   {
      HAttemptState state=(direction==DIR_BUY ? m_buy_attempt : m_sell_attempt);
      if(state.impulse_id==impulse_id) return;
      ResetHAttemptState(state);
      state.impulse_id=impulse_id;
      if(direction==DIR_BUY) m_buy_attempt=state;
      else if(direction==DIR_SELL) m_sell_attempt=state;
   }

   PAResult Analyze(const ENUM_TRADE_DIRECTION direction,
                    const MarketSnapshot &bars[],
                    const int min_separation,const double ema_near_usd,
                    const double pin_ratio,const double strong_body)
   {
      PAResult result;
      ResetPAResult(result);
      const int count=ArraySize(bars);
      if(count<2) return result;
      const MarketSnapshot signal=bars[count-1];
      const MarketSnapshot previous=bars[count-2];
      if(direction==DIR_BUY)
      {
         if(signal.low<previous.low)
            RearmAfterPullback(m_buy_attempt,1,signal.low,signal.ema20,ema_near_usd);
         if(signal.high>previous.high)
         {
            const int attempt=RegisterBreakoutAttempt(m_buy_attempt,signal.time,min_separation);
            result.h_attempt=(m_buy_attempt.last_attempt_time==signal.time ? attempt : 0);
         }
         result.pin_bar=IsBullishPinBar(signal,pin_ratio);
         result.hammer=IsBullishHammer(signal);
         result.engulfing=IsBullishEngulfing(signal,previous);
         result.strong_reversal_bar=IsStrongBullReversal(signal,strong_body);
         result.h_near_ema=(result.h_attempt>=2 && m_buy_attempt.rearm_near_ema);
         result.ema_distance_usd=m_buy_attempt.rearm_ema_distance_usd;
      }
      else if(direction==DIR_SELL)
      {
         if(signal.high>previous.high)
            RearmAfterPullback(m_sell_attempt,1,signal.high,signal.ema20,ema_near_usd);
         if(signal.low<previous.low)
         {
            const int attempt=RegisterBreakoutAttempt(m_sell_attempt,signal.time,min_separation);
            result.h_attempt=(m_sell_attempt.last_attempt_time==signal.time ? attempt : 0);
         }
         result.pin_bar=IsBearishPinBar(signal,pin_ratio);
         result.hammer=IsBearishHammer(signal);
         result.engulfing=IsBearishEngulfing(signal,previous);
         result.strong_reversal_bar=IsStrongBearReversal(signal,strong_body);
         result.h_near_ema=(result.h_attempt>=2 && m_sell_attempt.rearm_near_ema);
         result.ema_distance_usd=m_sell_attempt.rearm_ema_distance_usd;
      }
      result.secondary_pattern_valid=HasDirectionalPA(result);
      result.valid=(result.secondary_pattern_valid || IsEMAH23Trigger(result));
      if(result.h_attempt>=3) result.primary_pattern="H3";
      else if(result.h_attempt==2) result.primary_pattern="H2";
      else if(result.engulfing) result.primary_pattern="ENGULFING";
      else if(result.pin_bar) result.primary_pattern="PINBAR";
      else if(result.hammer) result.primary_pattern="HAMMER";
      else if(result.strong_reversal_bar) result.primary_pattern="STRONGBAR";
      return result;
   }
};

#endif
// ===== END INLINE: Include/XAUAI/PriceAction.mqh =====

// ===== BEGIN INLINE: Include/XAUAI/SignalEngine.mqh =====
#ifndef XAUAI_SIGNAL_ENGINE_MQH
#define XAUAI_SIGNAL_ENGINE_MQH

// [单文件合并] 已内联，跳过重复模块: StrategyTypes.mqh
// [单文件合并] 已内联，跳过重复模块: PriceAction.mqh

string DirectionLabel(const ENUM_TRADE_DIRECTION direction)
{
   if(direction==DIR_BUY) return "BUY";
   if(direction==DIR_SELL) return "SELL";
   return "NONE";
}

ENUM_SIGNAL_ROUTE ClassifySignalRoute(const bool fib_valid,
                                       const bool directional_pa_valid,
                                       const bool ema_h23_valid)
{
   const bool path_a=(fib_valid && directional_pa_valid);
   if(path_a && ema_h23_valid) return SIGNAL_ROUTE_BOTH;
   if(path_a) return SIGNAL_ROUTE_FIB_PA;
   if(ema_h23_valid) return SIGNAL_ROUTE_EMA_H23;
   return SIGNAL_ROUTE_NONE;
}

bool IsPendingRouteValid(const ENUM_SIGNAL_ROUTE route,
                         const bool direction_ok,
                         const bool context_ok,
                         const bool fib_valid,
                         const bool spread_ok)
{
   if(!direction_ok || !context_ok || !spread_ok) return false;
   if(route==SIGNAL_ROUTE_FIB_PA) return fib_valid;
   if(route==SIGNAL_ROUTE_EMA_H23 || route==SIGNAL_ROUTE_BOTH) return true;
   return false;
}

string SignalRouteLabel(const ENUM_SIGNAL_ROUTE route,
                         const ENUM_TRADE_DIRECTION direction)
{
   if(route==SIGNAL_ROUTE_FIB_PA) return "FIB_PA";
   if(route==SIGNAL_ROUTE_EMA_H23)
      return direction==DIR_SELL ? "EMA_L23" : "EMA_H23";
   if(route==SIGNAL_ROUTE_BOTH) return "BOTH";
   if(route==SIGNAL_ROUTE_STRATEGY01_H2) return "STRATEGY01_H2";
   return "NONE";
}

string StopModeLabel(const ENUM_STOP_MODE mode)
{
   if(mode==STOP_MODE_EMA_SIGNAL_EXPANDED) return "EMA_SIGNAL_EXPANDED";
   if(mode==STOP_MODE_LEGACY_FALLBACK) return "LEGACY_FALLBACK";
   return "LEGACY_ONLY";
}

string ExitDistanceModeLabel(const ENUM_EXIT_DISTANCE_MODE mode)
{
   if(mode==EXIT_DISTANCE_LEGACY_FROZEN) return "LEGACY_FROZEN";
   return "ACTUAL_ENTRY_RISK";
}

// V20：阶段出场统一采用冻结 Legacy ExitUnit（所有路线），不再按 StopMode 区分。
ENUM_EXIT_DISTANCE_MODE ExitDistanceModeFor(const ENUM_STOP_MODE mode)
{
   return EXIT_DISTANCE_LEGACY_FROZEN;
}

string StopFallbackReasonLabel(const ENUM_STOP_FALLBACK_REASON reason)
{
   if(reason==STOP_FALLBACK_SIGNAL_NOT_WIDER) return "SIGNAL_NOT_WIDER";
   if(reason==STOP_FALLBACK_EXPANSION_TOO_LARGE) return "EXPANSION_TOO_LARGE";
   if(reason==STOP_FALLBACK_SIGNAL_GT_MAX_SL_ATR) return "SIGNAL_GT_MAX_SL_ATR";
   if(reason==STOP_FALLBACK_SIGNAL_RR_TOO_LOW) return "SIGNAL_RR_TOO_LOW";
   if(reason==STOP_FALLBACK_LEGACY_PLAN_INVALID) return "LEGACY_PLAN_INVALID";
   return "NONE";
}

bool IsSignalBarRangeValid(const double range_usd,const double maximum_usd)
{
   return maximum_usd>0.0 && range_usd>=0.0 && range_usd<maximum_usd;
}

string PrimaryPattern(const PAResult &pa,const ENUM_TRADE_DIRECTION direction)
{
   if(pa.h_attempt>=3) return direction==DIR_SELL ? "L3" : "H3";
   if(pa.h_attempt==2) return direction==DIR_SELL ? "L2" : "H2";
   if(pa.engulfing) return "ENGULFING";
   if(pa.pin_bar) return "PINBAR";
   if(pa.hammer) return "HAMMER";
   if(pa.strong_reversal_bar) return "STRONGBAR";
   return "NONE";
}

string BuildSignalId(const string symbol,const datetime signal_time,
                     const ENUM_TIMEFRAMES timeframe,
                     const ENUM_TRADE_DIRECTION direction,const PAResult &pa,
                     const ENUM_SIGNAL_ROUTE route)
{
   MqlDateTime parts;
   TimeToStruct(signal_time,parts);
   return StringFormat("%s_%s_%04d%02d%02d_%02d%02d%02d_%s_%s_%s",
                       symbol,SignalTimeframeLabel(timeframe),parts.year,parts.mon,
                       parts.day,parts.hour,parts.min,parts.sec,
                       DirectionLabel(direction),PrimaryPattern(pa,direction),
                       SignalRouteLabel(route,direction));
}

const int STOP_LOCAL_LOOKBACK_BARS=5;

double RecentLowestLow(const MarketSnapshot &bars[],const int lookback)
{
   const int count=ArraySize(bars);
   if(count<=0 || lookback<=0) return 0.0;
   const int start=(count>lookback ? count-lookback : 0);
   double value=bars[start].low;
   for(int i=start+1;i<count;i++)
      if(bars[i].low<value) value=bars[i].low;
   return value;
}

double RecentHighestHigh(const MarketSnapshot &bars[],const int lookback)
{
   const int count=ArraySize(bars);
   if(count<=0 || lookback<=0) return 0.0;
   const int start=(count>lookback ? count-lookback : 0);
   double value=bars[start].high;
   for(int i=start+1;i<count;i++)
      if(bars[i].high>value) value=bars[i].high;
   return value;
}

bool TryCalculateDirectionalTargetRR(const ENUM_TRADE_DIRECTION direction,
                                     const double entry,const double target,
                                     const double risk,double &rr)
{
   rr=0.0;
   if(entry<=0.0 || target<=0.0 || risk<=0.0 ||
      !MathIsValidNumber(entry) || !MathIsValidNumber(target) ||
      !MathIsValidNumber(risk)) return false;

   double reward=0.0;
   if(direction==DIR_BUY) reward=target-entry;
   else if(direction==DIR_SELL) reward=entry-target;
   else return false;

   if(reward<=0.0 || !MathIsValidNumber(reward)) return false;
   rr=reward/risk;
   return rr>0.0 && MathIsValidNumber(rr);
}

class CProcessedSignalSet
{
private:
   string m_ids[];
public:
   bool Contains(const string id) const
   {
      for(int i=0;i<ArraySize(m_ids);i++)
         if(m_ids[i]==id) return true;
      return false;
   }
   bool Add(const string id)
   {
      if(id=="" || Contains(id)) return false;
      const int size=ArraySize(m_ids);
      ArrayResize(m_ids,size+1);
      m_ids[size]=id;
      return true;
   }
};

class CSignalEngine
{
public:
   bool Evaluate(const string symbol,const ENUM_TIMEFRAMES timeframe,
                 const ENUM_TRADE_DIRECTION direction,
                 const MarketSnapshot &bars[],const StructureResult &structure,
                 const FibResult &fib,const EMAImpulseResult &impulse,
                 const string &three_bar_reason,const PAResult &pa,
                 const double point,
                 const double entry_buffer_points,const double entry_buffer_atr,
                 const double stop_buffer_points,const double stop_buffer_atr,
                  const double max_sl_atr,const double min_rr,
                  const double max_signal_bar_usd,
                  const double ema_signal_bar_stop_usd,
                  CandidateSignal &candidate,string &reject_reason,
                 ENUM_SCAN_STAGE &reject_stage)
   {
      ResetCandidate(candidate);
      reject_reason="";
      reject_stage=STAGE_CANDIDATE;
      const int count=ArraySize(bars);
      if(count<1 || direction==DIR_NONE)
      {
         reject_reason="指标数据不足";
         reject_stage=STAGE_ENVIRONMENT;
         return false;
      }
      const MarketSnapshot signal=bars[count-1];
      if(signal.atr14<=0.0 || point<=0.0)
      {
         reject_reason="ATR或Point无效";
         reject_stage=STAGE_ENVIRONMENT;
         return false;
      }
      const bool structure_ok=(direction==DIR_BUY ? structure.buy_major_valid :
                                                     structure.sell_major_valid);
      if(!structure_ok || !structure.major_structure_valid)
      {
         reject_reason="主要结构不合格";
         reject_stage=STAGE_STRUCTURE;
         return false;
      }
      if(!fib.context_valid || fib.structure_invalidated)
      {
         reject_reason=(fib.structure_invalidated ?
                        "回调已破坏推动结构" : "Fib结构上下文无效");
         reject_stage=STAGE_STRUCTURE;
         return false;
      }

      const bool directional_pa_valid=HasDirectionalPA(pa);
      const bool path_a=(fib.valid && directional_pa_valid);
      const bool path_b=IsEMAH23Trigger(pa);
      const ENUM_SIGNAL_ROUTE route=
         ClassifySignalRoute(fib.valid,directional_pa_valid,path_b);
      if(route==SIGNAL_ROUTE_NONE)
      {
         const string path_a_reason=(!fib.valid ?
                                     "Fib不在38.2-61.8" : "缺少方向性PA");
         string path_b_reason="";
         if(pa.h_attempt!=2 && pa.h_attempt!=3)
            path_b_reason=(direction==DIR_SELL ?
                           "未形成L2/L3" : "未形成H2/H3");
         else if(!pa.h_near_ema)
            path_b_reason="回调不在EMA20±3美元";
         reject_reason="A: "+path_a_reason+" | B: "+path_b_reason;
         reject_stage=STAGE_PRICE_ACTION;
         return false;
      }
      const double signal_range=signal.high-signal.low;
      if(!IsSignalBarRangeValid(signal_range,max_signal_bar_usd))
      {
         reject_reason="信号K线长度"+DoubleToString(signal_range,2)+
                       "美元，必须严格小于"+DoubleToString(max_signal_bar_usd,2)+"美元";
         reject_stage=STAGE_SIGNAL_BAR;
         return false;
      }

      const double entry_buffer=MathMax(entry_buffer_points*point,
                                         signal.atr14*entry_buffer_atr);
      const double stop_buffer=MathMax(stop_buffer_points*point,
                                        signal.atr14*stop_buffer_atr);
      double stop_anchor=0.0;
      double legacy_sl=0.0;
      double signal_sl=0.0;
      if(direction==DIR_BUY)
      {
         candidate.planned_entry=signal.high+entry_buffer;
         stop_anchor=RecentLowestLow(bars,STOP_LOCAL_LOOKBACK_BARS);
         legacy_sl=stop_anchor-stop_buffer;
         signal_sl=(path_b ? signal.low-ema_signal_bar_stop_usd : 0.0);
         candidate.tp1=fib.swing_high;
      }
      else
      {
         candidate.planned_entry=signal.low-entry_buffer;
         stop_anchor=RecentHighestHigh(bars,STOP_LOCAL_LOOKBACK_BARS);
         legacy_sl=stop_anchor+stop_buffer;
         signal_sl=(path_b ? signal.high+ema_signal_bar_stop_usd : 0.0);
         candidate.tp1=fib.swing_low;
      }
      candidate.legacy_sl=legacy_sl;
      candidate.signal_sl=signal_sl;
      candidate.exit_unit=MathAbs(candidate.planned_entry-legacy_sl);

      // V3.9.18：先按14版 Legacy 计划验证候选是否本来就合格。
      // 若 LegacySL 本身都无法通过2ATR / 0.8R，按原规则拒绝，禁止用SignalSL绕过。
      const double legacy_risk=MathAbs(candidate.planned_entry-legacy_sl);
      double legacy_rr=0.0;
      const bool legacy_rr_ok=TryCalculateDirectionalTargetRR(direction,candidate.planned_entry,
                                                               candidate.tp1,legacy_risk,legacy_rr);
      if(legacy_risk<=0.0 || legacy_risk>max_sl_atr*signal.atr14)
      {
         reject_reason="SL距离无效或超过ATR上限";
         reject_stage=STAGE_CANDIDATE;
         ResetCandidate(candidate);
         return false;
      }
      if(!legacy_rr_ok || legacy_rr<min_rr)
      {
         reject_reason="TP1盈亏比"+DoubleToString(legacy_rr,2)+
                       "不足，要求>="+DoubleToString(min_rr,2);
         reject_stage=STAGE_CANDIDATE;
         ResetCandidate(candidate);
         return false;
      }

      // 默认先用LegacySL；EMA/BOTH是否启用更宽的SignalSL，留待价格对齐后最终决定。
      candidate.planned_sl=legacy_sl;
      candidate.rr_to_tp1=legacy_rr;
      candidate.stop_mode=STOP_MODE_LEGACY_ONLY;
      candidate.expansion_ratio=0.0;
      candidate.stop_fallback_reason=STOP_FALLBACK_NONE;

      candidate.signal_time=signal.time;
      candidate.direction=direction;
      candidate.signal_open=signal.open;
      candidate.signal_high=signal.high;
      candidate.signal_low=signal.low;
      candidate.signal_close=signal.close;
      candidate.ema20=signal.ema20;
      candidate.atr14=signal.atr14;
      candidate.rsi14=signal.rsi14;
      candidate.macd_hist=signal.macd_hist;
      candidate.swing_low=fib.swing_low;
      candidate.swing_high=fib.swing_high;
      candidate.fib_retracement=fib.retracement;
      candidate.three_bar_move_usd=impulse.move;
      candidate.ema_distance_usd=pa.ema_distance_usd;
      candidate.impulse_atr=fib.impulse_atr;
      candidate.pullback_depth_atr=fib.pullback_depth_atr;
      candidate.fib_zone=fib.zone;
      candidate.signal_route=route;
      candidate.fib_path_valid=path_a;
      candidate.ema_h23_path_valid=path_b;
      candidate.three_bar_failure_reason=(impulse.valid ? "" : three_bar_reason);
      candidate.major_structure_valid=true;
      candidate.three_bar_impulse_valid=impulse.valid;
      candidate.h_near_ema=pa.h_near_ema;
      candidate.secondary_pattern_valid=pa.secondary_pattern_valid;
      candidate.sr_confluence=fib.sr_confluence;
      candidate.ma_confluence=fib.ma_confluence;
      candidate.divergence=false;
      candidate.round_number_confluence=false;
      candidate.h_attempt=pa.h_attempt;
      candidate.pin_bar=pa.pin_bar;
      candidate.hammer=pa.hammer;
      candidate.engulfing=pa.engulfing;
      candidate.strong_reversal_bar=pa.strong_reversal_bar;
      candidate.signal_id=BuildSignalId(symbol,signal.time,timeframe,direction,
                                        pa,route);
      return true;
   }
};

#endif
// ===== END INLINE: Include/XAUAI/SignalEngine.mqh =====

// ===== BEGIN INLINE: Include/XAUAI/DeepSeekClient.mqh =====
#ifndef XAUAI_DEEPSEEK_CLIENT_MQH
#define XAUAI_DEEPSEEK_CLIENT_MQH

// [单文件合并] 已内联，跳过重复模块: StrategyTypes.mqh

// ===== BEGIN INLINE: Include/XAUAI/JsonLite.mqh =====
#ifndef XAUAI_JSON_LITE_MQH
#define XAUAI_JSON_LITE_MQH

// [单文件合并] 已内联，跳过重复模块: StrategyTypes.mqh

void JsonSkipWhitespace(const string text,int &position)
{
   const int length=StringLen(text);
   while(position<length)
   {
      const ushort c=(ushort)StringGetCharacter(text,position);
      if(c!=32 && c!=9 && c!=10 && c!=13) break;
      position++;
   }
}

int JsonHexDigit(const ushort c)
{
   if(c>='0' && c<='9') return (int)(c-'0');
   if(c>='a' && c<='f') return (int)(c-'a'+10);
   if(c>='A' && c<='F') return (int)(c-'A'+10);
   return -1;
}

bool JsonParseString(const string text,int &position,string &value,string &error)
{
   value="";
   const int length=StringLen(text);
   JsonSkipWhitespace(text,position);
   if(position>=length || StringGetCharacter(text,position)!=34)
   {
      error="JSON字符串缺少起始引号";
      return false;
   }
   position++;
   while(position<length)
   {
      const ushort c=(ushort)StringGetCharacter(text,position++);
      if(c==34) return true;
      if(c<32)
      {
         error="JSON字符串包含未转义控制字符";
         return false;
      }
      if(c!=92)
      {
         value+=ShortToString(c);
         continue;
      }
      if(position>=length)
      {
         error="JSON转义不完整";
         return false;
      }
      const ushort escaped=(ushort)StringGetCharacter(text,position++);
      if(escaped==34 || escaped==92 || escaped==47) value+=ShortToString(escaped);
      else if(escaped=='b') value+=ShortToString(8);
      else if(escaped=='f') value+=ShortToString(12);
      else if(escaped=='n') value+="\n";
      else if(escaped=='r') value+="\r";
      else if(escaped=='t') value+="\t";
      else if(escaped=='u')
      {
         if(position+4>length)
         {
            error="JSON Unicode转义不完整";
            return false;
         }
         int code=0;
         for(int i=0;i<4;i++)
         {
            const int digit=JsonHexDigit((ushort)StringGetCharacter(text,position+i));
            if(digit<0)
            {
               error="JSON Unicode转义非法";
               return false;
            }
            code=code*16+digit;
         }
         position+=4;
         value+=ShortToString((ushort)code);
      }
      else
      {
         error="JSON包含未知转义";
         return false;
      }
   }
   error="JSON字符串未结束";
   return false;
}

bool JsonParseBool(const string text,int &position,bool &value,string &error)
{
   JsonSkipWhitespace(text,position);
   if(StringSubstr(text,position,4)=="true")
   {
      value=true; position+=4; return true;
   }
   if(StringSubstr(text,position,5)=="false")
   {
      value=false; position+=5; return true;
   }
   error="JSON布尔值类型错误";
   return false;
}

bool JsonParseInteger(const string text,int &position,int &value,string &error)
{
   JsonSkipWhitespace(text,position);
   const int start=position;
   const int length=StringLen(text);
   if(position<length && StringGetCharacter(text,position)=='-') position++;
   const int digit_start=position;
   while(position<length)
   {
      const ushort c=(ushort)StringGetCharacter(text,position);
      if(c<'0' || c>'9') break;
      position++;
   }
   if(position==digit_start)
   {
      error="JSON整数类型错误";
      return false;
   }
   if(position<length)
   {
      const ushort c=(ushort)StringGetCharacter(text,position);
      if(c=='.' || c=='e' || c=='E')
      {
         error="confidence必须为整数";
         return false;
      }
   }
   value=(int)StringToInteger(StringSubstr(text,start,position-start));
   return true;
}

string JsonEscape(const string value)
{
   string output="";
   for(int i=0;i<StringLen(value);i++)
   {
      const ushort c=(ushort)StringGetCharacter(value,i);
      if(c==34) output+="\\\"";
      else if(c==92) output+="\\\\";
      else if(c==8) output+="\\b";
      else if(c==12) output+="\\f";
      else if(c==10) output+="\\n";
      else if(c==13) output+="\\r";
      else if(c==9) output+="\\t";
      else if(c<32) output+=StringFormat("\\u%04X",(int)c);
      else output+=ShortToString(c);
   }
   return output;
}

bool ParseAIDecision(const string json,AIDecision &decision,string &error)
{
   ResetAIDecision(decision);
   error="";
   int position=0;
   JsonSkipWhitespace(json,position);
   if(position>=StringLen(json) || StringGetCharacter(json,position)!='{')
   {
      error="AI决策不是JSON对象";
      return false;
   }
   position++;
   bool have_allow=false,have_confidence=false,have_reason=false;
   while(true)
   {
      JsonSkipWhitespace(json,position);
      if(position<StringLen(json) && StringGetCharacter(json,position)=='}')
      {
         position++;
         break;
      }
      string key;
      if(!JsonParseString(json,position,key,error)) return false;
      JsonSkipWhitespace(json,position);
      if(position>=StringLen(json) || StringGetCharacter(json,position)!=':')
      {
         error="JSON字段缺少冒号";
         return false;
      }
      position++;
      if(key=="allow_trade")
      {
         if(have_allow){ error="allow_trade重复"; return false; }
         if(!JsonParseBool(json,position,decision.allow_trade,error)) return false;
         have_allow=true;
      }
      else if(key=="confidence")
      {
         if(have_confidence){ error="confidence重复"; return false; }
         if(!JsonParseInteger(json,position,decision.confidence,error)) return false;
         have_confidence=true;
      }
      else if(key=="reason")
      {
         if(have_reason){ error="reason重复"; return false; }
         if(!JsonParseString(json,position,decision.reason,error)) return false;
         have_reason=true;
      }
      else
      {
         error="AI决策包含不允许的字段: "+key;
         return false;
      }
      JsonSkipWhitespace(json,position);
      if(position>=StringLen(json))
      {
         error="JSON对象未结束";
         return false;
      }
      const ushort separator=(ushort)StringGetCharacter(json,position++);
      if(separator=='}') break;
      if(separator!=',')
      {
         error="JSON字段分隔符非法";
         return false;
      }
   }
   JsonSkipWhitespace(json,position);
   if(position!=StringLen(json) || !have_allow || !have_confidence || !have_reason)
   {
      error="AI决策字段缺失或对象外存在内容";
      return false;
   }
   if(decision.confidence<0 || decision.confidence>100)
   {
      error="confidence超出0到100";
      return false;
   }
   decision.valid=true;
   decision.is_error=false;
   return true;
}

bool ExtractAssistantContent(const string response,string &content,string &error)
{
   content="";
   error="";
   const int message_position=StringFind(response,"\"message\"");
   if(message_position<0){ error="响应缺少message"; return false; }
   const int key_position=StringFind(response,"\"content\"",message_position);
   if(key_position<0){ error="响应缺少content"; return false; }
   int position=StringFind(response,":",key_position);
   if(position<0){ error="content缺少冒号"; return false; }
   position++;
   if(!JsonParseString(response,position,content,error)) return false;
   if(content==""){ error="content为空"; return false; }
   return true;
}

bool ParseModelsResponse(const string response,const string required_model,string &error)
{
   error="";
   if(required_model=="") { error="模型名称为空"; return false; }
   int data_pos=StringFind(response,"\"data\"");
   if(data_pos<0) { error="响应缺少data字段"; return false; }
   int array_start=StringFind(response,"[",data_pos);
   if(array_start<0) { error="data不是数组"; return false; }

   int depth=0;
   bool in_string=false;
   bool escaped=false;
   int array_end=-1;
   for(int i=array_start;i<StringLen(response);i++)
   {
      const ushort c=StringGetCharacter(response,i);
      if(in_string)
      {
         if(escaped) { escaped=false; continue; }
         if(c=='\\') { escaped=true; continue; }
         if(c=='\"') in_string=false;
         continue;
      }
      if(c=='\"') { in_string=true; continue; }
      if(c=='[') depth++;
      else if(c==']')
      {
         depth--;
         if(depth==0) { array_end=i; break; }
      }
   }
   if(array_end<0) { error="data数组未闭合"; return false; }

   const string data=StringSubstr(response,array_start,array_end-array_start+1);
   int position=0;
   while(true)
   {
      const int id_pos=StringFind(data,"\"id\"",position);
      if(id_pos<0) break;
      int colon=StringFind(data,":",id_pos+4);
      if(colon<0) { error="模型id字段非法"; return false; }
      int value_pos=colon+1;
      string model="";
      if(!JsonParseString(data,value_pos,model,error)) return false;
      if(model==required_model) return true;
      position=value_pos;
   }
   error="模型列表不包含"+required_model;
   return false;
}

string JsonBool(const bool value) { return value ? "true" : "false"; }
string JsonNumber(const double value) { return DoubleToString(value,8); }

string AttemptLabel(const ENUM_TRADE_DIRECTION direction,const int attempt)
{
   if(direction==DIR_BUY)
   {
      if(attempt==2) return "H2";
      if(attempt==3) return "H3";
   }
   if(direction==DIR_SELL)
   {
      if(attempt==2) return "L2";
      if(attempt==3) return "L3";
   }
   return "NONE";
}

string BuildCandidatePayload(const string symbol,const ENUM_TIMEFRAMES timeframe,
                              const CandidateSignal &signal,
                              const MarketSnapshot &bars[])
{
   string json="{";
   json+="\"signal_id\":\""+JsonEscape(signal.signal_id)+"\",";
   json+="\"symbol\":\""+JsonEscape(symbol)+"\",\"timeframe\":\""+
         SignalTimeframeLabel(timeframe)+"\",";
   json+="\"direction\":\""+DirectionLabel(signal.direction)+"\",";
   json+="\"signal_route\":\""+SignalRouteLabel(signal.signal_route,signal.direction)+"\",";
   json+="\"fib_path_valid\":"+JsonBool(signal.fib_path_valid)+",";
   json+="\"ema_h23_path_valid\":"+JsonBool(signal.ema_h23_path_valid)+",";
   const string major_structure=(signal.direction==DIR_BUY ? "HH_HL" : "LL_LH");
   const string structure_direction=(signal.direction==DIR_BUY ? "BULLISH" : "BEARISH");
   json+="\"trend\":{";
   json+="\"ema20\":"+JsonNumber(signal.ema20)+",";
   json+="\"three_bar_move_usd\":"+JsonNumber(signal.three_bar_move_usd)+",";
   json+="\"three_bar_impulse_valid\":"+JsonBool(signal.three_bar_impulse_valid)+",";
   json+="\"three_bar_failure_reason\":\""+JsonEscape(signal.three_bar_failure_reason)+"\",";
   json+="\"major_structure\":\""+major_structure+"\",";
   json+="\"structure_direction\":\""+structure_direction+"\",";
   json+="\"candidate_direction\":\""+DirectionLabel(signal.direction)+"\",";
   json+="\"direction_consistent_with_structure\":true,";
   json+="\"structure_semantics\":\"HH_HL=BULLISH;LL_LH=BEARISH\",";
   json+="\"major_structure_valid\":"+JsonBool(signal.major_structure_valid)+"},";
   json+="\"swing\":{\"swing_low\":"+JsonNumber(signal.swing_low)+
         ",\"swing_high\":"+JsonNumber(signal.swing_high)+
         ",\"impulse_atr\":"+JsonNumber(signal.impulse_atr)+"},";
   json+="\"pullback\":{\"fib_retracement\":"+JsonNumber(signal.fib_retracement)+
         ",\"fib_zone\":\""+JsonEscape(signal.fib_zone)+
         "\",\"pullback_depth_atr\":"+JsonNumber(signal.pullback_depth_atr)+"},";
   json+="\"confluence\":{\"sr\":"+JsonBool(signal.sr_confluence)+
         ",\"ema20_near\":"+JsonBool(signal.ma_confluence)+
         ",\"divergence\":"+JsonBool(signal.divergence)+
         ",\"round_number\":"+JsonBool(signal.round_number_confluence)+"},";
   json+="\"price_action\":{\"h_attempt\":"+IntegerToString(signal.h_attempt)+
         ",\"attempt_label\":\""+AttemptLabel(signal.direction,signal.h_attempt)+"\""+
         ",\"h_near_ema\":"+JsonBool(signal.h_near_ema)+
         ",\"ema_distance_usd\":"+JsonNumber(signal.ema_distance_usd)+
         ",\"secondary_pattern_valid\":"+JsonBool(signal.secondary_pattern_valid)+
         ",\"pin_bar\":"+JsonBool(signal.pin_bar)+
         ",\"hammer\":"+JsonBool(signal.hammer)+
         ",\"engulfing\":"+JsonBool(signal.engulfing)+
         ",\"strong_reversal_bar\":"+JsonBool(signal.strong_reversal_bar)+"},";
   json+="\"trade_plan\":{\"entry\":"+JsonNumber(signal.planned_entry)+
         ",\"sl\":"+JsonNumber(signal.planned_sl)+
         ",\"tp1\":"+JsonNumber(signal.tp1)+
         ",\"rr_to_tp1\":"+JsonNumber(signal.rr_to_tp1)+"},";
   json+="\"market\":{\"atr14\":"+JsonNumber(signal.atr14)+"},\"bars\":[";
   const int start=MathMax(0,ArraySize(bars)-30);
   for(int i=start;i<ArraySize(bars);i++)
   {
      if(i>start) json+=",";
      json+="{\"time\":\""+JsonEscape(TimeToString(bars[i].time,TIME_DATE|TIME_SECONDS))+"\",";
      json+="\"open\":"+JsonNumber(bars[i].open)+",\"high\":"+JsonNumber(bars[i].high)+
            ",\"low\":"+JsonNumber(bars[i].low)+",\"close\":"+JsonNumber(bars[i].close)+
            ",\"tick_volume\":"+IntegerToString((int)bars[i].tick_volume)+
            ",\"ema20\":"+JsonNumber(bars[i].ema20)+",\"rsi14\":"+JsonNumber(bars[i].rsi14)+
            ",\"macd_hist\":"+JsonNumber(bars[i].macd_hist)+"}";
   }
   json+="]}";
   return json;
}

#endif
// ===== END INLINE: Include/XAUAI/JsonLite.mqh =====

// ===== BEGIN INLINE: Include/XAUAI/CsvLogger.mqh =====
#ifndef XAUAI_CSV_LOGGER_MQH
#define XAUAI_CSV_LOGGER_MQH

// [单文件合并] 已内联，跳过重复模块: StrategyTypes.mqh

string CsvEscape(const string value)
{
   string escaped=value;
   StringReplace(escaped,"\"","\"\"");
   if(StringFind(value,",")>=0 || StringFind(value,"\"")>=0 ||
      StringFind(value,"\r")>=0 || StringFind(value,"\n")>=0)
      return "\""+escaped+"\"";
   return escaped;
}

bool ParseCsvLine(const string line,string &fields[])
{
   ArrayResize(fields,0);
   string field="";
   bool quoted=false;
   for(int i=0;i<StringLen(line);i++)
   {
      const ushort c=(ushort)StringGetCharacter(line,i);
      if(c==34)
      {
         if(quoted && i+1<StringLen(line) && StringGetCharacter(line,i+1)==34)
         {
            field+="\""; i++; continue;
         }
         quoted=!quoted;
      }
      else if(c==44 && !quoted)
      {
         const int size=ArraySize(fields);
         ArrayResize(fields,size+1);
         fields[size]=field;
         field="";
      }
      else field+=ShortToString(c);
   }
   if(quoted) return false;
   const int size=ArraySize(fields);
   ArrayResize(fields,size+1);
   fields[size]=field;
   return true;
}

class CReplayDecisionMap
{
private:
   string     m_ids[];
   AIDecision m_decisions[];
public:
   void Clear(){ ArrayResize(m_ids,0); ArrayResize(m_decisions,0); }
   int Size() const { return ArraySize(m_ids); }
   bool Add(const string id,const AIDecision &decision,string &error)
   {
      AIDecision ignored;
      if(Find(id,ignored)){ error="重复SignalID: "+id; return false; }
      const int size=ArraySize(m_ids);
      ArrayResize(m_ids,size+1); ArrayResize(m_decisions,size+1);
      m_ids[size]=id; m_decisions[size]=decision;
      return true;
   }
   bool Find(const string id,AIDecision &decision) const
   {
      for(int i=0;i<ArraySize(m_ids);i++)
         if(m_ids[i]==id){ decision=m_decisions[i]; return true; }
      ResetAIDecision(decision);
      decision.error_code="AI_REPLAY_MISSING";
      decision.reason="AI_REPLAY缺少SignalID";
      return false;
   }
};

bool LoadReplayDecisions(const string filename,CReplayDecisionMap &map,string &error)
{
   map.Clear(); error="";
   const int handle=FileOpen(filename,FILE_READ|FILE_TXT|FILE_ANSI|FILE_SHARE_READ,0,CP_UTF8);
   if(handle==INVALID_HANDLE){ error="无法打开AI Replay文件"; return false; }
   bool header=true;
   while(!FileIsEnding(handle))
   {
      const string line=FileReadString(handle);
      if(header){ header=false; continue; }
      if(StringLen(line)==0) continue;
      string fields[];
      if(!ParseCsvLine(line,fields) || ArraySize(fields)<4)
      { FileClose(handle); error="Replay CSV行格式错误"; return false; }
      AIDecision decision; ResetAIDecision(decision);
      if(fields[1]=="true" || fields[1]=="1") decision.allow_trade=true;
      else if(fields[1]=="false" || fields[1]=="0") decision.allow_trade=false;
      else { FileClose(handle); error="Replay AllowTrade非法"; return false; }
      decision.confidence=(int)StringToInteger(fields[2]);
      if(decision.confidence<0 || decision.confidence>100)
      { FileClose(handle); error="Replay Confidence越界"; return false; }
      decision.reason=fields[3]; decision.valid=true; decision.is_error=false;
      if(!map.Add(fields[0],decision,error)){ FileClose(handle); return false; }
   }
   FileClose(handle);
   return true;
}

bool AppendUtf8CsvLine(const string filename,const string header,const string line)
{
   // 用 FILE_COMMON 写到共享 Common Files：策略测试器跑完后诊断 CSV 仍然保留，
   // 供 Python V20 Reference 做运行态计算对账。只改输出位置，不改变任何交易逻辑。
   const int handle=FileOpen(filename,FILE_READ|FILE_WRITE|FILE_TXT|FILE_ANSI|
                              FILE_SHARE_READ|FILE_SHARE_WRITE,FILE_COMMON,CP_UTF8);
   if(handle==INVALID_HANDLE) return false;
   if(FileSize(handle)==0) FileWriteString(handle,header+"\r\n");
   FileSeek(handle,0,SEEK_END);
   FileWriteString(handle,line+"\r\n");
   FileFlush(handle);
   FileClose(handle);
   return true;
}

class CCsvLogger
{
private:
   string m_candidate_file;
   string m_decision_file;
public:
   void Init(const string candidate_file="AI_Candidates.csv",
             const string decision_file="AI_Decisions.csv")
   { m_candidate_file=candidate_file; m_decision_file=decision_file; }

   bool WriteCandidate(const CandidateSignal &s,const string symbol,
                       const double spread,const string payload)
   {
      const string header="SignalID,Time,Symbol,Direction,Entry,SL,TP1,RR,Fib,EMA20,ThreeBarMoveUSD,ThreeBarImpulseValid,Structure,H_Attempt,HNearEMA,EMADistanceUSD,SecondaryPatternValid,PinBar,Hammer,Engulfing,StrongReversal,SR,EMAConfluence,ATR,Spread,SignalRoute,FibPathValid,EMAH23PathValid,ThreeBarFailureReason,AttemptLabel,PayloadJSON";
      string row=CsvEscape(s.signal_id)+","+CsvEscape(TimeToString(s.signal_time,TIME_DATE|TIME_SECONDS))+","+
         CsvEscape(symbol)+","+DirectionLabel(s.direction)+","+DoubleToString(s.planned_entry,8)+","+
         DoubleToString(s.planned_sl,8)+","+DoubleToString(s.tp1,8)+","+DoubleToString(s.rr_to_tp1,4)+","+
         DoubleToString(s.fib_retracement,2)+","+DoubleToString(s.ema20,8)+","+
         DoubleToString(s.three_bar_move_usd,2)+","+(s.three_bar_impulse_valid?"true":"false")+","+
         (s.direction==DIR_BUY ? "HH_HL" : "LL_LH")+","+IntegerToString(s.h_attempt)+","+
         (s.h_near_ema?"true":"false")+","+DoubleToString(s.ema_distance_usd,2)+","+
         (s.secondary_pattern_valid?"true":"false")+","+(s.pin_bar?"true":"false")+","+
         (s.hammer?"true":"false")+","+(s.engulfing?"true":"false")+","+
         (s.strong_reversal_bar?"true":"false")+","+(s.sr_confluence?"true":"false")+","+
         (s.ma_confluence?"true":"false")+","+DoubleToString(s.atr14,8)+","+
         DoubleToString(spread,8)+","+CsvEscape(SignalRouteLabel(s.signal_route,s.direction))+","+
         (s.fib_path_valid?"true":"false")+","+(s.ema_h23_path_valid?"true":"false")+","+
         CsvEscape(s.three_bar_failure_reason)+","+
         CsvEscape(AttemptLabel(s.direction,s.h_attempt))+","+CsvEscape(payload);
      return AppendUtf8CsvLine(m_candidate_file,header,row);
   }

   bool WriteDecision(const CandidateSignal &s,const AIDecision &d)
   {
      const string header="SignalID,Time,AllowTrade,Confidence,Reason,PromptVersion,Model,HTTPStatus,ResponseTimeMs";
      const string row=CsvEscape(s.signal_id)+","+CsvEscape(TimeToString(s.signal_time,TIME_DATE|TIME_SECONDS))+","+
         (d.allow_trade?"true":"false")+","+IntegerToString(d.confidence)+","+CsvEscape(d.reason)+","+
         CsvEscape(d.prompt_version)+","+CsvEscape(d.model)+","+IntegerToString(d.http_status)+","+
         IntegerToString(d.response_time_ms);
      return AppendUtf8CsvLine(m_decision_file,header,row);
   }
};

#endif
// ===== END INLINE: Include/XAUAI/CsvLogger.mqh =====

#define AI_PROMPT_VERSION "AI_FILTER_V3_8_1_STRUCTURE_SEMANTICS_R1"

bool IsApproved(const AIDecision &decision,const int threshold,const bool allow_error_grace)
{
   if(decision.is_error)
      return allow_error_grace && decision.allow_trade;
   return decision.valid && decision.allow_trade &&
          decision.confidence>=threshold;
}

string DeepSeekSystemPrompt()
{
   return
    "You are the V"+V310_EA_VERSION+" Strategy 01 candidate quality reviewer. "
   "The EA has already evaluated closed M15/M5 bars, directional M15 strong-trend context, simple-pullback H2, location, signal bar, target space, risk and broker rules. "
   "You may only allow or reject this completed long or short candidate for obvious quality or data-consistency problems. "
   "You must not recompute direction, entry, FinalStop, volume, H count, or deterministic validity. "
   "You must not change any price, stop, size, side, rule, or parameter. "
    "Use rule version "+V310_RULE_VERSION+". Return only JSON with allow_trade, confidence, reason.";
#ifdef V310_ENABLE_LEGACY_AI_PROMPT
   return
   "Serialized route evidence fields: signal_route, fib_path_valid, ema_h23_path_valid, three_bar_impulse_valid, three_bar_failure_reason.\n"
   "你是一个严格但不过度过滤的 MT5 XAUUSD M5已收盘周期 EA V3.8.1 候选交易信号过滤器。\n"
   "你的唯一职责是审核EA已经产生的候选交易信号。\n"
   "你不能寻找新的交易机会。你不能改变候选交易方向。你不能把BUY改成SELL，也不能把SELL改成BUY。"
   "你不能决定仓位。你不能修改止损止盈。你不能绕过EA风险限制。\n"
   "你的目标不是寻找完美交易，而是排除明显低质量、明显矛盾和已经失去入场价值的交易。\n"
   "候选发送前，EA已执行共同的本地账户安全、主要结构、点差、止损和风险收益门禁，"
   "并已计算entry、sl、tp1、rr_to_tp1、atr14等候选数值风险字段。\n"
   "结构标签是EA已经确认的硬事实，必须按以下唯一定义读取，不得重新定义或反向解释："
   "HH_HL = Higher High + Higher Low = BULLISH = 上涨结构；"
   "LL_LH = Lower Low + Lower High = BEARISH = 下跌结构。\n"
   "BUY候选出现HH_HL表示方向一致；SELL候选出现LL_LH表示方向一致。"
   "当direction_consistent_with_structure=true且major_structure_valid=true时，"
   "不得以结构方向矛盾为理由拒绝，也不得把LL_LH解释为上涨或把HH_HL解释为下跌。"
   "上述字段完整时，不得声称关键市场结构数据不足。\n"
   "你必须读取signal_route、fib_path_valid、ema_h23_path_valid、three_bar_impulse_valid和"
   "three_bar_failure_reason来理解候选实际通过的路径和软证据。\n"
   "FIB_PA表示fib_path_valid=true并且同方向Price Action有效。"
   "EMA_H23表示买入的EMA20附近H2/H3路径；EMA_L23表示卖出的EMA20附近L2/L3路径。"
   "fib_path_valid=false is allowed for EMA_H23 and EMA_L23。"
   "BOTH表示两条入场路径都通过，但仍然只有一个候选。\n"
   "three_bar_impulse_valid is soft evidence。三根HH/HL或LL/LH推进及其总幅度仅作趋势上下文，"
   "three_bar_failure_reason解释该软证据为何失败。"
   "three_bar_impulse_valid=false alone must not reject a valid candidate。\n"
   "Not every candidate has passed all Fib/three-bar/H2H3/second-PA hard rules. "
   "不要重新实施已废弃的本地入场硬门禁；AI是第二层风险和数据一致性过滤器。\n"
   "S/R不存在、没有RSI/MACD背离或Fib不是精确50%，"
   "都不能单独作为拒绝交易的理由。这些属于增强条件，只能影响confidence。\n"
   "重点审核：候选方向和signal_route是否与主要趋势及序列化证据一致；回调是否仍合理；"
   "是否明显过度延伸；核心数据是否矛盾或缺失；现有风险字段是否显示真实风险。\n"
   "强否决可包括：逆主要趋势、Major结构明确失效、数据或路径字段矛盾、明显追涨追跌、"
   "核心数据缺失、风险字段明显不合理或入场明显失去价值。不要因为软证据失败或信号不完美就拒绝。\n"
   "confidence是对候选值得EA继续执行的综合信心，不是未来涨跌概率。"
   "0-39明显错误；40-59明显较弱；60-69暂不建议；70-79正常合格；80-89较高质量；90-100极高质量且少用。\n"
   "confidence=0只允许用于输入JSON损坏、关键字段确实缺失、价格数据不可用，或direction与显式structure_direction真正互相矛盾。"
   "正常但较弱的候选必须使用40-69，不得因为误读HH_HL或LL_LH而返回0。\n"
   "存在强否决则allow_trade=false；否则confidence>=70时allow_trade=true，低于70时false。\n"
   "只能返回合法JSON。禁止Markdown、代码块、JSON外文字和附加字段。"
   "JSON字段必须且只能为allow_trade、confidence、reason。"
   "示例JSON：{\"allow_trade\":true,\"confidence\":76,\"reason\":\"主要上涨结构仍然有效。\"}。"
   "仅在上述confidence=0允许条件满足时，才返回{\"allow_trade\":false,\"confidence\":0,\"reason\":\"关键输入字段确实缺失，无法可靠审核。\"}。";
#endif
}

string ReadApiKeyFile(const string filename)
{
   const int handle=FileOpen(filename,FILE_READ|FILE_TXT|FILE_ANSI|FILE_SHARE_READ,0,CP_UTF8);
   if(handle==INVALID_HANDLE) return "";
   string key=FileReadString(handle);
   FileClose(handle);
   StringTrimLeft(key); StringTrimRight(key);
   return key;
}

string DeepSeekHttpDiagnosticSnippet(const string source,const int max_chars=1200)
{
   string text=source;
   if(text=="") return "<empty>";
   StringReplace(text,"\r","\\r");
   StringReplace(text,"\n","\\n");
   if(StringLen(text)>max_chars) text=StringSubstr(text,0,max_chars)+"...<truncated>";
   return text;
}

class CDeepSeekClient
{
private:
   ENUM_AI_MODE       m_mode;
   string             m_key;
   string             m_model;
   int                m_timeout_ms;
   int                m_retry_count;
   CReplayDecisionMap m_replay;
   int                m_candidates;
   int                m_allowed;
   int                m_rejected;
   int                m_errors;
   bool               m_allow_error_grace;
   AIConnectionResult m_connection;

public:
   CDeepSeekClient()
   {
      m_mode=AI_OFF; m_key=""; m_model="deepseek-v4-flash";
      m_timeout_ms=3000; m_retry_count=1; m_allow_error_grace=true;
      m_candidates=0; m_allowed=0; m_rejected=0; m_errors=0;
      ResetAIConnectionResult(m_connection);
   }

   bool Init(const ENUM_AI_MODE mode,const ENUM_API_KEY_SOURCE key_source,
             const string input_key,const string key_file,const string replay_file,
             const string model,const int timeout_ms,const int retry_count,
             const bool allow_error_grace,string &error)
   {
      error=""; m_mode=mode; m_model=model; m_timeout_ms=timeout_ms;
      ResetAIConnectionResult(m_connection);
      m_retry_count=MathMax(0,MathMin(retry_count,1));
      m_allow_error_grace=allow_error_grace;
      if(mode==AI_REPLAY) return LoadReplayDecisions(replay_file,m_replay,error);
      if(mode==AI_LIVE)
      {
         m_key=(key_source==KEY_FROM_FILE ? ReadApiKeyFile(key_file) : input_key);
         StringTrimLeft(m_key); StringTrimRight(m_key);
         if(m_key==""){ error="DeepSeek API Key为空"; return false; }
      }
      return true;
   }

   bool TestConnection(AIConnectionResult &result,string &error)
   {
      ResetAIConnectionResult(result);
      result.attempted=true;
      result.model=m_model;
      error="";
      if(m_mode!=AI_LIVE)
      {
         result.reason="当前AI模式不需要联网自检";
         m_connection=result;
         return true;
      }
      if(m_key=="")
      {
         result.reason="DeepSeek API Key为空";
         error=result.reason;
         m_connection=result;
         return false;
      }

      const string headers="Authorization: Bearer "+m_key+"\r\n";
      for(int attempt=0;attempt<=m_retry_count;attempt++)
      {
         char request_bytes[];
         char response_bytes[];
         string response_headers="";
         ArrayResize(request_bytes,0);
         ResetLastError();
         const uint started=GetTickCount();
         const int status=WebRequest("GET","https://api.deepseek.com/models",
                                     headers,m_timeout_ms,request_bytes,
                                     response_bytes,response_headers);
         const int mt5_error=GetLastError();
         result.response_time_ms=(int)(GetTickCount()-started);
         result.http_status=status;
         result.mt5_error=mt5_error;
         const string response=CharArrayToString(response_bytes,0,WHOLE_ARRAY,CP_UTF8);
         if(status==200)
         {
            string parse_error="";
            if(ParseModelsResponse(response,m_model,parse_error))
            {
               result.connected=true;
               result.reason="连接成功";
               m_connection=result;
               return true;
            }
            result.reason=parse_error;
            Print("[DeepSeek诊断] HTTP=",status,
                  " | MT5Error=",mt5_error,
                  " | Attempt=",attempt+1,"/",m_retry_count+1,
                  " | KeyLength=",StringLen(m_key),
                  " | ParseError=",parse_error,
                  " | ResponseBody=",DeepSeekHttpDiagnosticSnippet(response),
                  " | ResponseHeaders=",DeepSeekHttpDiagnosticSnippet(response_headers));
         }
         else
         {
            result.reason="DeepSeek模型接口请求失败";
            Print("[DeepSeek诊断] HTTP=",status,
                  " | MT5Error=",mt5_error,
                  " | Attempt=",attempt+1,"/",m_retry_count+1,
                  " | KeyLength=",StringLen(m_key),
                  " | ResponseBody=",DeepSeekHttpDiagnosticSnippet(response),
                  " | ResponseHeaders=",DeepSeekHttpDiagnosticSnippet(response_headers));
         }
      }
      error=result.reason;
      m_connection=result;
      return false;
   }

   AIConnectionResult ConnectionState() const { return m_connection; }

   AIDecision Review(const CandidateSignal &candidate,const string payload)
   {
      AIDecision decision; ResetAIDecision(decision);
      m_candidates++;
      decision.model=m_model; decision.prompt_version=AI_PROMPT_VERSION;
      if(m_mode==AI_OFF)
      {
         decision.valid=true; decision.is_error=false; decision.allow_trade=true;
         decision.confidence=100; decision.reason="AI_OFF本地基准模式";
         m_allowed++; return decision;
      }
      if(m_mode==AI_REPLAY)
      {
         if(!m_replay.Find(candidate.signal_id,decision))
         { m_errors++; return decision; }
         decision.model="REPLAY"; decision.prompt_version=AI_PROMPT_VERSION;
         if(decision.allow_trade) m_allowed++; else m_rejected++;
         return decision;
      }

      const string body="{\"model\":\""+JsonEscape(m_model)+"\",\"messages\":["
         "{\"role\":\"system\",\"content\":\""+JsonEscape(DeepSeekSystemPrompt())+"\"},"
         "{\"role\":\"user\",\"content\":\""+JsonEscape(payload)+"\"}],"
         "\"stream\":false,\"thinking\":{\"type\":\"disabled\"},"
         "\"response_format\":{\"type\":\"json_object\"},\"max_tokens\":300}";
      char post[];
      StringToCharArray(body,post,0,WHOLE_ARRAY,CP_UTF8);
      if(ArraySize(post)>0) ArrayResize(post,ArraySize(post)-1);
      const string headers="Content-Type: application/json\r\nAuthorization: Bearer "+m_key+"\r\n";
      for(int attempt=0;attempt<=m_retry_count;attempt++)
      {
         char response_bytes[]; string response_headers;
         const uint started=GetTickCount();
         ResetLastError();
         const int status=WebRequest("POST","https://api.deepseek.com/chat/completions",
                                     headers,m_timeout_ms,post,response_bytes,response_headers);
         decision.response_time_ms=(int)(GetTickCount()-started);
         decision.http_status=status;
         if(status==200)
         {
            const string response=CharArrayToString(response_bytes,0,WHOLE_ARRAY,CP_UTF8);
            string content,error;
            if(ExtractAssistantContent(response,content,error) &&
               ParseAIDecision(content,decision,error))
            {
               decision.model=m_model; decision.prompt_version=AI_PROMPT_VERSION;
               if(decision.allow_trade) m_allowed++; else m_rejected++;
               return decision;
            }
            Print("[DeepSeek响应正文] HTTP=200 | JSON解析失败 | Body=",
                  DeepSeekHttpDiagnosticSnippet(response));
            decision.reason="AI JSON异常: "+error;
            decision.error_code="AI_JSON_INVALID";
         }
         else
         {
            const string response_body=CharArrayToString(response_bytes,0,WHOLE_ARRAY,CP_UTF8);
            Print("[DeepSeek响应正文] HTTP=",status,
                  " | Attempt=",attempt+1,"/",m_retry_count+1,
                  " | Body=",DeepSeekHttpDiagnosticSnippet(response_body),
                  " | Headers=",DeepSeekHttpDiagnosticSnippet(response_headers));
            decision.reason="DeepSeek请求失败，HTTP="+IntegerToString(status)+
                            "，MT5错误="+IntegerToString(GetLastError());
            decision.error_code="AI_HTTP_ERROR";
         }
      }
      decision.valid=false; decision.is_error=true;
      decision.allow_trade=m_allow_error_grace;
      decision.confidence=0; m_errors++;
      if(decision.allow_trade)
         Print("[AI审核异常·降级放行] 重试后DeepSeek仍失败，按本地信号继续 | SignalID=",
               candidate.signal_id," | ",decision.reason);
      return decision;
   }

   int CandidateCount() const { return m_candidates; }
   int AllowedCount() const { return m_allowed; }
   int RejectedCount() const { return m_rejected; }
   int ErrorCount() const { return m_errors; }
   double AllowRate() const
   { return m_candidates>0 ? (double)m_allowed/(double)m_candidates : 0.0; }
};

#endif
// ===== END INLINE: Include/XAUAI/DeepSeekClient.mqh =====

// ===== BEGIN INLINE: Include/XAUAI/RiskManager.mqh =====
#ifndef XAUAI_RISK_MANAGER_MQH
#define XAUAI_RISK_MANAGER_MQH

// [单文件合并] 已内联，跳过重复模块: StrategyTypes.mqh

int VolumeDigits(const double step)
{
   for(int digits=0;digits<=8;digits++)
      if(MathAbs(step-NormalizeDouble(step,digits))<1e-10)
         return digits;
   return 8;
}

double NormalizeVolumeDown(const double raw,const double step)
{
   if(raw<=0.0 || step<=0.0) return 0.0;
   const double units=MathFloor((raw+1e-12)/step);
   return NormalizeDouble(units*step,VolumeDigits(step));
}

double NormalizeVolumeUp(const double raw,const double step)
{
   if(raw<=0.0 || step<=0.0) return 0.0;
   const double units=MathCeil((raw-1e-12)/step);
   return NormalizeDouble(units*step,VolumeDigits(step));
}

double AlignPriceDown(const double price,const double tick_size)
{
   if(price<=0.0 || tick_size<=0.0) return 0.0;
   return NormalizeDouble(MathFloor((price+1e-10)/tick_size)*tick_size,10);
}

double AlignPriceUp(const double price,const double tick_size)
{
   if(price<=0.0 || tick_size<=0.0) return 0.0;
   return NormalizeDouble(MathCeil((price-1e-10)/tick_size)*tick_size,10);
}


bool AlignCandidateTradePrices(CandidateSignal &candidate,
                               const double tick_size,
                               const double max_sl_atr,
                               const double min_rr,
                               const double ema_max_stop_expansion_ratio,
                               string &error)
{
   error="";
   if(candidate.direction!=DIR_BUY && candidate.direction!=DIR_SELL)
   {
      error="候选方向无效，无法进行价格步长对齐";
      return false;
   }
   if(tick_size<=0.0)
   {
      error="经纪商最小价格步长无效";
      return false;
   }

   const double raw_entry=candidate.planned_entry;
   const double raw_legacy_sl=candidate.legacy_sl;
   const double raw_signal_sl=candidate.signal_sl;

   double aligned_entry=raw_entry;
   double aligned_legacy_sl=raw_legacy_sl;
   double aligned_signal_sl=raw_signal_sl;
   if(candidate.direction==DIR_BUY)
   {
      aligned_entry=AlignPriceUp(raw_entry,tick_size);
      aligned_legacy_sl=AlignPriceDown(raw_legacy_sl,tick_size);
      aligned_signal_sl=(raw_signal_sl>0.0 ? AlignPriceDown(raw_signal_sl,tick_size) : 0.0);
   }
   else
   {
      aligned_entry=AlignPriceDown(raw_entry,tick_size);
      aligned_legacy_sl=AlignPriceUp(raw_legacy_sl,tick_size);
      aligned_signal_sl=(raw_signal_sl>0.0 ? AlignPriceUp(raw_signal_sl,tick_size) : 0.0);
   }
   candidate.planned_entry=aligned_entry;
   candidate.legacy_sl=aligned_legacy_sl;
   candidate.signal_sl=aligned_signal_sl;

   const double legacy_risk=MathAbs(aligned_entry-aligned_legacy_sl);
   // V20：冻结风险距离必须在 Entry/LegacySL 对齐完成后，用最终对齐价格重算，
   // 保证 EA 的 ExitUnit 与 Python Reference 的对齐后 |Entry-LegacySL| 完全一致。
   candidate.exit_unit=legacy_risk;
   const double signal_risk=(aligned_signal_sl>0.0 ? MathAbs(aligned_entry-aligned_signal_sl) : 0.0);

   // V3.9.18 混合止损选择：默认LegacySL，仅EMA/BOTH在SignalSL更宽且合规时启用。
   double selected_sl=aligned_legacy_sl;
   ENUM_STOP_MODE stop_mode=STOP_MODE_LEGACY_ONLY;
   ENUM_STOP_FALLBACK_REASON fallback=STOP_FALLBACK_NONE;
   double expansion_ratio=0.0;
   const bool is_ema_path=(candidate.signal_route==SIGNAL_ROUTE_EMA_H23 ||
                           candidate.signal_route==SIGNAL_ROUTE_BOTH);
   if(is_ema_path && aligned_signal_sl>0.0)
   {
      if(signal_risk<=legacy_risk)
      {
         fallback=STOP_FALLBACK_SIGNAL_NOT_WIDER;
      }
      else
      {
         expansion_ratio=(legacy_risk>0.0 ? signal_risk/legacy_risk : 0.0);
         double signal_rr=0.0;
         const bool signal_rr_ok=TryCalculateDirectionalTargetRR(candidate.direction,aligned_entry,
                                                                  candidate.tp1,signal_risk,signal_rr);
         if(expansion_ratio>ema_max_stop_expansion_ratio)
            fallback=STOP_FALLBACK_EXPANSION_TOO_LARGE;
         else if(signal_risk>max_sl_atr*candidate.atr14)
            fallback=STOP_FALLBACK_SIGNAL_GT_MAX_SL_ATR;
         else if(!signal_rr_ok || signal_rr<min_rr)
            fallback=STOP_FALLBACK_SIGNAL_RR_TOO_LOW;
      }
      if(fallback==STOP_FALLBACK_NONE)
      {
         selected_sl=aligned_signal_sl;
         stop_mode=STOP_MODE_EMA_SIGNAL_EXPANDED;
      }
      else
      {
         stop_mode=STOP_MODE_LEGACY_FALLBACK;
      }
   }

   candidate.planned_sl=selected_sl;
   candidate.stop_mode=stop_mode;
   candidate.stop_fallback_reason=fallback;
   candidate.expansion_ratio=expansion_ratio;

   const double risk_after=MathAbs(candidate.planned_entry-candidate.planned_sl);
   if(candidate.planned_entry<=0.0 || candidate.planned_sl<=0.0 ||
      risk_after<=0.0 || candidate.atr14<=0.0)
   {
      error="价格步长对齐后Entry、SL或风险距离无效";
      return false;
   }
   if(!TryCalculateDirectionalTargetRR(candidate.direction,candidate.planned_entry,
                                       candidate.tp1,risk_after,candidate.rr_to_tp1))
   {
      error="价格步长对齐后TP1方向无效：BUY目标必须高于Entry，SELL目标必须低于Entry";
      return false;
   }
   if(risk_after>max_sl_atr*candidate.atr14)
   {
      error="价格步长对齐后SL距离超过ATR上限";
      return false;
   }

   if(candidate.rr_to_tp1<min_rr)
   {
      error="价格步长对齐后TP1盈亏比"+DoubleToString(candidate.rr_to_tp1,2)+
            "不足，要求>="+DoubleToString(min_rr,2);
      return false;
   }

   if(is_ema_path)
   {
      g_ema_hybrid.candidates++;
      if(candidate.stop_mode==STOP_MODE_EMA_SIGNAL_EXPANDED)
      {
         g_ema_hybrid.signal_sl_used++;
         g_ema_hybrid.expansion_sum+=candidate.expansion_ratio;
         if(candidate.expansion_ratio>g_ema_hybrid.expansion_max)
            g_ema_hybrid.expansion_max=candidate.expansion_ratio;
         if(candidate.direction==DIR_BUY) g_ema_hybrid.buy_signal_sl++;
         else if(candidate.direction==DIR_SELL) g_ema_hybrid.sell_signal_sl++;
      }
      else
      {
         g_ema_hybrid.legacy_sl_used++;
         if(candidate.stop_fallback_reason==STOP_FALLBACK_SIGNAL_NOT_WIDER) g_ema_hybrid.not_wider++;
         else if(candidate.stop_fallback_reason==STOP_FALLBACK_EXPANSION_TOO_LARGE) g_ema_hybrid.expansion_too_large++;
         else if(candidate.stop_fallback_reason==STOP_FALLBACK_SIGNAL_GT_MAX_SL_ATR) g_ema_hybrid.gt_max_sl_atr++;
         else if(candidate.stop_fallback_reason==STOP_FALLBACK_SIGNAL_RR_TOO_LOW) g_ema_hybrid.rr_too_low++;
      }

      double legacy_rr_aligned=0.0;
      double signal_rr_aligned=0.0;
      TryCalculateDirectionalTargetRR(candidate.direction,candidate.planned_entry,
                                      candidate.tp1,legacy_risk,legacy_rr_aligned);
      if(signal_risk>0.0)
         TryCalculateDirectionalTargetRR(candidate.direction,candidate.planned_entry,
                                         candidate.tp1,signal_risk,signal_rr_aligned);
      Print("[EMA混合止损]",
            " | SignalID=",candidate.signal_id,
            " | Direction=",DirectionLabel(candidate.direction),
            " | Route=",SignalRouteLabel(candidate.signal_route,candidate.direction),
            " | Entry=",DoubleToString(candidate.planned_entry,_Digits),
            " | ATR=",DoubleToString(candidate.atr14,2),
            " | LegacySL=",DoubleToString(candidate.legacy_sl,_Digits),
            " | LegacyRisk=",DoubleToString(legacy_risk,2),
            " | LegacyRR=",DoubleToString(legacy_rr_aligned,2),
            " | SignalSL=",DoubleToString(candidate.signal_sl,_Digits),
            " | SignalRisk=",DoubleToString(signal_risk,2),
            " | SignalRR=",DoubleToString(signal_rr_aligned,2),
            " | ExpansionRatio=",DoubleToString(expansion_ratio,3),
            " | ExpansionLimit=",DoubleToString(ema_max_stop_expansion_ratio,2),
            " | SelectedSL=",DoubleToString(candidate.planned_sl,_Digits),
            " | SelectedRisk=",DoubleToString(risk_after,2),
            " | StopMode=",StopModeLabel(candidate.stop_mode),
            " | ExitUnit=",DoubleToString(candidate.exit_unit,_Digits),
            " | ExitDistanceMode=",ExitDistanceModeLabel(ExitDistanceModeFor(candidate.stop_mode)),
            " | FallbackReason=",StopFallbackReasonLabel(candidate.stop_fallback_reason));
   }

   const double epsilon=tick_size*1e-6;
   if(MathAbs(raw_entry-candidate.planned_entry)>epsilon ||
      MathAbs(raw_legacy_sl-candidate.legacy_sl)>epsilon)
   {
      Print("[价格对齐] Direction=",DirectionLabel(candidate.direction),
            " | RawEntry=",DoubleToString(raw_entry,10),
            " | AlignedEntry=",DoubleToString(candidate.planned_entry,10),
            " | RawLegacySL=",DoubleToString(raw_legacy_sl,10),
            " | AlignedLegacySL=",DoubleToString(candidate.legacy_sl,10),
            " | TickSize=",DoubleToString(tick_size,10),
            " | LegacyRisk=",DoubleToString(legacy_risk,10),
            " | RR=",DoubleToString(candidate.rr_to_tp1,2));
   }
   return true;
}

bool CalculateFrozenExitPrices(const ENUM_TRADE_DIRECTION direction,
                               const double actual_entry_price,
                               const double initial_stop_loss,
                               const bool use_frozen_exit_unit,
                               const double exit_unit,
                               const double tp1_multiple,
                               const double tp2_multiple,
                               const double lock_multiple,
                               const double tick_size,
                               double &risk_distance,
                               double &tp1_price,
                               double &tp2_price,
                               double &lock_price,
                               string &error)
{
   error="";
   risk_distance=0.0;
   tp1_price=0.0;
   tp2_price=0.0;
   lock_price=0.0;
   if(direction==DIR_NONE || actual_entry_price<=0.0 || initial_stop_loss<=0.0 ||
      (use_frozen_exit_unit && exit_unit<=0.0) ||
      tick_size<=0.0 || tp1_multiple<=0.0 || tp2_multiple<=tp1_multiple ||
      lock_multiple<0.0)
   {
      error="实际成交止盈计划参数无效";
      return false;
   }
   risk_distance=MathAbs(actual_entry_price-initial_stop_loss);
   // V3.9.19：Legacy 模式完全忽略 exit_unit（复刻 V14），只有 SignalSL 模式才冻结距离。
   const double exit_distance=(use_frozen_exit_unit && exit_unit>0.0 ? exit_unit : risk_distance);
   if(risk_distance<tick_size*0.5 || !MathIsValidNumber(risk_distance))
   {
      error="实际成交价与初始止损无法形成有效R值";
      return false;
   }
   if(direction==DIR_BUY)
   {
      tp1_price=AlignPriceUp(actual_entry_price+exit_distance*tp1_multiple,tick_size);
      tp2_price=AlignPriceUp(actual_entry_price+exit_distance*tp2_multiple,tick_size);
      lock_price=AlignPriceDown(actual_entry_price+exit_distance*lock_multiple,tick_size);
   }
   else
   {
      tp1_price=AlignPriceDown(actual_entry_price-exit_distance*tp1_multiple,tick_size);
      tp2_price=AlignPriceDown(actual_entry_price-exit_distance*tp2_multiple,tick_size);
      lock_price=AlignPriceUp(actual_entry_price-exit_distance*lock_multiple,tick_size);
   }
   if(tp1_price<=0.0 || tp2_price<=0.0 || lock_price<=0.0)
   {
      error="止盈计划价格对齐失败";
      return false;
   }
   return true;
}

StageClosePlan BuildStageClosePlan(const double initial_volume,
                                   const double current_volume,
                                   const double close_percent,
                                   const double volume_min,
                                   const double volume_max,
                                   const double volume_step)
{
   StageClosePlan plan;
   ZeroMemory(plan);
   plan.reason="";
   if(initial_volume<=0.0 || current_volume<=0.0 || close_percent<=0.0 ||
      volume_min<=0.0 || volume_max<volume_min || volume_step<=0.0 ||
      current_volume>volume_max+volume_step*0.1)
   {
      plan.reason="阶段平仓手数输入无效";
      return plan;
   }
   const double minimum=NormalizeVolumeUp(volume_min,volume_step);
   const double current=NormalizeVolumeDown(current_volume,volume_step);
   if(current<minimum)
   {
      plan.reason="当前持仓低于最小手数，禁止发送订单";
      return plan;
   }
   double close_volume=NormalizeVolumeDown(initial_volume*close_percent/100.0,
                                            volume_step);
   if(close_volume<minimum) close_volume=minimum;
   if(close_volume>=current-volume_step*0.1)
   {
      plan.valid=true;
      plan.close_all=true;
      plan.close_volume=current;
      plan.expected_remaining=0.0;
      plan.reason="因小仓位无法继续拆分，本阶段全部止盈";
      return plan;
   }
   const double remaining=NormalizeVolumeDown(current-close_volume,volume_step);
   if(remaining<minimum)
   {
      plan.valid=true;
      plan.close_all=true;
      plan.close_volume=current;
      plan.expected_remaining=0.0;
      plan.reason="因小仓位无法继续拆分，本阶段全部止盈";
      return plan;
   }
   plan.valid=true;
   plan.close_all=false;
   plan.close_volume=close_volume;
   plan.expected_remaining=remaining;
   return plan;
}

bool IsConfirmedVolumeReduction(const double before_volume,
                                const bool position_exists_after,
                                const double after_volume,
                                const double volume_step)
{
   if(before_volume<=0.0 || volume_step<=0.0) return false;
   if(!position_exists_after) return true;
   return after_volume>=0.0 && after_volume<before_volume-volume_step*0.5;
}

bool IsStructureTargetBeyondActualTP2(const ENUM_TRADE_DIRECTION direction,
                                      const double structure_target,
                                      const double actual_tp2,
                                      const double tick_size)
{
   if(direction==DIR_NONE || structure_target<=0.0 || actual_tp2<=0.0 || tick_size<=0.0)
      return false;
   const double epsilon=tick_size*0.1;
   if(direction==DIR_BUY)
      return structure_target+epsilon>=actual_tp2+tick_size;
   return structure_target-epsilon<=actual_tp2-tick_size;
}

StopValidationResult ValidateProtectiveStop(const ENUM_TRADE_DIRECTION direction,
                                            const double desired_stop,
                                            const double current_stop,
                                            const double bid,
                                            const double ask,
                                            const double point,
                                            const double tick_size,
                                            const int stops_level,
                                            const int freeze_level)
{
   StopValidationResult result;
   ZeroMemory(result);
   result.reason="";
   if(direction==DIR_NONE || desired_stop<=0.0 || bid<=0.0 || ask<=0.0 ||
      point<=0.0 || tick_size<=0.0)
   {
      result.reason="止损校验输入无效";
      return result;
   }
   result.aligned_price=(direction==DIR_BUY ? AlignPriceDown(desired_stop,tick_size) :
                                                   AlignPriceUp(desired_stop,tick_size));
   const double epsilon=tick_size*0.1;
   const bool improves=(direction==DIR_BUY ?
      (current_stop<=0.0 || result.aligned_price>current_stop+epsilon) :
      (current_stop<=0.0 || result.aligned_price<current_stop-epsilon));
   if(!improves)
   {
      result.reason="新止损未优于当前止损";
      return result;
   }
   const double minimum_distance=MathMax((double)MathMax(stops_level,freeze_level),0.0)*point;
   const bool distance_ok=(direction==DIR_BUY ?
      bid-result.aligned_price+epsilon>=minimum_distance :
      result.aligned_price-ask+epsilon>=minimum_distance);
   if(!distance_ok)
   {
      result.reason="当前价格距离不足，不满足Stops/Freeze Level";
      return result;
   }
   result.valid=true;
   return result;
}

bool ValidateStagedExitParameters(const double tp1_multiple,
                                  const double tp1_close_percent,
                                  const double break_even_spread_multiple,
                                  const int break_even_min_points,
                                  const double tp2_multiple,
                                  const double tp2_close_percent,
                                  const double tp2_lock_multiple,
                                  string &error)
{
   error="";
   if(tp1_multiple<=0.0)
   { error="第一止盈R倍数必须大于0"; return false; }
   if(tp2_multiple<=tp1_multiple)
   { error="第二止盈R倍数必须大于第一止盈R倍数"; return false; }
   if(tp1_close_percent<=0.0)
   { error="第一止盈平仓比例必须大于0"; return false; }
   if(tp2_close_percent<=0.0)
   { error="第二止盈平仓比例必须大于0"; return false; }
   if(tp2_close_percent<100.0)
   { error="V3.10.7标准模式TP2必须全部退出剩余仓位"; return false; }
   if(break_even_spread_multiple<0.0)
   { error="保本缓冲点差倍数不得为负"; return false; }
   if(break_even_min_points<0)
   { error="保本最小额外缓冲点数不得为负"; return false; }
   if(tp2_lock_multiple<0.0)
   { error="第二止盈锁盈R倍数不得为负"; return false; }
   return true;
}

double CalculateRiskVolume(const double equity,const double risk_percent_input,
                           const double entry,const double sl,
                           const double tick_size,const double tick_value_loss,
                           const double volume_min,const double volume_max,
                           const double volume_step,string &error)
{
   error="";
   if(equity<=0.0 || risk_percent_input<0.1 || tick_size<=0.0 ||
      tick_value_loss<=0.0 || volume_min<=0.0 || volume_max<volume_min ||
      volume_step<=0.0 || entry==sl)
   {
      error="手数计算输入无效";
      return 0.0;
   }
   const double risk_percent=MathMin(risk_percent_input,1.0);
   const double risk_money=equity*risk_percent/100.0;
   const double loss_per_lot=MathAbs(entry-sl)/tick_size*tick_value_loss;
   if(loss_per_lot<=0.0 || !MathIsValidNumber(loss_per_lot))
   {
      error="每手止损金额无效";
      return 0.0;
   }
   double volume=NormalizeVolumeDown(risk_money/loss_per_lot,volume_step);
   volume=MathMin(volume,volume_max);
   if(volume<volume_min || volume*loss_per_lot>risk_money+0.01)
   {
      error="最小合法手数超过风险预算";
      return 0.0;
   }
   return volume;
}

int ServerDateKey(const datetime value)
{
   MqlDateTime parts;
   TimeToStruct(value,parts);
   return parts.year*10000+parts.mon*100+parts.day;
}

datetime ServerDayStart(const datetime value)
{
   MqlDateTime parts;
   TimeToStruct(value,parts);
   parts.hour=0;
   parts.min=0;
   parts.sec=0;
   return StructToTime(parts);
}

bool UseOwnFloatingForRiskLock()
{
   return (AccountInfoInteger(ACCOUNT_MARGIN_MODE)==ACCOUNT_MARGIN_MODE_RETAIL_HEDGING);
}

string IndependentRiskAccountModeLabel()
{
   const long mode=AccountInfoInteger(ACCOUNT_MARGIN_MODE);
   if(mode==ACCOUNT_MARGIN_MODE_RETAIL_HEDGING) return "HEDGING";
   if(mode==ACCOUNT_MARGIN_MODE_RETAIL_NETTING) return "NETTING";
   if(mode==ACCOUNT_MARGIN_MODE_EXCHANGE) return "EXCHANGE";
   return "UNKNOWN";
}

int    g_own_realized_date_key=0;
double g_own_realized_today=0.0;
bool   g_own_realized_ready=false;

bool RefreshOwnRealizedToday(const string symbol,const long magic,
                             const datetime server_time,string &error)
{
   error="";
   const int key=ServerDateKey(server_time);
   const datetime day_start=ServerDayStart(server_time);
   if(day_start<=0 || !HistorySelect(day_start,server_time))
   {
      error="无法读取本EA当日成交历史，MT5Error="+IntegerToString(GetLastError());
      return false;
   }

   double net=0.0;
   const int total=HistoryDealsTotal();
   for(int i=0;i<total;i++)
   {
      const ulong deal=HistoryDealGetTicket(i);
      if(deal==0) continue;
      if(HistoryDealGetString(deal,DEAL_SYMBOL)!=symbol ||
         !V3109C32MagicBelongsToGroup(magic,HistoryDealGetInteger(deal,DEAL_MAGIC)))
         continue;

      net+=HistoryDealGetDouble(deal,DEAL_PROFIT)+
           HistoryDealGetDouble(deal,DEAL_COMMISSION)+
           HistoryDealGetDouble(deal,DEAL_SWAP)+
           HistoryDealGetDouble(deal,DEAL_FEE);
   }

   g_own_realized_date_key=key;
   g_own_realized_today=net;
   g_own_realized_ready=true;
   return true;
}

bool EnsureOwnRealizedToday(const string symbol,const long magic,
                            const datetime server_time,string &error)
{
   const int key=ServerDateKey(server_time);
   if(g_own_realized_ready && g_own_realized_date_key==key)
   {
      error="";
      return true;
   }
   return RefreshOwnRealizedToday(symbol,magic,server_time,error);
}

double CalculateOwnFloatingPnL(const string symbol,const long magic)
{
   // 净额/交易所账户的同品种持仓会被合并，POSITION_MAGIC不能可靠隔离多个EA。
   // 为保证本EA绝不受其他EA共享持仓影响，这两种模式不计入浮动盈亏。
   if(!UseOwnFloatingForRiskLock()) return 0.0;

   double net=0.0;
   for(int i=PositionsTotal()-1;i>=0;i--)
   {
      const ulong ticket=PositionGetTicket(i);
      if(ticket==0) continue;
      if(PositionGetString(POSITION_SYMBOL)!=symbol ||
         !V3109C32MagicBelongsToGroup(magic,PositionGetInteger(POSITION_MAGIC)))
         continue;

      net+=PositionGetDouble(POSITION_PROFIT)+PositionGetDouble(POSITION_SWAP);
   }
   return net;
}

class CRiskManager
{
private:
   int    m_date_key;
   double m_start_equity;
   double m_start_strategy_net;
   double m_strategy_change;
   double m_daily_loss_limit;
   int    m_consecutive_losses;
   bool   m_daily_locked;
   bool   m_loss_locked;

public:
   CRiskManager() { ResetSession(); }

   void ResetSession()
   {
      m_date_key=0;
      m_start_equity=0.0;
      m_start_strategy_net=0.0;
      m_strategy_change=0.0;
      m_daily_loss_limit=0.0;
      m_consecutive_losses=0;
      m_daily_locked=false;
      m_loss_locked=false;
   }

   bool UpdateDailyLock(const datetime server_time,const double session_equity,
                        const double strategy_net)
   {
      const int key=ServerDateKey(server_time);
      if(key!=m_date_key)
      {
         m_date_key=key;
         m_start_equity=session_equity;
         m_start_strategy_net=strategy_net;
         m_strategy_change=0.0;
         m_daily_loss_limit=(m_start_equity>0.0 ? m_start_equity*0.02 : 0.0);
         m_consecutive_losses=0;
         m_daily_locked=false;
         m_loss_locked=false;
      }

      // 只比较本EA自身损益相对本次启动/换日基线的变化。
      // 其他EA或手动交易造成的账户净值变化不再参与锁定判断。
      m_strategy_change=strategy_net-m_start_strategy_net;
      if(m_daily_loss_limit>0.0 && m_strategy_change<=-m_daily_loss_limit)
         m_daily_locked=true;
      return IsEntryLocked();
   }

   bool RegisterClosedTradeGroup(const double net_result)
   {
      if(net_result<0.0) m_consecutive_losses++;
      else m_consecutive_losses=0;
      if(m_consecutive_losses>=3) m_loss_locked=true;
      return IsEntryLocked();
   }

   bool IsEntryLocked() const { return m_daily_locked || m_loss_locked; }
   bool IsDailyLocked() const { return m_daily_locked; }
   bool IsConsecutiveLossLocked() const { return m_loss_locked; }
   int ConsecutiveLosses() const { return m_consecutive_losses; }
   double StartEquity() const { return m_start_equity; }
   double StrategyChange() const { return m_strategy_change; }
   double DailyLossLimit() const { return m_daily_loss_limit; }

   bool CheckSpread(const double bid,const double ask,const double atr,
                    const double max_atr_ratio,const int absolute_points,
                    const double point,string &error) const
   {
      error="";
      if(bid<=0.0 || ask<=bid || atr<=0.0 || max_atr_ratio<=0.0 || point<=0.0)
      {
         error="Spread输入无效";
         return false;
      }
      const double spread=ask-bid;
      if(spread>max_atr_ratio*atr)
      {
         error="动态Spread超过ATR限制";
         return false;
      }
      if(absolute_points>0 && spread>absolute_points*point)
      {
         error="Spread超过绝对点数限制";
         return false;
      }
      return true;
   }

   bool CheckPostAIMove(const ENUM_TRADE_DIRECTION direction,
                        const double planned_entry,const double current_price,
                        const double atr,const double max_move_atr,
                        string &error) const
   {
      error="";
      if(atr<=0.0 || max_move_atr<0.0)
      {
         error="AI后行情复检输入无效";
         return false;
      }
      const double move=(direction==DIR_BUY ? current_price-planned_entry :
                                               planned_entry-current_price);
      if(move>max_move_atr*atr)
      {
         error="AI返回后价格已过度延伸";
         return false;
      }
      return true;
   }

   bool HasManagedExposure(const string symbol,const long magic) const
   {
      for(int i=PositionsTotal()-1;i>=0;i--)
      {
         const ulong ticket=PositionGetTicket(i);
         if(ticket>0 && PositionGetString(POSITION_SYMBOL)==symbol &&
            PositionGetInteger(POSITION_MAGIC)==magic)
            return true;
      }
      for(int i=OrdersTotal()-1;i>=0;i--)
      {
         const ulong ticket=OrderGetTicket(i);
         if(ticket>0 && OrderGetString(ORDER_SYMBOL)==symbol &&
            OrderGetInteger(ORDER_MAGIC)==magic)
            return true;
      }
      return false;
   }
};

#endif
// ===== END INLINE: Include/XAUAI/RiskManager.mqh =====

// ===== BEGIN INLINE: Include/XAUAI/WeekendRiskGuard.mqh =====
#ifndef XAUAI_WEEKEND_RISK_GUARD_MQH
#define XAUAI_WEEKEND_RISK_GUARD_MQH

struct WeekendGuardStatus
{
   bool     enabled;
   bool     schedule_available;
   bool     used_fallback;
   bool     weekend;
   bool     no_new_entries;
   bool     force_flat;
   datetime friday_close;
   datetime no_new_entry_time;
   datetime force_close_time;
   string   reason;
};

datetime StartOfServerDay(const datetime value)
{
   if(value<=0) return 0;
   const long seconds_per_day=86400;
   return (datetime)((((long)value)/seconds_per_day)*seconds_per_day);
}

ENUM_DAY_OF_WEEK StableServerDayOfWeek(const datetime day_midnight)
{
   // 1970.01.01是周四（ENUM_DAY_OF_WEEK中的4）。直接用固定锚点计算，
   // 避免部分终端环境中MqlDateTime.day_of_week返回异常而把工作日误判为周末。
   const long seconds_per_day=86400;
   const long day_index=((long)day_midnight)/seconds_per_day;
   int anchored_day=(int)((day_index+4)%7);
   if(anchored_day<0) anchored_day+=7;
   return (ENUM_DAY_OF_WEEK)anchored_day;
}

datetime StartOfCurrentTradingWeek(const datetime now)
{
   if(now<=0) return 0;

   // 以1970.01.01（星期四）为固定锚点直接按天数计算周一边界。
   // 不再依赖MqlDateTime.day_of_week，避免策略测试器中周起点退化成“每日00:00”。
   const long seconds_per_day=86400;
   const long day_index=((long)now)/seconds_per_day;
   const datetime day_start=(datetime)(day_index*seconds_per_day);
   const int days_since_monday=(int)((day_index+3)%7); // 1970.01.01是周四，距周一3天
   return (datetime)((long)day_start-(long)days_since_monday*seconds_per_day);
}

bool ResolveFridayMarketClose(const string symbol,const datetime now,
                              const int fallback_close_hour,
                              const int fallback_close_minute,
                              datetime &friday_close,bool &used_fallback,
                              string &reason)
{
   reason="";
   used_fallback=false;
   friday_close=0;

   // 周五日期直接锚定到已验证过的“当前交易周周一00:00”算法。
   // 不再用day_of_week做相对位移，避免测试器/环境异常导致周五日期被推到错误的一天。
   const long seconds_per_day=86400;
   const datetime week_start=StartOfCurrentTradingWeek(now);
   if(week_start<=0)
   {
      used_fallback=true;
      reason="当前服务器时间无效，暂无法计算周末风控时段";
      return false;
   }
   const datetime friday_midnight=(datetime)((long)week_start+4*seconds_per_day);
   const int fallback_seconds=fallback_close_hour*3600+fallback_close_minute*60;

   int latest_end_seconds=-1;
   for(uint session_index=0;session_index<32;session_index++)
   {
      datetime session_from=0;
      datetime session_to=0;
      ResetLastError();
      if(!SymbolInfoSessionTrade(symbol,FRIDAY,session_index,session_from,session_to)) break;

      // SymbolInfoSessionTrade只使用一天内的时分秒；日期部分必须忽略。
      int from_seconds=(int)(((long)session_from)%seconds_per_day);
      int to_seconds=(int)(((long)session_to)%seconds_per_day);
      if(from_seconds<0) from_seconds+=(int)seconds_per_day;
      if(to_seconds<0) to_seconds+=(int)seconds_per_day;
      if(to_seconds<=from_seconds) to_seconds+=(int)seconds_per_day;
      if(to_seconds>latest_end_seconds) latest_end_seconds=to_seconds;
   }

   // V3.9.9只要读取到任意Session就直接信任动态结果。
   // 本次增加最终结果校验：动态周五休市只允许落在周五00:00到周六12:00之间，
   // 且必须能被正常格式化；否则立即使用23:55等用户设定的备用休市点。
   bool dynamic_schedule_valid=(latest_end_seconds>0 && latest_end_seconds<=36*3600);
   datetime dynamic_close=0;
   if(dynamic_schedule_valid)
   {
      dynamic_close=(datetime)((long)friday_midnight+(long)latest_end_seconds);
      const string dynamic_text=TimeToString(dynamic_close,TIME_DATE|TIME_SECONDS);
      if(dynamic_close<=friday_midnight ||
         dynamic_close>(datetime)((long)friday_midnight+36*3600) ||
         dynamic_text=="")
         dynamic_schedule_valid=false;
   }

   if(!dynamic_schedule_valid)
   {
      used_fallback=true;
      friday_close=(datetime)((long)friday_midnight+(long)fallback_seconds);
      reason=(latest_end_seconds<0 ?
              "无法读取周五交易时段，已使用服务器时间备用休市点" :
              "周五动态交易时段结果无效，已使用服务器时间备用休市点");
   }
   else
   {
      friday_close=dynamic_close;
      reason="已读取并校验交易品种周五最后交易时段";
   }

   return friday_close>friday_midnight;
}

struct DailyReviewSession
{
   bool     available;
   bool     used_fallback;
   string   review_key;
   datetime session_open;
   datetime session_close;
   datetime ready_server;
   string   reason;
};

bool ResolveDailyReviewSession(const string symbol,const datetime reference_day,
                               const int delay_minutes,
                               const int fallback_close_hour,
                               const int fallback_close_minute,
                               DailyReviewSession &session)
{
   ZeroMemory(session);
   session.available=false;
   session.used_fallback=false;
   if(reference_day<=0) return false;

   const long seconds_per_day=86400;
   const datetime day_midnight=StartOfServerDay(reference_day);
   const ENUM_DAY_OF_WEEK day_of_week=StableServerDayOfWeek(day_midnight);
   if(day_of_week==SATURDAY || day_of_week==SUNDAY)
   {
      session.reason="周末无交易时段，不生成每日复盘触发";
      return false;
   }

   int earliest_start_seconds=-1;
   int latest_end_seconds=-1;
   for(uint session_index=0;session_index<32;session_index++)
   {
      datetime session_from=0;
      datetime session_to=0;
      ResetLastError();
      if(!SymbolInfoSessionTrade(symbol,day_of_week,session_index,
                                 session_from,session_to)) break;
      int from_seconds=(int)(((long)session_from)%seconds_per_day);
      int to_seconds=(int)(((long)session_to)%seconds_per_day);
      if(from_seconds<0) from_seconds+=(int)seconds_per_day;
      if(to_seconds<0) to_seconds+=(int)seconds_per_day;
      if(to_seconds<=from_seconds) to_seconds+=(int)seconds_per_day;
      if(earliest_start_seconds<0 || from_seconds<earliest_start_seconds)
         earliest_start_seconds=from_seconds;
      if(to_seconds>latest_end_seconds) latest_end_seconds=to_seconds;
   }

   const bool dynamic_valid=(earliest_start_seconds>=0 && latest_end_seconds>0 &&
                             latest_end_seconds<=36*3600 &&
                             latest_end_seconds>earliest_start_seconds);
   if(dynamic_valid)
   {
      session.session_open=(datetime)((long)day_midnight+earliest_start_seconds);
      session.session_close=(datetime)((long)day_midnight+latest_end_seconds);
      session.reason="已读取经纪商品种最后交易时段";
   }
   else
   {
      session.used_fallback=true;
      session.session_open=day_midnight;
      session.session_close=(datetime)((long)day_midnight+
         fallback_close_hour*3600+fallback_close_minute*60);
      session.reason="无法读取经纪商每日交易时段，使用服务器备用收盘时间";
   }
   session.ready_server=session.session_close+delay_minutes*60;
   session.review_key=TimeToString(day_midnight,TIME_DATE);
   StringReplace(session.review_key,".","-");
   session.available=(session.session_close>session.session_open &&
                      session.ready_server>session.session_close);
   return session.available;
}

WeekendGuardStatus EvaluateWeekendGuard(const string symbol,const datetime now,
                                         const bool enabled,
                                         const int no_new_trade_minutes,
                                         const int force_close_minutes,
                                         const int fallback_close_hour,
                                         const int fallback_close_minute)
{
   WeekendGuardStatus status;
   status.enabled=enabled;
   status.schedule_available=false;
   status.used_fallback=false;
   status.weekend=false;
   status.no_new_entries=false;
   status.force_flat=false;
   status.friday_close=0;
   status.no_new_entry_time=0;
   status.force_close_time=0;
   status.reason=(enabled ? "周末风控等待计算" : "周末风控已关闭");
   if(!enabled) return status;

   string resolve_reason="";
   status.schedule_available=ResolveFridayMarketClose(symbol,now,
      fallback_close_hour,fallback_close_minute,status.friday_close,
      status.used_fallback,resolve_reason);
   status.no_new_entry_time=status.friday_close-no_new_trade_minutes*60;
   status.force_close_time=status.friday_close-force_close_minutes*60;

   MqlDateTime now_parts;
   TimeToStruct(now,now_parts);
   status.weekend=(now_parts.day_of_week==SATURDAY || now_parts.day_of_week==SUNDAY);
   status.no_new_entries=(status.weekend || now>=status.no_new_entry_time);
   status.force_flat=(status.weekend || now>=status.force_close_time);

   if(status.weekend)
      status.reason="周末休市期间禁止交易并要求清空本EA敞口";
   else if(status.force_flat)
      status.reason="已进入周五强制平仓窗口";
   else if(status.no_new_entries)
      status.reason="已进入周五禁止新开仓窗口";
   else
      status.reason=resolve_reason;
   return status;
}

bool CanOpenNewTradeNow(const WeekendGuardStatus &status)
{
   return (!status.enabled || (!status.no_new_entries && !status.force_flat));
}

string FormatWeekendGuardStatus(const WeekendGuardStatus &status)
{
   if(!status.enabled) return "关闭";
   string mode=(status.used_fallback ? "备用时间" : "动态交易时段");
   string phase="允许开仓";
   if(status.force_flat) phase="强制清仓";
   else if(status.no_new_entries) phase="禁止新开仓";
   return phase+" | "+mode+" | 周五休市="+
          TimeToString(status.friday_close,TIME_DATE|TIME_MINUTES);
}

#endif
// ===== END INLINE: Include/XAUAI/WeekendRiskGuard.mqh =====

// ===== BEGIN INLINE: Include/XAUAI/RolloverRiskGuard.mqh =====
#ifndef XAUAI_ROLLOVER_RISK_GUARD_MQH
#define XAUAI_ROLLOVER_RISK_GUARD_MQH

struct RolloverGuardStatus
{
   bool   enabled;
   bool   block_new_entries;
   int    minute_of_day;
   int    start_minutes;
   int    end_minutes;
   string reason;
};

int ServerMinuteOfDay(const datetime now)
{
   MqlDateTime parts;
   TimeToStruct(now,parts);
   return parts.hour*60+parts.min;
}

bool IsMinuteInsideRolloverWindow(const int minute_of_day,
                                  const int start_minutes,
                                  const int end_minutes)
{
   if(start_minutes==end_minutes) return false;
   if(start_minutes<end_minutes)
      return minute_of_day>=start_minutes && minute_of_day<end_minutes;
   return minute_of_day>=start_minutes || minute_of_day<end_minutes;
}

RolloverGuardStatus EvaluateRolloverGuard(const datetime now,
                                          const bool enabled,
                                          const int start_hour,
                                          const int start_minute,
                                          const int end_hour,
                                          const int end_minute)
{
   RolloverGuardStatus status;
   status.enabled=enabled;
   status.block_new_entries=false;
   status.minute_of_day=ServerMinuteOfDay(now);
   status.start_minutes=start_hour*60+start_minute;
   status.end_minutes=end_hour*60+end_minute;
   status.reason=(enabled ? "服务器换日保护窗口等待计算" : "每日换日风控已关闭");
   if(!enabled) return status;

   status.block_new_entries=IsMinuteInsideRolloverWindow(status.minute_of_day,
                                                         status.start_minutes,
                                                         status.end_minutes);
   status.reason=(status.block_new_entries ?
                  "已进入服务器换日保护窗口，禁止新开仓并删除挂单" :
                  "当前不在服务器换日保护窗口");
   return status;
}

bool CanOpenNewTradeDuringRollover(const RolloverGuardStatus &status)
{
   return (!status.enabled || !status.block_new_entries);
}

string RolloverMinuteLabel(const int total_minutes)
{
   const int normalized=(total_minutes%1440+1440)%1440;
   return StringFormat("%02d:%02d",normalized/60,normalized%60);
}

string FormatRolloverGuardStatus(const RolloverGuardStatus &status)
{
   if(!status.enabled) return "关闭";
   return (status.block_new_entries ? "禁止新开仓" : "允许开仓")+
          " | 窗口="+RolloverMinuteLabel(status.start_minutes)+"-"+
          RolloverMinuteLabel(status.end_minutes)+"（服务器时间）";
}

#endif
// ===== END INLINE: Include/XAUAI/RolloverRiskGuard.mqh =====

// ===== BEGIN INLINE: Include/XAUAI/TradeManager.mqh =====
#ifndef XAUAI_TRADE_MANAGER_MQH
#define XAUAI_TRADE_MANAGER_MQH

#include <Trade/Trade.mqh>
// [单文件合并] 已内联，跳过重复模块: StrategyTypes.mqh
// [单文件合并] 已内联，跳过重复模块: RiskManager.mqh
// [单文件合并] 已内联，跳过重复模块: WeekendRiskGuard.mqh

void ApplyTP1Policy(TradeRuntimeState &state,const double close_percent,
                    const double volume_min,const double volume_step)
{
   const double close_volume=NormalizeVolumeDown(state.remaining_volume*close_percent/100.0,
                                                  volume_step);
   const double remaining=NormalizeVolumeDown(state.remaining_volume-close_volume,volume_step);
   state.tp1_reached=true;
   if(close_volume<volume_min || remaining<volume_min)
   {
      state.final_exit_requested=true;
      state.state=STATE_TP1_REACHED;
      return;
   }
   state.remaining_volume=remaining;
   state.state=STATE_TP1_REACHED;
}

ENUM_EXIT_STAGE NextStagedExitStage(const TradeRuntimeState &state,
                                    const double bid,const double ask)
{
   if(state.exit_mode!=EXIT_MODE_STAGED || state.execution_check_pending ||
      bid<=0.0 || ask<=0.0) return EXIT_STAGE_NONE;
   if(!state.tp1_done && state.tp1_price>0.0)
   {
      const bool reached=(state.direction==DIR_BUY ? bid>=state.tp1_price :
                                                   ask<=state.tp1_price);
      if(reached) return EXIT_STAGE_TP1;
   }
   if(state.tp1_done && !state.tp2_done && state.tp2_price>0.0)
   {
      const bool reached=(state.direction==DIR_BUY ? bid>=state.tp2_price :
                                                   ask<=state.tp2_price);
      if(reached) return EXIT_STAGE_TP2;
   }
   return EXIT_STAGE_NONE;
}

ENUM_EA_STATE RestoredPositionState(const TradeRuntimeState &state)
{
   if(state.exit_mode==EXIT_MODE_STAGED)
   {
      if(state.tp2_done) return STATE_RUNNER;
      if(state.tp1_done) return STATE_TP1_REACHED;
      return STATE_POSITION_OPEN;
   }
   if(state.exit_mode==EXIT_MODE_LEGACY && state.tp1_reached)
      return STATE_TP1_REACHED;
   return STATE_POSITION_OPEN;
}

bool CanTrailToStructureByState(const TradeRuntimeState &state)
{
   if(state.exit_mode==EXIT_MODE_STAGED) return state.tp2_done;
   if(state.exit_mode==EXIT_MODE_LEGACY) return state.tp1_reached;
   return false;
}

bool ApplyConfirmedStageResult(TradeRuntimeState &state,
                               const ENUM_EXIT_STAGE stage,
                               const bool position_exists_after,
                               const double after_volume,string &error)
{
   error="";
   if(!position_exists_after)
   {
      ResetTradeRuntimeState(state);
      return true;
   }
   if(after_volume<=0.0 || state.remaining_volume<=0.0 ||
      after_volume>=state.remaining_volume-1e-10)
   {
      error="实际持仓手数未确认减少";
      return false;
   }
   if(stage==EXIT_STAGE_TP1)
   {
      if(state.exit_mode!=EXIT_MODE_STAGED || state.tp1_done)
      {
         error="TP1阶段状态无效或已经完成";
         return false;
      }
      state.tp1_done=true;
      state.tp1_reached=true;
      state.remaining_volume=after_volume;
      state.break_even_pending=true;
      state.state=STATE_TP1_REACHED;
   }
   else if(stage==EXIT_STAGE_TP2)
   {
      if(state.exit_mode!=EXIT_MODE_STAGED || !state.tp1_done || state.tp2_done)
      {
         error="TP2必须在TP1完成后且只能执行一次";
         return false;
      }
      state.tp2_done=true;
      state.remaining_volume=after_volume;
      state.break_even_pending=false;
      state.tp2_lock_pending=true;
      state.state=STATE_RUNNER;
   }
   else if(stage==EXIT_STAGE_LEGACY_TP1)
   {
      if(state.exit_mode!=EXIT_MODE_LEGACY || state.tp1_reached)
      {
         error="Legacy TP1阶段状态无效或已经完成";
         return false;
      }
      state.tp1_reached=true;
      state.remaining_volume=after_volume;
      state.state=STATE_TP1_REACHED;
   }
   else
   {
      error="未知退出阶段";
      return false;
   }
   state.execution_check_pending=false;
   state.execution_stage=EXIT_STAGE_NONE;
   state.execution_before_volume=0.0;
   state.execution_requested_volume=0.0;
   state.execution_result_order=0;
   state.execution_result_deal=0;
   state.execution_requested_at=0;
   return true;
}

bool ApplyStructureTrail(TradeRuntimeState &state,const ENUM_TRADE_DIRECTION direction,
                          const double structure_price,const double buffer)
{
   if(!state.tp1_reached || structure_price<=0.0 || buffer<0.0) return false;
   const double proposed=(direction==DIR_BUY ? structure_price-buffer : structure_price+buffer);
   const bool improves=(direction==DIR_BUY ? proposed>state.stop_loss :
                                           (state.stop_loss<=0.0 || proposed<state.stop_loss));
   const bool new_structure=(direction==DIR_BUY ?
      structure_price>state.last_tracked_structure :
      (state.last_tracked_structure<=0.0 || structure_price<state.last_tracked_structure));
   if(!improves || !new_structure) return false;
   state.stop_loss=proposed;
   state.last_tracked_structure=structure_price;
   state.state=STATE_RUNNER;
   return true;
}

string SerializeTradeRuntimeState(const TradeRuntimeState &state)
{
   return IntegerToString((int)state.state)+","+state.signal_id+","+
      IntegerToString((int)state.direction)+","+IntegerToString((int)state.order_ticket)+","+
      IntegerToString((int)state.position_id)+","+DoubleToString(state.entry,10)+","+
      DoubleToString(state.initial_sl,10)+","+DoubleToString(state.tp1,10)+","+
      DoubleToString(state.initial_volume,8)+","+DoubleToString(state.remaining_volume,8)+","+
      DoubleToString(state.stop_loss,10)+","+DoubleToString(state.last_tracked_structure,10)+","+
      (state.tp1_reached?"1":"0")+","+(state.final_exit_requested?"1":"0")+","+
      IntegerToString((int)state.pending_created)+","+IntegerToString((int)state.pending_expiry)+","+
      IntegerToString((int)state.signal_route)+","+
      IntegerToString(state.state_version)+","+IntegerToString((int)state.exit_mode)+","+
      DoubleToString(state.risk_distance,10)+","+DoubleToString(state.tp1_price,10)+","+
      DoubleToString(state.tp2_price,10)+","+DoubleToString(state.structure_target,10)+","+
      DoubleToString(state.lock_price,10)+","+
      (state.tp1_done?"1":"0")+","+(state.tp2_done?"1":"0")+","+
      (state.break_even_done?"1":"0")+","+(state.break_even_pending?"1":"0")+","+
      (state.tp2_lock_done?"1":"0")+","+(state.tp2_lock_pending?"1":"0")+","+
      (state.server_tp_done?"1":"0")+","+(state.server_tp_pending?"1":"0")+","+
      (state.execution_check_pending?"1":"0")+","+
      IntegerToString((int)state.execution_stage)+","+
      DoubleToString(state.execution_before_volume,8)+","+
      DoubleToString(state.execution_requested_volume,8)+","+
      IntegerToString((long)state.execution_result_order)+","+
      IntegerToString((long)state.execution_result_deal)+","+
      IntegerToString((long)state.execution_requested_at)+","+
      (state.runner_stop_pending?"1":"0")+","+
      DoubleToString(state.runner_pending_structure,10)+","+
      DoubleToString(state.runner_pending_buffer,10)+","+
      DoubleToString(state.legacy_sl,10)+","+
      DoubleToString(state.exit_unit,10)+","+
      IntegerToString((int)state.stop_mode)+","+
      DoubleToString(state.expansion_ratio,10);
}

bool ParseTradeRuntimeState(const string line,TradeRuntimeState &state)
{
   ResetTradeRuntimeState(state);
   string f[];
   const int count=StringSplit(line,',',f);
   if(count<16) return false;

   state.state=(ENUM_EA_STATE)StringToInteger(f[0]);
   state.signal_id=f[1];
   state.direction=(ENUM_TRADE_DIRECTION)StringToInteger(f[2]);
   state.order_ticket=(ulong)StringToInteger(f[3]);
   state.position_id=(ulong)StringToInteger(f[4]);
   state.entry=StringToDouble(f[5]);
   state.initial_sl=StringToDouble(f[6]);
   state.tp1=StringToDouble(f[7]);
   state.initial_volume=StringToDouble(f[8]);
   state.remaining_volume=StringToDouble(f[9]);
   state.stop_loss=StringToDouble(f[10]);
   state.last_tracked_structure=StringToDouble(f[11]);
   state.tp1_reached=(f[12]=="1");
   state.final_exit_requested=(f[13]=="1");
   state.pending_created=(datetime)StringToInteger(f[14]);
   state.pending_expiry=(datetime)StringToInteger(f[15]);

   state.signal_route=SIGNAL_ROUTE_NONE;
   if(count>=17)
   {
      const int route_value=(int)StringToInteger(f[16]);
      const bool canonical_route=(f[16]==IntegerToString(route_value));
      if(canonical_route && route_value>=SIGNAL_ROUTE_FIB_PA &&
         route_value<=SIGNAL_ROUTE_STRATEGY01_H2)
         state.signal_route=(ENUM_SIGNAL_ROUTE)route_value;
   }
   state.state_version=1;
   state.exit_mode=EXIT_MODE_LEGACY;
   state.structure_target=state.tp1;
   if(count>=18) state.state_version=(int)StringToInteger(f[17]);
   if(count>=19)
   {
      const int mode=(int)StringToInteger(f[18]);
      if(mode>=EXIT_MODE_LEGACY && mode<=EXIT_MODE_PROTECT_ONLY)
         state.exit_mode=(ENUM_EXIT_MODE)mode;
      else
         state.exit_mode=EXIT_MODE_PROTECT_ONLY;
   }
   if(count>=20) state.risk_distance=StringToDouble(f[19]);
   if(count>=21) state.tp1_price=StringToDouble(f[20]);
   if(count>=22) state.tp2_price=StringToDouble(f[21]);
   if(count>=23) state.structure_target=StringToDouble(f[22]);
   if(count>=24) state.lock_price=StringToDouble(f[23]);
   if(count>=25) state.tp1_done=(f[24]=="1");
   if(count>=26) state.tp2_done=(f[25]=="1");
   if(count>=27) state.break_even_done=(f[26]=="1");
   if(count>=28) state.break_even_pending=(f[27]=="1");
   if(count>=29) state.tp2_lock_done=(f[28]=="1");
   if(count>=30) state.tp2_lock_pending=(f[29]=="1");
   if(count>=31) state.server_tp_done=(f[30]=="1");
   if(count>=32) state.server_tp_pending=(f[31]=="1");
   if(count>=33) state.execution_check_pending=(f[32]=="1");
   if(count>=34) state.execution_stage=(ENUM_EXIT_STAGE)StringToInteger(f[33]);
   if(count>=35) state.execution_before_volume=StringToDouble(f[34]);
   if(count>=36) state.execution_requested_volume=StringToDouble(f[35]);
   if(count>=37) state.execution_result_order=(ulong)StringToInteger(f[36]);
   if(count>=38) state.execution_result_deal=(ulong)StringToInteger(f[37]);
   if(count>=39) state.execution_requested_at=(datetime)StringToInteger(f[38]);
   if(count>=40) state.runner_stop_pending=(f[39]=="1");
   if(count>=41) state.runner_pending_structure=StringToDouble(f[40]);
   if(count>=42) state.runner_pending_buffer=StringToDouble(f[41]);
   if(count>=43) state.legacy_sl=StringToDouble(f[42]);
   if(count>=44) state.exit_unit=StringToDouble(f[43]);
   if(count>=45)
   {
      const int sm=(int)StringToInteger(f[44]);
      state.stop_mode=(sm>=STOP_MODE_LEGACY_ONLY && sm<=STOP_MODE_LEGACY_FALLBACK ?
                       (ENUM_STOP_MODE)sm : STOP_MODE_LEGACY_ONLY);
   }
   if(count>=46) state.expansion_ratio=StringToDouble(f[45]);
   if(count>17 && count<39) state.exit_mode=EXIT_MODE_PROTECT_ONLY;
   return true;
}

const int STAGE_CLOSE_MARKET_CLOSED_RETRY_SECONDS=60;

bool CanInitializeFilledState(const ENUM_EA_STATE state,
                              const double recorded_volume,
                              const double current_volume,
                              const double volume_step)
{
   if(state==STATE_PENDING_ORDER) return true;
   if(state!=STATE_POSITION_OPEN || current_volume<=0.0 ||
      !MathIsValidNumber(recorded_volume) || !MathIsValidNumber(current_volume))
      return false;
   const double epsilon=(volume_step>0.0 && MathIsValidNumber(volume_step) ?
                         volume_step*0.5 : 1e-8);
   return current_volume>recorded_volume+epsilon;
}

class CTradeManager
{
private:
   CTrade            m_trade;
   string            m_symbol;
   long              m_magic;
   TradeRuntimeState m_state;
   string            m_state_file;
   bool              m_state_file_loaded;
   bool              m_state_file_valid;
   bool              m_use_staged_exit;
   double            m_tp1_r_multiple;
   double            m_tp1_close_percent;
   bool              m_move_break_even;
   double            m_break_even_spread_multiple;
   int               m_break_even_min_points;
   double            m_tp2_r_multiple;
   double            m_tp2_close_percent;
   double            m_tp2_lock_multiple;
   bool              m_use_structure_server_tp;
   datetime          m_stage_close_retry_after;
   ENUM_EXIT_STAGE   m_stage_close_retry_stage;

   void ResetStageCloseRetry()
   {
      m_stage_close_retry_after=0;
      m_stage_close_retry_stage=EXIT_STAGE_NONE;
   }

   void ArmStageCloseRetry(const ENUM_EXIT_STAGE stage,const int retry_seconds)
   {
      m_stage_close_retry_stage=stage;
      m_stage_close_retry_after=TimeCurrent()+retry_seconds;
   }

   void ResetManagedPositionSnapshot(ManagedPositionSnapshot &snapshot)
   {
      ZeroMemory(snapshot);
   }

   bool SelectManagedPosition(const ulong preferred_identifier,
                              ManagedPositionSnapshot &snapshot,string &error)
   {
      ResetManagedPositionSnapshot(snapshot);
      error="";
      int matches=0;
      for(int i=PositionsTotal()-1;i>=0;i--)
      {
         const ulong ticket=PositionGetTicket(i);
         if(ticket==0 || PositionGetString(POSITION_SYMBOL)!=m_symbol ||
            PositionGetInteger(POSITION_MAGIC)!=m_magic) continue;
         const ulong identifier=(ulong)PositionGetInteger(POSITION_IDENTIFIER);
         if(preferred_identifier!=0 && identifier!=preferred_identifier) continue;
         matches++;
         snapshot.valid=true;
         snapshot.ticket=ticket;
         snapshot.identifier=identifier;
         snapshot.type=(ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE);
         snapshot.volume=PositionGetDouble(POSITION_VOLUME);
         snapshot.entry=PositionGetDouble(POSITION_PRICE_OPEN);
         snapshot.sl=PositionGetDouble(POSITION_SL);
         snapshot.tp=PositionGetDouble(POSITION_TP);
      }
      if(matches==1) return true;
      ResetManagedPositionSnapshot(snapshot);
      if(matches>1) error="发现多个无法唯一识别的本EA持仓";
      else if(preferred_identifier!=0) error="未找到状态文件指定的Position Identifier";
      else error="未找到本EA持仓";
      return false;
   }

   bool IsCloseRetcodeAccepted(const uint retcode) const
   {
      return retcode==TRADE_RETCODE_DONE || retcode==TRADE_RETCODE_DONE_PARTIAL;
   }

   bool IsAmbiguousRetcode(const uint retcode) const
   {
      return retcode==0 || retcode==TRADE_RETCODE_TIMEOUT ||
             retcode==TRADE_RETCODE_CONNECTION || retcode==TRADE_RETCODE_TOO_MANY_REQUESTS;
   }

   int ManagedPositionCount()
   {
      int count=0;
      for(int i=PositionsTotal()-1;i>=0;i--)
      {
         const ulong ticket=PositionGetTicket(i);
         if(ticket>0 && PositionGetString(POSITION_SYMBOL)==m_symbol &&
            PositionGetInteger(POSITION_MAGIC)==m_magic) count++;
      }
      return count;
   }

   bool HasMatchingExitDealSince(const datetime requested_at)
   {
      if(requested_at<=0 || !HistorySelect(requested_at-5,TimeCurrent())) return false;
      for(int i=HistoryDealsTotal()-1;i>=0;i--)
      {
         const ulong deal=HistoryDealGetTicket(i);
         if(deal==0 || HistoryDealGetString(deal,DEAL_SYMBOL)!=m_symbol ||
            HistoryDealGetInteger(deal,DEAL_MAGIC)!=m_magic ||
            (ulong)HistoryDealGetInteger(deal,DEAL_POSITION_ID)!=m_state.position_id)
            continue;
         const long entry=HistoryDealGetInteger(deal,DEAL_ENTRY);
         if((entry==DEAL_ENTRY_OUT || entry==DEAL_ENTRY_OUT_BY) &&
            (datetime)HistoryDealGetInteger(deal,DEAL_TIME)>=requested_at)
            return true;
      }
      return false;
   }

   string ExitStageLabel(const ENUM_EXIT_STAGE stage) const
   {
      if(stage==EXIT_STAGE_TP1) return "TP1-1R";
      if(stage==EXIT_STAGE_TP2) return "TP2-2R";
      if(stage==EXIT_STAGE_LEGACY_TP1) return "Legacy-TP1";
      return "未知阶段";
   }

   void SaveState()
   {
      const int h=FileOpen(m_state_file,FILE_WRITE|FILE_TXT|FILE_ANSI,0,CP_UTF8);
      if(h==INVALID_HANDLE) return;
      const string line=SerializeTradeRuntimeState(m_state);
      FileWriteString(h,line); FileFlush(h); FileClose(h);
      m_state_file_loaded=true;
      m_state_file_valid=true;
   }

   void LoadState()
   {
      m_state_file_loaded=false;
      m_state_file_valid=false;
      const int h=FileOpen(m_state_file,FILE_READ|FILE_TXT|FILE_ANSI|FILE_SHARE_READ,0,CP_UTF8);
      if(h==INVALID_HANDLE) return;
      m_state_file_loaded=true;
      const string line=FileReadString(h); FileClose(h);
      m_state_file_valid=ParseTradeRuntimeState(line,m_state);
   }

public:
   bool Init(const string symbol,const long magic,const bool use_staged_exit,
             const double tp1_r_multiple,const double tp1_close_percent,
             const bool move_break_even,const double break_even_spread_multiple,
             const int break_even_min_points,const double tp2_r_multiple,
             const double tp2_close_percent,const double tp2_lock_multiple,
             const bool use_structure_server_tp)
   {
      m_symbol=symbol; m_magic=magic;
      m_use_staged_exit=use_staged_exit;
      m_tp1_r_multiple=tp1_r_multiple;
      m_tp1_close_percent=tp1_close_percent;
      m_move_break_even=move_break_even;
      m_break_even_spread_multiple=break_even_spread_multiple;
      m_break_even_min_points=break_even_min_points;
      m_tp2_r_multiple=tp2_r_multiple;
      m_tp2_close_percent=tp2_close_percent;
      m_tp2_lock_multiple=tp2_lock_multiple;
      m_use_structure_server_tp=use_structure_server_tp;
      ResetStageCloseRetry();
      m_state_file="XAUAI_RuntimeState_"+symbol+"_"+IntegerToString((int)magic)+".csv";
      m_trade.SetExpertMagicNumber(magic);
      m_trade.SetTypeFillingBySymbol(symbol);
      ResetTradeRuntimeState(m_state);
      LoadState();
      return true;
   }

   TradeRuntimeState State() const { return m_state; }

   bool IsExitStageRetryBlocked(const ENUM_EXIT_STAGE stage) const
   {
      return (stage!=EXIT_STAGE_NONE &&
              m_stage_close_retry_stage==stage &&
              m_stage_close_retry_after>0 &&
              TimeCurrent()<m_stage_close_retry_after);
   }

   void MarkFilled()
   {
      ManagedPositionSnapshot position;
      string select_error="";
      if(!SelectManagedPosition(m_state.position_id,position,select_error))
      {
         if(m_state.position_id==0 && SelectManagedPosition(0,position,select_error)) {}
         else
         {
            Print("[高优先级警告] 成交后无法唯一读取本EA持仓：",select_error);
            m_state.exit_mode=EXIT_MODE_PROTECT_ONLY;
            SaveState();
            return;
         }
      }
      const double volume_step=SymbolInfoDouble(m_symbol,SYMBOL_VOLUME_STEP);
      if(!CanInitializeFilledState(m_state.state,m_state.initial_volume,
                                   position.volume,volume_step)) return;
      ResetStageCloseRetry();
      const TradeRuntimeState saved=m_state;
      m_state.state=STATE_POSITION_OPEN;
      m_state.position_id=position.identifier;
      m_state.entry=position.entry;
      m_state.initial_sl=position.sl;
      m_state.stop_loss=position.sl;
      m_state.remaining_volume=position.volume;
      m_state.initial_volume=position.volume;
      m_state.direction=(position.type==POSITION_TYPE_BUY ? DIR_BUY : DIR_SELL);
      RecordServerSLEvent("AFTER_FILL",m_state.signal_id,m_state.position_id,
                          m_state.direction,m_state.initial_volume,
                          saved.initial_sl,saved.initial_sl,0.0,0.0,m_state.stop_loss,
                          false,0,"");
      Print("[服务器SL确认] Position=",IntegerToString((long)m_state.position_id),
            " | Source=AFTER_FILL",
            " | PlannedSL=",DoubleToString(saved.initial_sl,_Digits),
            " | SubmittedSL=",DoubleToString(saved.initial_sl,_Digits),
            " | ServerSL=",DoubleToString(m_state.stop_loss,_Digits));
      m_state.structure_target=(saved.structure_target>0.0 ? saved.structure_target : saved.tp1);
      m_state.tp1=m_state.structure_target;
       m_state.exit_mode=saved.exit_mode;
       if(m_state.exit_mode==EXIT_MODE_UNKNOWN)
       {
          m_state.exit_mode=EXIT_MODE_PROTECT_ONLY;
          Print("[高优先级警告] 成交来源状态不明确，持仓进入保护模式；不猜测Legacy或分段进度");
       }
      if(m_state.exit_mode==EXIT_MODE_STAGED)
      {
          const double tick_size=SymbolInfoDouble(m_symbol,SYMBOL_TRADE_TICK_SIZE);
          string plan_error="";
          const ENUM_EXIT_DISTANCE_MODE exit_dist_mode=ExitDistanceModeFor(m_state.stop_mode);
          // V310_ACTUAL_R_FROM_FILL_AND_FINAL_STOP: R=实际成交价-冻结FinalStop。
          const bool use_frozen=false;
          if(!CalculateFrozenExitPrices(m_state.direction,m_state.entry,m_state.initial_sl,
              use_frozen,m_state.exit_unit,
              m_tp1_r_multiple,m_tp2_r_multiple,m_tp2_lock_multiple,tick_size,
             m_state.risk_distance,m_state.tp1_price,m_state.tp2_price,
             m_state.lock_price,plan_error))
         {
            m_state.exit_mode=EXIT_MODE_PROTECT_ONLY;
            Print("[高优先级警告] 无法根据实际成交数据安全建立分段止盈：",plan_error,
                  "；仅保留服务器SL/TP");
         }
         else
         {
            m_state.server_tp_pending=(m_use_structure_server_tp &&
               IsStructureTargetBeyondActualTP2(m_state.direction,m_state.structure_target,
                                                 m_state.tp2_price,tick_size));
            if(m_use_structure_server_tp && !m_state.server_tp_pending)
               Print("[服务器TP警告] Swing目标未处于实际2R之外，保持TP=0");
            Print("[止盈计划] Entry=",DoubleToString(m_state.entry,_Digits),
                  " | InitialSL=",DoubleToString(m_state.initial_sl,_Digits),
                  " | 1R=",DoubleToString(m_state.tp1_price,_Digits),
                  " | 2R=",DoubleToString(m_state.tp2_price,_Digits),
                  " | StructureTarget=",DoubleToString(m_state.structure_target,_Digits),
                  " | InitialVolume=",DoubleToString(m_state.initial_volume,2));
            Print("[出场距离初始化] SignalID=",m_state.signal_id,
                  " | ActualEntry=",DoubleToString(m_state.entry,_Digits),
                  " | InitialSL=",DoubleToString(m_state.initial_sl,_Digits),
                  " | LegacySL=",DoubleToString(m_state.legacy_sl,_Digits),
                  " | ProtectiveSL=",DoubleToString(m_state.initial_sl,_Digits),
                  " | StopMode=",StopModeLabel(m_state.stop_mode),
                  " | ExitDistanceMode=",ExitDistanceModeLabel(exit_dist_mode),
                  " | ExitUnitSource=ACTUAL_ENTRY_TO_FINAL_STOP",
                  " | TPAnchor=ACTUAL_ENTRY",
                  " | FrozenExitUnit=",DoubleToString(m_state.exit_unit,_Digits),
                  " | ActualRiskDistance=",DoubleToString(m_state.risk_distance,_Digits),
                  " | TP1=",DoubleToString(m_state.tp1_price,_Digits),
                  " | TP2=",DoubleToString(m_state.tp2_price,_Digits),
                  " | LockDistance=",DoubleToString(m_state.lock_price,_Digits));
         }
      }
      SaveState();
   }

   bool SyncPendingState(string &event_message)
   {
      event_message="";
      if(m_state.state!=STATE_PENDING_ORDER) return true;

      const ulong ticket=m_state.order_ticket;
      if(ticket==0)
      {
         ResetTradeRuntimeState(m_state);
         SaveState();
         event_message="[状态同步] 挂单Ticket为空，已清除本地挂单状态";
         return true;
      }

      if(!OrderSelect(ticket))
      {
         if(PositionSelect(m_symbol) && PositionGetInteger(POSITION_MAGIC)==m_magic)
         {
            MarkFilled();
            event_message="[状态同步] 挂单已成交，已切换为持仓状态";
            return true;
         }

         // 订单已从活动池移除但Deal事件可能尚未到达。历史状态为成交时先保留
         // signal_id、TP1等元数据，等待DEAL_ADD完成持仓状态切换。
         if(HistoryOrderSelect(ticket))
         {
            const ENUM_ORDER_STATE history_state=
               (ENUM_ORDER_STATE)HistoryOrderGetInteger(ticket,ORDER_STATE);
            if(history_state==ORDER_STATE_FILLED || history_state==ORDER_STATE_PARTIAL)
            {
               event_message="[状态同步] 挂单已成交，等待成交事件确认持仓";
               return true;
            }
         }

         ResetTradeRuntimeState(m_state);
         SaveState();
         event_message="[状态同步] 活动挂单已不存在，已清除本地挂单状态";
         return true;
      }

      if(OrderGetString(ORDER_SYMBOL)!=m_symbol ||
         OrderGetInteger(ORDER_MAGIC)!=m_magic)
      {
         ResetTradeRuntimeState(m_state);
         SaveState();
         event_message="[状态同步] Ticket不属于本EA，已清除本地挂单状态";
      }
      return true;
   }

   bool Restore()
   {
      const TradeRuntimeState saved=m_state;
      ResetTradeRuntimeState(m_state);
      ManagedPositionSnapshot position;
      string position_error="";
      const ulong preferred=(m_state_file_valid ? saved.position_id : 0);
      if(SelectManagedPosition(preferred,position,position_error))
      {
         if(m_state_file_valid) m_state=saved;
         m_state.position_id=position.identifier;
         m_state.entry=position.entry;
         m_state.stop_loss=position.sl;
         m_state.remaining_volume=position.volume;
         m_state.direction=(position.type==POSITION_TYPE_BUY ? DIR_BUY : DIR_SELL);
         if(!m_state_file_loaded || !m_state_file_valid)
         {
            m_state.exit_mode=EXIT_MODE_PROTECT_ONLY;
            m_state.initial_sl=position.sl;
            m_state.initial_volume=position.volume;
            m_state.state=STATE_POSITION_OPEN;
            Print("[高优先级警告] 状态文件缺失或损坏，已有持仓进入保护模式；",
                  "仅保留服务器SL/TP，不猜测TP1/TP2进度");
         }
         else if(saved.state_version<2 || saved.exit_mode==EXIT_MODE_LEGACY)
         {
            m_state.exit_mode=EXIT_MODE_LEGACY;
            m_state.structure_target=(saved.structure_target>0.0 ? saved.structure_target : saved.tp1);
            m_state.tp1=m_state.structure_target;
            m_state.state=RestoredPositionState(m_state);
         }
         else if(saved.exit_mode==EXIT_MODE_STAGED)
         {
            if(saved.initial_volume<=0.0 || saved.initial_sl<=0.0 ||
               saved.risk_distance<=0.0 || saved.tp1_price<=0.0 || saved.tp2_price<=0.0)
            {
               m_state.exit_mode=EXIT_MODE_PROTECT_ONLY;
               m_state.state=STATE_POSITION_OPEN;
               Print("[高优先级警告] 状态数据不完整，无法安全恢复分段进度；",
                     "仅保留服务器SL/TP");
            }
            else
               m_state.state=RestoredPositionState(m_state);
         }
         else
         {
            m_state.exit_mode=EXIT_MODE_PROTECT_ONLY;
            m_state.state=STATE_POSITION_OPEN;
            Print("[高优先级警告] 退出模式未知，已有持仓进入保护模式");
         }
         SaveState();
         return true;
      }
      if(ManagedPositionCount()>0)
      {
         Print("[高优先级警告] ",position_error,
               "；不自动管理这些持仓，保留各自服务器SL/TP");
         return false;
      }
      for(int i=OrdersTotal()-1;i>=0;i--)
      {
         const ulong ticket=OrderGetTicket(i);
         if(ticket>0 && OrderGetString(ORDER_SYMBOL)==m_symbol &&
            OrderGetInteger(ORDER_MAGIC)==m_magic)
         {
            if(m_state_file_valid) m_state=saved;
            m_state.state=STATE_PENDING_ORDER;
            m_state.order_ticket=ticket;
            m_state.entry=OrderGetDouble(ORDER_PRICE_OPEN);
            m_state.stop_loss=OrderGetDouble(ORDER_SL);
            m_state.pending_expiry=(datetime)OrderGetInteger(ORDER_TIME_EXPIRATION);
            m_state.initial_sl=OrderGetDouble(ORDER_SL);
            m_state.initial_volume=OrderGetDouble(ORDER_VOLUME_INITIAL);
            m_state.remaining_volume=OrderGetDouble(ORDER_VOLUME_CURRENT);
            const ENUM_ORDER_TYPE order_type=(ENUM_ORDER_TYPE)OrderGetInteger(ORDER_TYPE);
            m_state.direction=(order_type==ORDER_TYPE_BUY_STOP ? DIR_BUY : DIR_SELL);
            if(!m_state_file_valid)
            {
               m_state.exit_mode=EXIT_MODE_PROTECT_ONLY;
               Print("[高优先级警告] 挂单存在但状态数据缺失，成交后仅保留服务器SL/TP");
            }
            else if(saved.state_version<2)
            {
               m_state.exit_mode=EXIT_MODE_LEGACY;
               m_state.structure_target=saved.tp1;
            }
            SaveState();
            return true;
         }
      }
      ResetTradeRuntimeState(m_state);
      SaveState();
      return false;
   }

   bool PlacePending(const CandidateSignal &candidate,const double volume,
                     const datetime expiry,string &error)
   {
      error="";
      if(volume<=0.0 || candidate.planned_sl<=0.0 || candidate.planned_entry<=0.0)
      { error="挂单价格、止损或手数无效"; return false; }

      // 下单边界再次读取最新Tick，避免AI审核期间价格变化后仍使用旧Bid/Ask。
      MqlTick latest_tick;
      if(!SymbolInfoTick(m_symbol,latest_tick) || latest_tick.bid<=0.0 || latest_tick.ask<=0.0)
      { error="下单前实时复检失败：无法获取最新Bid/Ask"; return false; }
      const double point=SymbolInfoDouble(m_symbol,SYMBOL_POINT);
      const int stops_level=(int)SymbolInfoInteger(m_symbol,SYMBOL_TRADE_STOPS_LEVEL);
      const double min_pending_distance=MathMax(0.0,(double)stops_level)*point;
      const double tick_size=SymbolInfoDouble(m_symbol,SYMBOL_TRADE_TICK_SIZE);
      const double price_epsilon=MathMax(point,tick_size)*0.1;
      if(candidate.direction==DIR_BUY)
      {
         if(candidate.planned_entry<=latest_tick.ask)
         { error="下单前实时复检：买入挂单价已被Ask达到或越过"; return false; }
         if(candidate.planned_entry+price_epsilon<latest_tick.ask+min_pending_distance)
         { error="下单前实时复检：买入挂单距离不足"; return false; }
      }
      else if(candidate.direction==DIR_SELL)
      {
         if(candidate.planned_entry>=latest_tick.bid)
         { error="下单前实时复检：卖出挂单价已被Bid达到或越过"; return false; }
         if(candidate.planned_entry-price_epsilon>latest_tick.bid-min_pending_distance)
         { error="下单前实时复检：卖出挂单距离不足"; return false; }
      }
      if(tick_size>0.0)
      {
         const double aligned_entry=MathRound(candidate.planned_entry/tick_size)*tick_size;
         if(MathAbs(aligned_entry-candidate.planned_entry)>price_epsilon)
         { error="下单前实时复检：价格对齐后挂单价仍不符合经纪商最小价格步长"; return false; }
      }
      const string comment=StringSubstr(candidate.signal_id,0,31);
      bool placed=false;
      if(candidate.direction==DIR_BUY)
         placed=m_trade.BuyStop(volume,candidate.planned_entry,m_symbol,candidate.planned_sl,
                                0.0,ORDER_TIME_SPECIFIED,expiry,comment);
      else if(candidate.direction==DIR_SELL)
         placed=m_trade.SellStop(volume,candidate.planned_entry,m_symbol,candidate.planned_sl,
                                 0.0,ORDER_TIME_SPECIFIED,expiry,comment);
      const uint retcode=m_trade.ResultRetcode();
      ulong confirmed_order=m_trade.ResultOrder();
      if(confirmed_order==0 || !OrderSelect(confirmed_order))
      {
         confirmed_order=0;
         for(int i=OrdersTotal()-1;i>=0;i--)
         {
            const ulong ticket=OrderGetTicket(i);
            if(ticket>0 && OrderGetString(ORDER_SYMBOL)==m_symbol &&
               OrderGetInteger(ORDER_MAGIC)==m_magic &&
               OrderGetString(ORDER_COMMENT)==comment)
            {
               confirmed_order=ticket;
               break;
            }
         }
      }
      const bool retcode_ok=(retcode==TRADE_RETCODE_DONE || retcode==TRADE_RETCODE_PLACED);
      const bool position_created=(PositionSelect(m_symbol) &&
                                   PositionGetInteger(POSITION_MAGIC)==m_magic &&
                                   PositionGetString(POSITION_COMMENT)==comment);
      if((!placed || !retcode_ok || confirmed_order==0) && !position_created)
      {
         error="挂单结果未确认: CTrade="+(placed?"true":"false")+
               " | Retcode="+IntegerToString((int)retcode)+
               " | ResultOrder="+IntegerToString((long)m_trade.ResultOrder())+
               " | 已按品种、Magic和信号ID核验未找到活动订单/持仓";
         return false;
      }
      ResetTradeRuntimeState(m_state);
      m_state.state=STATE_PENDING_ORDER;
      m_state.signal_id=candidate.signal_id;
      m_state.order_ticket=confirmed_order;
      m_state.direction=candidate.direction;
      m_state.signal_route=candidate.signal_route;
      m_state.exit_mode=(m_use_staged_exit ? EXIT_MODE_STAGED : EXIT_MODE_LEGACY);
      m_state.pending_created=TimeTradeServer();
      m_state.pending_expiry=expiry;
      m_state.entry=candidate.planned_entry;
      m_state.initial_sl=candidate.planned_sl;
      m_state.legacy_sl=candidate.legacy_sl;
      m_state.exit_unit=candidate.exit_unit;
      m_state.stop_mode=candidate.stop_mode;
      m_state.expansion_ratio=candidate.expansion_ratio;
      m_state.stop_loss=candidate.planned_sl;
      m_state.tp1=candidate.tp1;
      m_state.structure_target=candidate.tp1;
      m_state.initial_volume=volume;
      m_state.remaining_volume=volume;
      SaveState();
      if(position_created) MarkFilled();
      return true;
   }

   bool DeleteExpiredPending(const datetime now,string &error)
   {
      error="";
      string sync_message="";
      SyncPendingState(sync_message);
      if(m_state.state!=STATE_PENDING_ORDER || m_state.order_ticket==0 ||
         m_state.pending_expiry==0 || now<m_state.pending_expiry) return true;

      const ulong ticket=m_state.order_ticket;
      if(!OrderSelect(ticket))
      {
         SyncPendingState(sync_message);
         return true;
      }

      const bool request_ok=m_trade.OrderDelete(ticket);
      const uint retcode=m_trade.ResultRetcode();
      const bool still_active=OrderSelect(ticket);
      if(still_active || (!request_ok && retcode!=TRADE_RETCODE_DONE))
      {
         // 请求返回不明确时，先核验活动订单池；确认订单已不存在才允许提交本地状态。
         if(!still_active)
         {
            SyncPendingState(sync_message);
            return true;
         }
         error="删除到期挂单结果未确认: CTrade="+(request_ok?"true":"false")+
               " | Retcode="+IntegerToString((int)retcode)+" "+
               m_trade.ResultRetcodeDescription()+
               "，Ticket="+IntegerToString((int)ticket);
         return false;
      }

      ResetTradeRuntimeState(m_state);
      SaveState();
      return true;
   }

   bool CancelPending(string &error)
   {
      error="";
      string sync_message="";
      SyncPendingState(sync_message);
      if(m_state.state!=STATE_PENDING_ORDER || m_state.order_ticket==0) return true;

      const ulong ticket=m_state.order_ticket;
      if(!OrderSelect(ticket))
      {
         SyncPendingState(sync_message);
         return true;
      }

      const bool request_ok=m_trade.OrderDelete(ticket);
      const uint retcode=m_trade.ResultRetcode();
      const bool still_active=OrderSelect(ticket);
      if(still_active || (!request_ok && retcode!=TRADE_RETCODE_DONE))
      {
         // 取消请求与服务器删除事件可能交错；确认订单已不存在时才视为完成。
         if(!still_active)
         {
            SyncPendingState(sync_message);
            return true;
         }
         error="取消失效挂单结果未确认: CTrade="+(request_ok?"true":"false")+
               " | Retcode="+IntegerToString((int)retcode)+" "+
               m_trade.ResultRetcodeDescription()+
               "，Ticket="+IntegerToString((int)ticket);
         return false;
      }

      ResetTradeRuntimeState(m_state);
      SaveState();
      return true;
   }

   void HandleOrderRemoved(const ulong order_ticket,const ENUM_ORDER_STATE order_state)
   {
      if(order_ticket==0 || m_state.state!=STATE_PENDING_ORDER ||
         m_state.order_ticket!=order_ticket) return;

      // 正常成交统一等待DEAL_ADD执行MarkFilled，避免同一成交重复初始化。
      if(order_state==ORDER_STATE_FILLED || order_state==ORDER_STATE_PARTIAL)
      {
         // 保留挂单元数据，等待紧随其后的DEAL_ADD事件。
         return;
      }
      if(order_state==ORDER_STATE_EXPIRED || order_state==ORDER_STATE_CANCELED ||
         order_state==ORDER_STATE_REJECTED)
      {
         ResetTradeRuntimeState(m_state);
         SaveState();
         return;
      }

      // 防御性回退：再次确认活动池、历史池和持仓，防止经纪商返回非常规状态。
      if(OrderSelect(order_ticket)) return;
      if(HistoryOrderSelect(order_ticket))
      {
         const ENUM_ORDER_STATE history_state=
            (ENUM_ORDER_STATE)HistoryOrderGetInteger(order_ticket,ORDER_STATE);
         if(history_state==ORDER_STATE_FILLED || history_state==ORDER_STATE_PARTIAL)
         {
            // 历史状态已确认成交时同样等待DEAL_ADD；非常规状态仍走下方防御性回退。
            return;
         }
         if(history_state==ORDER_STATE_EXPIRED || history_state==ORDER_STATE_CANCELED ||
            history_state==ORDER_STATE_REJECTED)
         {
            ResetTradeRuntimeState(m_state);
            SaveState();
            return;
         }
      }

      if(PositionSelect(m_symbol) && PositionGetInteger(POSITION_MAGIC)==m_magic)
         MarkFilled();
      else
      {
         ResetTradeRuntimeState(m_state);
         SaveState();
      }
   }


   bool HasManagedPendingOrders()
   {
      for(int i=OrdersTotal()-1;i>=0;i--)
      {
         const ulong ticket=OrderGetTicket(i);
         if(ticket>0 && OrderGetString(ORDER_SYMBOL)==m_symbol &&
            OrderGetInteger(ORDER_MAGIC)==m_magic) return true;
      }
      return false;
   }

   bool HasManagedPositions()
   {
      for(int i=PositionsTotal()-1;i>=0;i--)
      {
         const ulong ticket=PositionGetTicket(i);
         if(ticket>0 && PositionGetString(POSITION_SYMBOL)==m_symbol &&
            PositionGetInteger(POSITION_MAGIC)==m_magic) return true;
      }
      return false;
   }

   bool HasPriorWeekManagedExposure(const datetime now)
   {
      const datetime week_start=StartOfCurrentTradingWeek(now);
      if(week_start<=0) return false;

      for(int i=PositionsTotal()-1;i>=0;i--)
      {
         const ulong ticket=PositionGetTicket(i);
         if(ticket<=0 || PositionGetString(POSITION_SYMBOL)!=m_symbol ||
            PositionGetInteger(POSITION_MAGIC)!=m_magic) continue;

         const datetime position_time=(datetime)PositionGetInteger(POSITION_TIME);
         // POSITION_TIME=0 属于无效/尚未同步时间，不能误判为“上周遗留”。
         if(position_time>0 && position_time<week_start)
         {
            Print("[周末风控诊断] 检测到上周遗留持仓 | Ticket=",ticket,
                  " | PositionTime=",TimeToString(position_time,TIME_DATE|TIME_SECONDS),
                  " | WeekStart=",TimeToString(week_start,TIME_DATE|TIME_SECONDS));
            return true;
         }
      }

      for(int i=OrdersTotal()-1;i>=0;i--)
      {
         const ulong ticket=OrderGetTicket(i);
         if(ticket<=0 || OrderGetString(ORDER_SYMBOL)!=m_symbol ||
            OrderGetInteger(ORDER_MAGIC)!=m_magic) continue;

         const datetime order_setup_time=(datetime)OrderGetInteger(ORDER_TIME_SETUP);
         // 策略测试器/订单刚建立时，时间属性可能暂时为0；0不能参与跨周比较。
         if(order_setup_time>0 && order_setup_time<week_start)
         {
            Print("[周末风控诊断] 检测到上周遗留挂单 | Ticket=",ticket,
                  " | OrderSetupTime=",TimeToString(order_setup_time,TIME_DATE|TIME_SECONDS),
                  " | WeekStart=",TimeToString(week_start,TIME_DATE|TIME_SECONDS));
            return true;
         }
      }
      return false;
   }

   bool CancelAllManagedPending(string &error)
   {
      error="";
      bool all_ok=true;
      for(int i=OrdersTotal()-1;i>=0;i--)
      {
         const ulong ticket=OrderGetTicket(i);
         if(ticket==0 || OrderGetString(ORDER_SYMBOL)!=m_symbol ||
            OrderGetInteger(ORDER_MAGIC)!=m_magic) continue;

         const bool request_ok=m_trade.OrderDelete(ticket);
         const uint retcode=m_trade.ResultRetcode();
         const bool still_active=OrderSelect(ticket);
         if(still_active || (!request_ok && retcode!=TRADE_RETCODE_DONE &&
                             retcode!=TRADE_RETCODE_NO_CHANGES))
         {
            if(!still_active) continue;
            if(error!="") error+=" | ";
            error+="删除风控挂单结果未确认 Ticket="+IntegerToString((int)ticket)+
                   " | CTrade="+(request_ok?"true":"false")+
                   " | Retcode="+IntegerToString((int)retcode)+
                   "，"+m_trade.ResultRetcodeDescription();
            all_ok=false;
         }
      }
      Restore();
      return all_ok;
   }

   bool CloseAllManagedPositions(string &error)
   {
      error="";
      bool all_ok=true;
      for(int i=PositionsTotal()-1;i>=0;i--)
      {
         const ulong ticket=PositionGetTicket(i);
         if(ticket==0 || PositionGetString(POSITION_SYMBOL)!=m_symbol ||
            PositionGetInteger(POSITION_MAGIC)!=m_magic) continue;

         const bool request_ok=m_trade.PositionClose(ticket);
         const uint retcode=m_trade.ResultRetcode();
         const bool still_exists=PositionSelectByTicket(ticket);
         if(still_exists || (!request_ok && retcode!=TRADE_RETCODE_DONE &&
                             retcode!=TRADE_RETCODE_DONE_PARTIAL))
         {
            if(!still_exists) continue;
            if(error!="") error+=" | ";
            error+="周末强制平仓结果未确认 Ticket="+IntegerToString((int)ticket)+
                   " | CTrade="+(request_ok?"true":"false")+
                   " | Retcode="+IntegerToString((int)retcode)+
                   "，"+m_trade.ResultRetcodeDescription();
            all_ok=false;
         }
      }
      Restore();
      return all_ok;
   }

   bool ResolveUncertainExecution(string &event_message)
   {
      event_message="";
      if(!m_state.execution_check_pending) return true;
      ManagedPositionSnapshot after;
      string select_error="";
      const bool exists=SelectManagedPosition(m_state.position_id,after,select_error);
      if(!exists && ManagedPositionCount()==0)
      {
         TradeRuntimeState proposed=m_state;
         proposed.remaining_volume=m_state.execution_before_volume;
         string apply_error="";
         const ENUM_EXIT_STAGE stage=m_state.execution_stage;
         if(!ApplyConfirmedStageResult(proposed,stage,false,0.0,apply_error))
         {
            event_message="[退出执行异常] 无法提交已确认的全部平仓："+apply_error;
            return false;
         }
         m_state=proposed;
         SaveState();
         event_message="["+ExitStageLabel(stage)+"] 已确认持仓全部关闭";
         return true;
      }
      if(exists && IsConfirmedVolumeReduction(m_state.execution_before_volume,true,
                                               after.volume,
                                               SymbolInfoDouble(m_symbol,SYMBOL_VOLUME_STEP)))
      {
         TradeRuntimeState proposed=m_state;
         proposed.remaining_volume=m_state.execution_before_volume;
         string apply_error="";
         const ENUM_EXIT_STAGE stage=m_state.execution_stage;
         if(!ApplyConfirmedStageResult(proposed,stage,true,after.volume,apply_error))
         {
            event_message="[退出执行异常] 无法提交已确认的减仓："+apply_error;
            return false;
         }
         m_state=proposed;
         SaveState();
         event_message="["+ExitStageLabel(stage)+"] 延迟核验确认减仓 | Remaining="+
                       DoubleToString(after.volume,2);
         return true;
      }
      const bool exit_deal=HasMatchingExitDealSince(m_state.execution_requested_at);
      const bool order_known=(m_state.execution_result_order>0 &&
                              (OrderSelect(m_state.execution_result_order) ||
                               HistoryOrderSelect(m_state.execution_result_order)));
      if(!exit_deal && !order_known && m_state.execution_requested_at>0 &&
         TimeCurrent()-m_state.execution_requested_at>=15 && exists &&
         MathAbs(after.volume-m_state.execution_before_volume)<
            SymbolInfoDouble(m_symbol,SYMBOL_VOLUME_STEP)*0.5)
      {
         m_state.execution_check_pending=false;
         m_state.execution_stage=EXIT_STAGE_NONE;
         m_state.execution_before_volume=0.0;
         m_state.execution_requested_volume=0.0;
         m_state.execution_result_order=0;
         m_state.execution_result_deal=0;
         m_state.execution_requested_at=0;
         SaveState();
         event_message="[退出执行核验] 已确认未发生减仓，下一Tick允许重试";
         return true;
      }
      event_message="[退出执行待核验] 网络结果仍不明确，禁止重复发送减仓请求";
      return false;
   }

   bool ExecuteStageClose(const ENUM_EXIT_STAGE stage,const double close_percent,
                          string &event_message)
   {
      event_message="";
      if(IsExitStageRetryBlocked(stage))
         return false;
      if(m_state.execution_check_pending)
      {
         ResolveUncertainExecution(event_message);
         return false;
      }
      ManagedPositionSnapshot before;
      string select_error="";
      if(!SelectManagedPosition(m_state.position_id,before,select_error))
      {
         event_message="[退出执行异常] "+select_error;
         return false;
      }
      if(stage==EXIT_STAGE_TP1 && (m_state.exit_mode!=EXIT_MODE_STAGED || m_state.tp1_done))
         return false;
      if(stage==EXIT_STAGE_TP2 &&
         (m_state.exit_mode!=EXIT_MODE_STAGED || !m_state.tp1_done || m_state.tp2_done))
         return false;
      if(stage==EXIT_STAGE_LEGACY_TP1 &&
         (m_state.exit_mode!=EXIT_MODE_LEGACY || m_state.tp1_reached)) return false;

      const double minimum=SymbolInfoDouble(m_symbol,SYMBOL_VOLUME_MIN);
      const double maximum=SymbolInfoDouble(m_symbol,SYMBOL_VOLUME_MAX);
      const double step=SymbolInfoDouble(m_symbol,SYMBOL_VOLUME_STEP);
      StageClosePlan plan;
      if(stage==EXIT_STAGE_LEGACY_TP1)
      {
         ZeroMemory(plan);
         plan.reason="";
         const double close_volume=NormalizeVolumeDown(before.volume*close_percent/100.0,step);
         const double remaining=NormalizeVolumeDown(before.volume-close_volume,step);
         plan.valid=(before.volume>=minimum && close_percent>0.0);
         plan.close_all=(close_volume<minimum || remaining<minimum);
         plan.close_volume=(plan.close_all ? before.volume : close_volume);
         plan.expected_remaining=(plan.close_all ? 0.0 : remaining);
      }
      else
         plan=BuildStageClosePlan(m_state.initial_volume,before.volume,close_percent,
                                  minimum,maximum,step);
      if(!plan.valid || plan.close_volume<=0.0)
      {
         event_message="[退出执行异常] "+plan.reason;
         return false;
      }

      const string order_comment=StringSubstr(m_state.signal_id+"_"+ExitStageLabel(stage),0,31);
      bool request_ok=false;
      if(plan.close_all)
         request_ok=m_trade.PositionClose(before.ticket);
      else if(AccountInfoInteger(ACCOUNT_MARGIN_MODE)==ACCOUNT_MARGIN_MODE_RETAIL_HEDGING)
         request_ok=m_trade.PositionClosePartial(before.ticket,plan.close_volume);
      else if(before.type==POSITION_TYPE_BUY)
         request_ok=m_trade.Sell(plan.close_volume,m_symbol,0.0,0.0,0.0,order_comment);
      else
         request_ok=m_trade.Buy(plan.close_volume,m_symbol,0.0,0.0,0.0,order_comment);

      const uint retcode=m_trade.ResultRetcode();
      const ulong result_order=m_trade.ResultOrder();
      const ulong result_deal=m_trade.ResultDeal();
      ManagedPositionSnapshot after;
      string after_error="";
      const bool exists_after=SelectManagedPosition(before.identifier,after,after_error);
      const bool fully_closed=(!exists_after && ManagedPositionCount()==0);
      const bool reduced=(fully_closed ||
         (exists_after && IsConfirmedVolumeReduction(before.volume,true,after.volume,step)));
      if(reduced)
      {
         TradeRuntimeState proposed=m_state;
         proposed.remaining_volume=before.volume;
         string apply_error="";
         if(!ApplyConfirmedStageResult(proposed,stage,exists_after,
                                       (exists_after ? after.volume : 0.0),apply_error))
         {
            event_message="[退出执行异常] 实际减仓已发生但状态提交失败："+apply_error;
            return false;
         }
         if(stage==EXIT_STAGE_TP1 && !m_move_break_even)
            proposed.break_even_pending=false;
         m_state=proposed;
         SaveState();
         ResetStageCloseRetry();
         if(fully_closed)
         {
            Print("[分段止盈] 因小仓位无法继续拆分，本阶段全部止盈");
            event_message="["+ExitStageLabel(stage)+"] 全部平仓成功 | Close="+
                          DoubleToString(plan.close_volume,2);
         }
         else
            event_message="["+ExitStageLabel(stage)+"] 部分平仓成功 | Close="+
                          DoubleToString(plan.close_volume,2)+" | Remaining="+
                          DoubleToString(after.volume,2);
         return true;
      }

      if(request_ok || IsCloseRetcodeAccepted(retcode) || IsAmbiguousRetcode(retcode))
      {
         m_state.execution_check_pending=true;
         m_state.execution_stage=stage;
         m_state.execution_before_volume=before.volume;
         m_state.execution_requested_volume=plan.close_volume;
         m_state.execution_result_order=result_order;
         m_state.execution_result_deal=result_deal;
         m_state.execution_requested_at=TimeCurrent();
         SaveState();
         event_message="["+ExitStageLabel(stage)+"异常] 交易结果待核验 | Retcode="+
                       IntegerToString((int)retcode)+" | 阶段状态未提交";
         return false;
      }
      if(retcode==TRADE_RETCODE_MARKET_CLOSED)
      {
         ArmStageCloseRetry(stage,STAGE_CLOSE_MARKET_CLOSED_RETRY_SECONDS);
         Print("["+ExitStageLabel(stage)+"异常] 部分平仓失败 | Retcode="+
               IntegerToString((int)retcode)+" "+m_trade.ResultRetcodeDescription()+
               " | 原状态未修改");
         event_message="["+ExitStageLabel(stage)+"重试] 市场关闭，暂停重复减仓请求 | "+
                       IntegerToString(STAGE_CLOSE_MARKET_CLOSED_RETRY_SECONDS)+
                       "秒后重试 | NextRetry="+
                       TimeToString(m_stage_close_retry_after,TIME_DATE|TIME_SECONDS);
         return false;
      }
      event_message="["+ExitStageLabel(stage)+"异常] 部分平仓失败 | Retcode="+
                    IntegerToString((int)retcode)+" "+m_trade.ResultRetcodeDescription()+
                    " | 原状态未修改";
      return false;
   }

   bool ManageStagedExit(const double bid,const double ask,string &event_message)
   {
      event_message="";
      if(m_state.exit_mode!=EXIT_MODE_STAGED) return false;
      if(m_state.execution_check_pending)
      {
         ResolveUncertainExecution(event_message);
         return false;
      }
      const ENUM_EXIT_STAGE stage=NextStagedExitStage(m_state,bid,ask);
      if(stage==EXIT_STAGE_NONE) return false;
      Print(stage==EXIT_STAGE_TP1 ? "[TP1-1R] 已达到1R" : "[TP2-2R] 已达到2R");
      // V310_TP2_CLOSE_ALL_REMAINING
      return ExecuteStageClose(stage,(stage==EXIT_STAGE_TP1 ? m_tp1_close_percent : 100.0),
                               event_message);
   }

   bool ManageLegacyTP1(const double bid,const double ask,string &event_message)
   {
      event_message="";
      if(m_state.exit_mode!=EXIT_MODE_LEGACY || m_state.tp1_reached || m_state.tp1<=0.0)
         return false;
      const bool reached=(m_state.direction==DIR_BUY ? bid>=m_state.tp1 : ask<=m_state.tp1);
      if(!reached) return false;
      return ExecuteStageClose(EXIT_STAGE_LEGACY_TP1,m_tp1_close_percent,event_message);
   }

   bool TrySetProtection(const bool use_tp2_lock,string &event_message)
   {
      event_message="";
      if(m_state.exit_mode!=EXIT_MODE_STAGED) return false;
      if(use_tp2_lock && (!m_state.tp2_done || !m_state.tp2_lock_pending)) return false;
      if(!use_tp2_lock && (!m_state.tp1_done || !m_state.break_even_pending)) return false;
      ManagedPositionSnapshot position;
      string select_error="";
      if(!SelectManagedPosition(m_state.position_id,position,select_error)) return false;
      MqlTick tick;
      if(!SymbolInfoTick(m_symbol,tick) || tick.bid<=0.0 || tick.ask<=0.0) return false;
      double desired=m_state.lock_price;
      if(!use_tp2_lock)
      {
         desired=m_state.entry;
      }
      const double tick_size=SymbolInfoDouble(m_symbol,SYMBOL_TRADE_TICK_SIZE);
      const double aligned=(m_state.direction==DIR_BUY ? AlignPriceDown(desired,tick_size) :
                                                       AlignPriceUp(desired,tick_size));
      const double epsilon=tick_size*0.1;
      const bool already_set=(m_state.direction==DIR_BUY ?
         position.sl>=aligned-epsilon : (position.sl>0.0 && position.sl<=aligned+epsilon));
      if(already_set)
      {
         m_state.stop_loss=position.sl;
         if(use_tp2_lock)
         {
            m_state.tp2_lock_done=true;
            m_state.tp2_lock_pending=false;
         }
         else
         {
            m_state.break_even_done=true;
            m_state.break_even_pending=false;
         }
         SaveState();
         event_message=(use_tp2_lock ? "[锁盈] 已核验当前SL达到1R保护" :
                                       "[保本] 已核验当前SL达到保本保护");
         return true;
      }
      const StopValidationResult validation=ValidateProtectiveStop(m_state.direction,desired,
         position.sl,tick.bid,tick.ask,SymbolInfoDouble(m_symbol,SYMBOL_POINT),tick_size,
         (int)SymbolInfoInteger(m_symbol,SYMBOL_TRADE_STOPS_LEVEL),
         (int)SymbolInfoInteger(m_symbol,SYMBOL_TRADE_FREEZE_LEVEL));
      if(!validation.valid)
      {
         event_message=(use_tp2_lock ? "[锁盈待执行] " : "[保本待执行] ")+
                       validation.reason+"，后续Tick重试";
         return false;
      }
      const bool request_ok=m_trade.PositionModify(position.ticket,
                                                    validation.aligned_price,position.tp);
      const uint retcode=m_trade.ResultRetcode();
      ManagedPositionSnapshot after;
      string after_error="";
      const bool exists=SelectManagedPosition(position.identifier,after,after_error);
      const bool confirmed=(exists && (m_state.direction==DIR_BUY ?
         after.sl>=validation.aligned_price-epsilon :
         after.sl>0.0 && after.sl<=validation.aligned_price+epsilon));
      if(!confirmed)
      {
         const double failed_after_sl=(exists ? after.sl : 0.0);
         string failure_desc=m_trade.ResultRetcodeDescription();
         if(!exists)
            failure_desc+=" | after_read_failed";
         RecordServerSLEvent((use_tp2_lock ? "TP2_LOCK_1R" : "TP1_BREAKEVEN"),
                             m_state.signal_id,position.identifier,m_state.direction,
                             (exists ? after.volume : position.volume),
                             m_state.initial_sl,m_state.initial_sl,position.sl,
                             validation.aligned_price,failed_after_sl,false,
                             retcode,failure_desc);
         event_message=(use_tp2_lock ? "[锁盈待执行] " : "[保本待执行] ")+
                       "修改未确认 | CTrade="+(request_ok?"true":"false")+
                       " | Retcode="+IntegerToString((int)retcode)+
                       " | "+m_trade.ResultRetcodeDescription();
         return false;
      }
      RecordServerSLEvent((use_tp2_lock ? "TP2_LOCK_1R" : "TP1_BREAKEVEN"),
                          m_state.signal_id,position.identifier,m_state.direction,after.volume,
                          m_state.initial_sl,m_state.initial_sl,position.sl,validation.aligned_price,
                          after.sl,true,retcode,m_trade.ResultRetcodeDescription());
      Print("[服务器SL修改] Position=",IntegerToString((long)position.identifier),
            " | Source=",(use_tp2_lock ? "TP2_LOCK_1R" : "TP1_BREAKEVEN"),
            " | BeforeSL=",DoubleToString(position.sl,_Digits),
            " | RequestedSL=",DoubleToString(validation.aligned_price,_Digits),
            " | Retcode=",IntegerToString((int)retcode),
            " | AfterSL=",DoubleToString(after.sl,_Digits));
      m_state.stop_loss=after.sl;
      if(use_tp2_lock)
      {
         m_state.tp2_lock_done=true;
         m_state.tp2_lock_pending=false;
         event_message="[锁盈] SL已移动至"+DoubleToString(m_tp2_lock_multiple,2)+
                       "R | NewSL="+DoubleToString(after.sl,_Digits);
      }
      else
      {
         m_state.break_even_done=true;
         m_state.break_even_pending=false;
         event_message="[保本] 新SL="+DoubleToString(after.sl,_Digits);
      }
      SaveState();
      return true;
   }

   bool TrySetServerStructureTP(string &event_message)
   {
      event_message="";
      if(m_state.exit_mode!=EXIT_MODE_STAGED || !m_state.server_tp_pending ||
         m_state.server_tp_done) return false;
      ManagedPositionSnapshot position;
      string select_error="";
      if(!SelectManagedPosition(m_state.position_id,position,select_error)) return false;
      const double tick_size=SymbolInfoDouble(m_symbol,SYMBOL_TRADE_TICK_SIZE);
      double target=(m_state.direction==DIR_BUY ?
         AlignPriceDown(m_state.structure_target,tick_size) :
         AlignPriceUp(m_state.structure_target,tick_size));
      if(!IsStructureTargetBeyondActualTP2(m_state.direction,target,m_state.tp2_price,tick_size))
      {
         m_state.server_tp_pending=false;
         SaveState();
         event_message="[服务器TP警告] Swing目标未处于实际2R之外，保持TP=0";
         return false;
      }
      const double epsilon=tick_size*0.1;
      if(MathAbs(position.tp-target)<=epsilon)
      {
         m_state.server_tp_done=true;
         m_state.server_tp_pending=false;
         SaveState();
         event_message="[服务器TP] 已核验结构目标设置成功";
         return true;
      }
      MqlTick tick;
      if(!SymbolInfoTick(m_symbol,tick) || tick.bid<=0.0 || tick.ask<=0.0) return false;
      const double point=SymbolInfoDouble(m_symbol,SYMBOL_POINT);
      const double minimum=MathMax((double)MathMax(
         (int)SymbolInfoInteger(m_symbol,SYMBOL_TRADE_STOPS_LEVEL),
         (int)SymbolInfoInteger(m_symbol,SYMBOL_TRADE_FREEZE_LEVEL)),0.0)*point;
      const bool direction_valid=(m_state.direction==DIR_BUY ? target>tick.ask+minimum-epsilon :
                                                                target<tick.bid-minimum+epsilon);
      if(!direction_valid)
      {
         const bool already_passed=(m_state.direction==DIR_BUY ? tick.bid>=target : tick.ask<=target);
         if(already_passed)
         {
            m_state.server_tp_pending=false;
            SaveState();
         }
         event_message="[服务器TP警告] 当前价格距离不足，保持TP=0";
         return false;
      }
      const bool request_ok=m_trade.PositionModify(position.ticket,position.sl,target);
      const uint retcode=m_trade.ResultRetcode();
      ManagedPositionSnapshot after;
      string after_error="";
      const bool confirmed=(SelectManagedPosition(position.identifier,after,after_error) &&
                            MathAbs(after.tp-target)<=epsilon);
      if(!confirmed)
      {
         event_message="[服务器TP警告] 设置结果未确认 | Retcode="+
                       IntegerToString((int)retcode)+" | CTrade="+
                       (request_ok?"true":"false")+" | 保持待核验";
         return false;
      }
      m_state.server_tp_done=true;
      m_state.server_tp_pending=false;
      m_state.stop_loss=after.sl;
      SaveState();
      event_message="[服务器TP] 结构目标设置成功";
      return true;
   }

   bool RetryPendingProtection(string &event_message)
   {
      event_message="";
      if(m_state.tp2_lock_pending && TrySetProtection(true,event_message)) return true;
      if(event_message!="") return false;
      if(m_state.break_even_pending && TrySetProtection(false,event_message)) return true;
      if(event_message!="") return false;
      if(m_state.server_tp_pending && TrySetServerStructureTP(event_message)) return true;
      if(event_message!="") return false;
      if(m_state.runner_stop_pending)
         return TrailToStructure(m_state.runner_pending_structure,
                                 m_state.runner_pending_buffer,event_message);
      return false;
   }

   bool TrailToStructure(const double structure_price,const double buffer,
                         string &event_message)
   {
      event_message="";
      const bool eligible=CanTrailToStructureByState(m_state);
      if(!eligible || structure_price<=0.0 || buffer<0.0) return false;
      ManagedPositionSnapshot position;
      string select_error="";
      if(!SelectManagedPosition(m_state.position_id,position,select_error)) return false;
      const bool new_structure=(m_state.direction==DIR_BUY ?
         structure_price>m_state.last_tracked_structure :
         (m_state.last_tracked_structure<=0.0 || structure_price<m_state.last_tracked_structure));
      if(!new_structure) return false;
      const double desired=(m_state.direction==DIR_BUY ? structure_price-buffer :
                                                            structure_price+buffer);
      MqlTick tick;
      if(!SymbolInfoTick(m_symbol,tick)) return false;
      const double tick_size=SymbolInfoDouble(m_symbol,SYMBOL_TRADE_TICK_SIZE);
      const double aligned=(m_state.direction==DIR_BUY ? AlignPriceDown(desired,tick_size) :
                                                       AlignPriceUp(desired,tick_size));
      const double epsilon=tick_size*0.1;
      const bool already_set=(m_state.direction==DIR_BUY ?
         position.sl>=aligned-epsilon : (position.sl>0.0 && position.sl<=aligned+epsilon));
      if(already_set)
      {
         m_state.stop_loss=position.sl;
         m_state.last_tracked_structure=structure_price;
         m_state.runner_stop_pending=false;
         m_state.runner_pending_structure=0.0;
         m_state.runner_pending_buffer=0.0;
         m_state.state=STATE_RUNNER;
         SaveState();
         event_message="[RUNNER] 已核验当前SL达到结构保护 | SL="+
                       DoubleToString(position.sl,_Digits);
         return true;
      }
      const StopValidationResult validation=ValidateProtectiveStop(m_state.direction,desired,
         position.sl,tick.bid,tick.ask,SymbolInfoDouble(m_symbol,SYMBOL_POINT),
         tick_size,
         (int)SymbolInfoInteger(m_symbol,SYMBOL_TRADE_STOPS_LEVEL),
         (int)SymbolInfoInteger(m_symbol,SYMBOL_TRADE_FREEZE_LEVEL));
      if(!validation.valid)
      {
         m_state.runner_stop_pending=true;
         m_state.runner_pending_structure=structure_price;
         m_state.runner_pending_buffer=buffer;
         SaveState();
         event_message="[RUNNER止损待执行] "+validation.reason+"，后续Tick重试";
         return false;
      }
      const bool request_ok=m_trade.PositionModify(position.ticket,
                                                    validation.aligned_price,position.tp);
      const uint retcode=m_trade.ResultRetcode();
      ManagedPositionSnapshot after;
      string after_error="";
      const bool exists=SelectManagedPosition(position.identifier,after,after_error);
      const bool confirmed=(exists && (m_state.direction==DIR_BUY ?
         after.sl>=validation.aligned_price-epsilon :
         after.sl>0.0 && after.sl<=validation.aligned_price+epsilon));
      if(!confirmed)
      {
         const double failed_after_sl=(exists ? after.sl : 0.0);
         string failure_desc=m_trade.ResultRetcodeDescription();
         if(!exists)
            failure_desc+=" | after_read_failed";
         RecordServerSLEvent("RUNNER_TRAIL",m_state.signal_id,position.identifier,
                             m_state.direction,(exists ? after.volume : position.volume),
                             m_state.initial_sl,m_state.initial_sl,position.sl,
                             validation.aligned_price,failed_after_sl,false,
                             retcode,failure_desc);
         m_state.runner_stop_pending=true;
         m_state.runner_pending_structure=structure_price;
         m_state.runner_pending_buffer=buffer;
         SaveState();
         event_message="[RUNNER止损待核验] 修改结果未确认 | CTrade="+
                       (request_ok?"true":"false")+" | Retcode="+
                       IntegerToString((int)retcode)+" | 后续Tick先核验再重试";
         return false;
      }
      RecordServerSLEvent("RUNNER_TRAIL",m_state.signal_id,position.identifier,
                          m_state.direction,after.volume,m_state.initial_sl,m_state.initial_sl,
                          position.sl,validation.aligned_price,after.sl,
                          true,retcode,m_trade.ResultRetcodeDescription());
      Print("[服务器SL修改] Position=",IntegerToString((long)position.identifier),
            " | Source=RUNNER_TRAIL",
            " | BeforeSL=",DoubleToString(position.sl,_Digits),
            " | RequestedSL=",DoubleToString(validation.aligned_price,_Digits),
            " | Retcode=",IntegerToString((int)retcode),
            " | AfterSL=",DoubleToString(after.sl,_Digits));
      m_state.stop_loss=after.sl;
      m_state.last_tracked_structure=structure_price;
      m_state.runner_stop_pending=false;
      m_state.runner_pending_structure=0.0;
      m_state.runner_pending_buffer=0.0;
      m_state.state=STATE_RUNNER;
      SaveState();
      event_message=(m_state.exit_mode==EXIT_MODE_STAGED ? "[RUNNER] 新结构移动止损 | NewSL=" :
                                                         "[移动止损] 新SL=")+
                    DoubleToString(m_state.stop_loss,_Digits);
      return true;
   }

   void MarkClosed()
   {
      ResetStageCloseRetry();
      ResetTradeRuntimeState(m_state);
      SaveState();
   }
};

#endif
// ===== END INLINE: Include/XAUAI/TradeManager.mqh =====
// [单文件合并] 已内联，跳过重复模块: CsvLogger.mqh

// ===== BEGIN INLINE: Include/XAUAI/ObservationTypes.mqh =====
#ifndef XAUAI_OBSERVATION_TYPES_MQH
#define XAUAI_OBSERVATION_TYPES_MQH

// [单文件合并] 已内联，跳过重复模块: StrategyTypes.mqh

struct ObservationFrameSnapshot
{
   bool              valid;
   ENUM_TIMEFRAMES   timeframe;
   datetime          bar_time;
   double            open;
   double            high;
   double            low;
   double            close;
   long              tick_volume;
   double            ema20;
   double            atr14;
   double            rsi14;
   double            macd_hist;
};

void ResetObservationFrameSnapshot(ObservationFrameSnapshot &value)
{
   ZeroMemory(value);
   value.valid=false;
   value.timeframe=PERIOD_CURRENT;
}

string ObservationOutcomeLabel(const ENUM_SCAN_OUTCOME outcome)
{
   if(outcome==SCAN_PASS) return "pass";
   if(outcome==SCAN_REJECT) return "reject";
   if(outcome==SCAN_SKIP) return "skip";
   return "unknown";
}

string ObservationStageLabel(const ENUM_SCAN_STAGE stage)
{
   if(stage==STAGE_ENVIRONMENT) return "environment";
   if(stage==STAGE_EXPOSURE) return "exposure";
   if(stage==STAGE_TREND) return "trend";
   if(stage==STAGE_STRUCTURE) return "structure";
   if(stage==STAGE_FIB) return "fib";
   if(stage==STAGE_PRICE_ACTION) return "price_action";
   if(stage==STAGE_SIGNAL_BAR) return "signal_bar";
   if(stage==STAGE_RISK_LOCK) return "risk_lock";
   if(stage==STAGE_SPREAD) return "spread";
   if(stage==STAGE_DUPLICATE) return "duplicate";
   if(stage==STAGE_CANDIDATE) return "candidate";
   if(stage==STAGE_AI) return "ai";
   if(stage==STAGE_ORDER) return "order";
   return "unknown";
}

string ObservationEAStateLabel(const ENUM_EA_STATE state)
{
   if(state==STATE_IDLE) return "idle";
   if(state==STATE_CANDIDATE_FOUND) return "candidate_found";
   if(state==STATE_WAIT_AI) return "wait_ai";
   if(state==STATE_AI_APPROVED) return "ai_approved";
   if(state==STATE_PENDING_ORDER) return "pending_order";
   if(state==STATE_POSITION_OPEN) return "position_open";
   if(state==STATE_TP1_REACHED) return "tp1_reached";
   if(state==STATE_RUNNER) return "runner";
   if(state==STATE_LOCKED) return "locked";
   return "unknown";
}

int CurrentServerUtcOffsetSeconds()
{
   const datetime server_now=TimeTradeServer();
   const datetime gmt_now=TimeGMT();
   if(server_now<=0 || gmt_now<=0) return 0;
   return (int)(server_now-gmt_now);
}

datetime BeijingTimeFromServer(const datetime server_time)
{
   return server_time-CurrentServerUtcOffsetSeconds()+8*60*60;
}

string ObservationTimeText(const datetime value)
{
   if(value<=0) return "";
   MqlDateTime parts;
   TimeToStruct(value,parts);
   return StringFormat("%04d-%02d-%02d %02d:%02d:%02d",parts.year,parts.mon,parts.day,
                       parts.hour,parts.min,parts.sec);
}

string BeijingDateKey(const datetime server_time)
{
   MqlDateTime parts;
   TimeToStruct(BeijingTimeFromServer(server_time),parts);
   return StringFormat("%04d-%02d-%02d",parts.year,parts.mon,parts.day);
}

string BeijingHourKey(const datetime server_time)
{
   MqlDateTime parts;
   TimeToStruct(BeijingTimeFromServer(server_time),parts);
   return StringFormat("%04d-%02d-%02dT%02d",parts.year,parts.mon,parts.day,parts.hour);
}

string SanitizeObservationPathPart(const string source)
{
   string value=source;
   StringReplace(value,"\\","_");
   StringReplace(value,"/","_");
   StringReplace(value,":","_");
   StringReplace(value,"*","_");
   StringReplace(value,"?","_");
   StringReplace(value,"\"","_");
   StringReplace(value,"<","_");
   StringReplace(value,">","_");
   StringReplace(value,"|","_");
   StringReplace(value," ","_");
   if(value=="") value="UNKNOWN";
   return value;
}

#endif
// ===== END INLINE: Include/XAUAI/ObservationTypes.mqh =====

// ===== BEGIN INLINE: Include/XAUAI/ObservationLogger.mqh =====
#ifndef XAUAI_OBSERVATION_LOGGER_MQH
#define XAUAI_OBSERVATION_LOGGER_MQH

// [单文件合并] 已内联，跳过重复模块: ObservationTypes.mqh

string ObservationCsvEscape(const string source)
{
   string value=source;
   StringReplace(value,"\"","\"\"");
   if(StringFind(source,",")>=0 || StringFind(source,"\"")>=0 ||
      StringFind(source,"\r")>=0 || StringFind(source,"\n")>=0)
      return "\""+value+"\"";
   return value;
}

bool AppendCommonUtf8CsvLine(const string filename,const string header,const string line)
{
   const int handle=FileOpen(filename,FILE_READ|FILE_WRITE|FILE_TXT|FILE_ANSI|
                             FILE_COMMON|FILE_SHARE_READ|FILE_SHARE_WRITE,0,CP_UTF8);
   if(handle==INVALID_HANDLE) return false;
   if(FileSize(handle)==0) FileWriteString(handle,header+"\r\n");
   FileSeek(handle,0,SEEK_END);
   FileWriteString(handle,line+"\r\n");
   FileFlush(handle);
   FileClose(handle);
   return true;
}

class CObservationLogger
{
private:
   bool              m_enabled;
   string            m_symbol;
   long              m_magic;
   string            m_base_path;
   ENUM_TIMEFRAMES   m_timeframes[4];
   int               m_ema_handles[4];
   int               m_atr_handles[4];
   int               m_rsi_handles[4];
   int               m_macd_handles[4];

   void ResetHandles()
   {
      for(int i=0;i<4;i++)
      {
         m_ema_handles[i]=INVALID_HANDLE;
         m_atr_handles[i]=INVALID_HANDLE;
         m_rsi_handles[i]=INVALID_HANDLE;
         m_macd_handles[i]=INVALID_HANDLE;
      }
   }

   bool CopyOne(const int handle,const int buffer_index,const int shift,double &value) const
   {
      double data[];
      ArrayResize(data,1);
      if(handle==INVALID_HANDLE || CopyBuffer(handle,buffer_index,shift,1,data)!=1) return false;
      value=data[0];
      return MathIsValidNumber(value);
   }

   bool LoadFrame(const int index,ObservationFrameSnapshot &snapshot) const
   {
      ResetObservationFrameSnapshot(snapshot);
      if(index<0 || index>=4) return false;
      MqlRates rates[];
      ArrayResize(rates,1);
      if(CopyRates(m_symbol,m_timeframes[index],1,1,rates)!=1) return false;
      double ema=0.0,atr=0.0,rsi=0.0,macd_main=0.0,macd_signal=0.0;
      if(!CopyOne(m_ema_handles[index],0,1,ema) ||
         !CopyOne(m_atr_handles[index],0,1,atr) ||
         !CopyOne(m_rsi_handles[index],0,1,rsi) ||
         !CopyOne(m_macd_handles[index],0,1,macd_main) ||
         !CopyOne(m_macd_handles[index],1,1,macd_signal)) return false;
      snapshot.valid=true;
      snapshot.timeframe=m_timeframes[index];
      snapshot.bar_time=rates[0].time;
      snapshot.open=rates[0].open;
      snapshot.high=rates[0].high;
      snapshot.low=rates[0].low;
      snapshot.close=rates[0].close;
      snapshot.tick_volume=rates[0].tick_volume;
      snapshot.ema20=ema;
      snapshot.atr14=atr;
      snapshot.rsi14=rsi;
      snapshot.macd_hist=macd_main-macd_signal;
      return true;
   }

   string FrameCsv(const ObservationFrameSnapshot &frame) const
   {
      if(!frame.valid) return ",,,,,,,,,";
      return ObservationCsvEscape(ObservationTimeText(frame.bar_time))+","+
             DoubleToString(frame.open,_Digits)+","+DoubleToString(frame.high,_Digits)+","+
             DoubleToString(frame.low,_Digits)+","+DoubleToString(frame.close,_Digits)+","+
             IntegerToString((int)frame.tick_volume)+","+DoubleToString(frame.ema20,_Digits)+","+
             DoubleToString(frame.atr14,_Digits)+","+DoubleToString(frame.rsi14,2)+","+
             DoubleToString(frame.macd_hist,8);
   }

public:
   CObservationLogger()
   {
      m_enabled=false;
      m_symbol="";
      m_magic=0;
      m_base_path="";
      ResetHandles();
   }

   bool Init(const string symbol,const long magic,const string root_folder,
             const bool enabled,const bool include_tester,
             const int ema_period,const int atr_period,const int rsi_period,
             const int macd_fast,const int macd_slow,const int macd_signal,
             string &error)
   {
      error="";
      Release();
      m_symbol=symbol;
      m_magic=magic;
      m_enabled=(enabled && (!((bool)MQLInfoInteger(MQL_TESTER)) || include_tester));
      const string company=SanitizeObservationPathPart(AccountInfoString(ACCOUNT_COMPANY));
      const string server=SanitizeObservationPathPart(AccountInfoString(ACCOUNT_SERVER));
      const string login=IntegerToString(AccountInfoInteger(ACCOUNT_LOGIN));
      const string environment=company+"_"+server;
      const string symbol_tf=SanitizeObservationPathPart(symbol)+"_M5";
      // 旧的长根目录名会让公共路径超过 Windows 260 字符上限，导致所有文件写入失败
      // （parallel audit temp open failed: 5003）。把历史长名自动映射到短名，
      // 这样即使图表里还存着旧参数，数据也会落到同一个新根目录下。
      string effective_root=root_folder;
      if(effective_root=="XAUUSD_M15_M5_V3109_C31_M5_ENTRY_QUALITY")
         effective_root="XAU_V3109_C33_TF1";
      m_base_path=SanitizeObservationPathPart(effective_root)+"\\"+environment+"\\Account_"+
                  login+"\\"+symbol_tf;
      if(!m_enabled) return true;

      m_timeframes[0]=PERIOD_M5;
      m_timeframes[1]=PERIOD_M15;
      m_timeframes[2]=PERIOD_H1;
      m_timeframes[3]=PERIOD_H4;
      for(int i=0;i<4;i++)
      {
         m_ema_handles[i]=iMA(m_symbol,m_timeframes[i],ema_period,0,MODE_EMA,PRICE_CLOSE);
         m_atr_handles[i]=iATR(m_symbol,m_timeframes[i],atr_period);
         m_rsi_handles[i]=iRSI(m_symbol,m_timeframes[i],rsi_period,PRICE_CLOSE);
         m_macd_handles[i]=iMACD(m_symbol,m_timeframes[i],macd_fast,macd_slow,macd_signal,PRICE_CLOSE);
         if(m_ema_handles[i]==INVALID_HANDLE || m_atr_handles[i]==INVALID_HANDLE ||
            m_rsi_handles[i]==INVALID_HANDLE || m_macd_handles[i]==INVALID_HANDLE)
         {
            error="AI盯盘多周期指标句柄创建失败: "+EnumToString(m_timeframes[i]);
            Release();
            return false;
         }
      }
      return true;
   }

   void Release()
   {
      for(int i=0;i<4;i++)
      {
         if(m_ema_handles[i]!=INVALID_HANDLE) IndicatorRelease(m_ema_handles[i]);
         if(m_atr_handles[i]!=INVALID_HANDLE) IndicatorRelease(m_atr_handles[i]);
         if(m_rsi_handles[i]!=INVALID_HANDLE) IndicatorRelease(m_rsi_handles[i]);
         if(m_macd_handles[i]!=INVALID_HANDLE) IndicatorRelease(m_macd_handles[i]);
      }
      ResetHandles();
      m_enabled=false;
   }

   bool IsEnabled() const { return m_enabled; }
   string BasePath() const { return m_base_path; }

   bool WriteMarketSnapshot(const datetime server_now,const ScanResult &scan,
                            const string weekend_status,const string rollover_status,
                            const TradeRuntimeState &runtime_state)
   {
      if(!m_enabled) return true;
      ObservationFrameSnapshot frames[4];
      for(int i=0;i<4;i++) LoadFrame(i,frames[i]);
      const double bid=SymbolInfoDouble(m_symbol,SYMBOL_BID);
      const double ask=SymbolInfoDouble(m_symbol,SYMBOL_ASK);
      const string day=BeijingDateKey(server_now);
      const string filename=m_base_path+"\\Raw_Data\\Market_Snapshots_"+day+".csv";
      const string header="server_time,beijing_time,symbol,bid,ask,spread,account_balance,account_equity,ea_state,signal_id,order_ticket,position_id,last_scan_outcome,last_scan_stage,last_scan_reason,weekend_guard,rollover_guard,"+
         "m5_time,m5_open,m5_high,m5_low,m5_close,m5_tick_volume,m5_ema20,m5_atr14,m5_rsi14,m5_macd_hist,"+
         "m15_time,m15_open,m15_high,m15_low,m15_close,m15_tick_volume,m15_ema20,m15_atr14,m15_rsi14,m15_macd_hist,"+
         "h1_time,h1_open,h1_high,h1_low,h1_close,h1_tick_volume,h1_ema20,h1_atr14,h1_rsi14,h1_macd_hist,"+
         "h4_time,h4_open,h4_high,h4_low,h4_close,h4_tick_volume,h4_ema20,h4_atr14,h4_rsi14,h4_macd_hist";
      string row=ObservationCsvEscape(ObservationTimeText(server_now))+","+
         ObservationCsvEscape(ObservationTimeText(BeijingTimeFromServer(server_now)))+","+
         ObservationCsvEscape(m_symbol)+","+DoubleToString(bid,_Digits)+","+
         DoubleToString(ask,_Digits)+","+DoubleToString(ask-bid,_Digits)+","+
         DoubleToString(AccountInfoDouble(ACCOUNT_BALANCE),2)+","+
         DoubleToString(AccountInfoDouble(ACCOUNT_EQUITY),2)+","+
         ObservationEAStateLabel(runtime_state.state)+","+ObservationCsvEscape(runtime_state.signal_id)+","+
         IntegerToString((long)runtime_state.order_ticket)+","+IntegerToString((long)runtime_state.position_id)+","+
         ObservationOutcomeLabel(scan.outcome)+","+ObservationStageLabel(scan.stage)+","+
         ObservationCsvEscape(scan.reason)+","+ObservationCsvEscape(weekend_status)+","+
         ObservationCsvEscape(rollover_status)+","+FrameCsv(frames[0])+","+FrameCsv(frames[1])+","+
         FrameCsv(frames[2])+","+FrameCsv(frames[3]);
      return AppendCommonUtf8CsvLine(filename,header,row);
   }

   bool WriteEvent(const datetime server_now,const string event_type,const string signal_id,
                   const string stage,const string outcome,const string reason,
                   const string details,const ulong order_ticket=0,const ulong position_id=0,
                   const ulong deal_ticket=0,const ENUM_TRADE_DIRECTION direction=DIR_NONE,
                   const double volume=0.0,const double price=0.0,const double profit=0.0)
   {
      if(!m_enabled) return true;
      const string day=BeijingDateKey(server_now);
      const string filename=m_base_path+"\\Raw_Data\\Strategy_Events_"+day+".csv";
      const string header="server_time,beijing_time,event_type,signal_id,stage,outcome,reason,details,order_ticket,position_id,deal_ticket,direction,volume,price,profit";
      const string row=ObservationCsvEscape(ObservationTimeText(server_now))+","+
         ObservationCsvEscape(ObservationTimeText(BeijingTimeFromServer(server_now)))+","+
         ObservationCsvEscape(event_type)+","+ObservationCsvEscape(signal_id)+","+
         ObservationCsvEscape(stage)+","+ObservationCsvEscape(outcome)+","+
         ObservationCsvEscape(reason)+","+ObservationCsvEscape(details)+","+
         IntegerToString((long)order_ticket)+","+IntegerToString((long)position_id)+","+
         IntegerToString((long)deal_ticket)+","+DirectionLabel(direction)+","+
         DoubleToString(volume,2)+","+DoubleToString(price,_Digits)+","+
         DoubleToString(profit,2);
      return AppendCommonUtf8CsvLine(filename,header,row);
   }
};

#endif
// ===== END INLINE: Include/XAUAI/ObservationLogger.mqh =====

// ===== BEGIN INLINE: Include/XAUAI/EconomicCalendarLogger.mqh =====
#ifndef XAUAI_ECONOMIC_CALENDAR_LOGGER_MQH
#define XAUAI_ECONOMIC_CALENDAR_LOGGER_MQH

// [单文件合并] 已内联，跳过重复模块: ObservationLogger.mqh

class CEconomicCalendarLogger
{
private:
   bool   m_enabled;
   string m_base_path;
   string m_last_refresh_hour;
   ulong  m_written_value_ids[];

   bool WasWritten(const ulong value_id) const
   {
      for(int i=0;i<ArraySize(m_written_value_ids);i++)
         if(m_written_value_ids[i]==value_id) return true;
      return false;
   }

   void MarkWritten(const ulong value_id)
   {
      if(WasWritten(value_id)) return;
      const int size=ArraySize(m_written_value_ids);
      ArrayResize(m_written_value_ids,size+1);
      m_written_value_ids[size]=value_id;
   }

   string CalendarValueText(const MqlCalendarValue &value,const int field) const
   {
      if(field==0) return value.HasActualValue() ? DoubleToString(value.GetActualValue(),6) : "";
      if(field==1) return value.HasForecastValue() ? DoubleToString(value.GetForecastValue(),6) : "";
      if(field==2) return value.HasPreviousValue() ? DoubleToString(value.GetPreviousValue(),6) : "";
      if(field==3) return value.HasRevisedValue() ? DoubleToString(value.GetRevisedValue(),6) : "";
      return "";
   }

public:
   CEconomicCalendarLogger()
   {
      m_enabled=false;
      m_base_path="";
      m_last_refresh_hour="";
      ArrayResize(m_written_value_ids,0);
   }

   void Init(const string base_path,const bool enabled)
   {
      m_base_path=base_path;
      m_enabled=(enabled && !((bool)MQLInfoInteger(MQL_TESTER)) && base_path!="");
      m_last_refresh_hour="";
      ArrayResize(m_written_value_ids,0);
   }

   bool RefreshIfDue(const datetime server_now,string &error)
   {
      error="";
      if(!m_enabled) return true;
      const string hour_key=BeijingHourKey(server_now);
      if(hour_key==m_last_refresh_hour) return true;
      m_last_refresh_hour=hour_key;

      MqlCalendarValue values[];
      const datetime from_time=server_now-6*60*60;
      const datetime to_time=server_now+30*60*60;
      ResetLastError();
      const int count=CalendarValueHistory(values,from_time,to_time,NULL,"USD");
      if(count<0)
      {
         error="MT5经济日历读取失败，错误="+IntegerToString(GetLastError());
         return false;
      }
      for(int i=0;i<count;i++)
      {
         if(WasWritten(values[i].id)) continue;
         MqlCalendarEvent event;
         if(!CalendarEventById(values[i].event_id,event)) continue;
         if(event.importance!=CALENDAR_IMPORTANCE_HIGH) continue;
         const string day=BeijingDateKey(values[i].time);
         const string filename=m_base_path+"\\Raw_Data\\Economic_Calendar_"+day+".csv";
         const string header="server_time,beijing_time,event_server_time,event_beijing_time,value_id,event_id,event_name,event_code,importance,actual,forecast,previous,revised,impact_type";
         const string row=ObservationCsvEscape(ObservationTimeText(server_now))+","+
            ObservationCsvEscape(ObservationTimeText(BeijingTimeFromServer(server_now)))+","+
            ObservationCsvEscape(ObservationTimeText(values[i].time))+","+
            ObservationCsvEscape(ObservationTimeText(BeijingTimeFromServer(values[i].time)))+","+
            IntegerToString((long)values[i].id)+","+IntegerToString((long)values[i].event_id)+","+
            ObservationCsvEscape(event.name)+","+ObservationCsvEscape(event.event_code)+","+
            ObservationCsvEscape(EnumToString((ENUM_CALENDAR_EVENT_IMPORTANCE)event.importance))+","+
            CalendarValueText(values[i],0)+","+CalendarValueText(values[i],1)+","+
            CalendarValueText(values[i],2)+","+CalendarValueText(values[i],3)+","+
            ObservationCsvEscape(EnumToString((ENUM_CALENDAR_EVENT_IMPACT)values[i].impact_type));
         if(AppendCommonUtf8CsvLine(filename,header,row)) MarkWritten(values[i].id);
      }
      return true;
   }
};

#endif
// ===== END INLINE: Include/XAUAI/EconomicCalendarLogger.mqh =====

// ===== BEGIN: Lark开仓卡片通知旁路模块（不参与交易决策） =====
void ResetLarkTradeContext(LarkTradeContext &ctx)
{
   ZeroMemory(ctx);
   ctx.signal_id="";
   ctx.ai_reason="";
   ctx.ai_is_error=false;
   ctx.direction=DIR_NONE;
   ctx.signal_route=SIGNAL_ROUTE_NONE;
}

string LarkSafeFileToken(string value)
{
   StringReplace(value,"\\","_");
   StringReplace(value,"/","_");
   StringReplace(value,":","_");
   StringReplace(value,"*","_");
   StringReplace(value,"?","_");
   StringReplace(value,"\"","_");
   StringReplace(value,"<","_");
   StringReplace(value,">","_");
   StringReplace(value,"|","_");
   return value;
}

string LarkPersistEncode(string value)
{
   StringReplace(value,"%","%25");
   StringReplace(value,"|","%7C");
   StringReplace(value,"\r","%0D");
   StringReplace(value,"\n","%0A");
   return value;
}

string LarkPersistDecode(string value)
{
   StringReplace(value,"%0A","\n");
   StringReplace(value,"%0D","\r");
   StringReplace(value,"%7C","|");
   StringReplace(value,"%25","%");
   return value;
}

bool IsIntegerString(const string value)
{
   const int len=StringLen(value);
   if(len==0) return false;
   for(int i=0;i<len;i++)
   {
      const ushort ch=(ushort)StringGetCharacter(value,i);
      if(ch<'0' || ch>'9') return false;
   }
   return true;
}

string SerializeLarkTradeContext(const LarkTradeContext &ctx)
{
   return LarkPersistEncode(ctx.signal_id)+"|"+
      IntegerToString((long)ctx.order_ticket)+"|"+
      IntegerToString((long)ctx.created_at)+"|"+
      IntegerToString((int)ctx.direction)+"|"+
      IntegerToString((int)ctx.signal_route)+"|"+
      DoubleToString(ctx.planned_entry,10)+"|"+
      DoubleToString(ctx.planned_sl,10)+"|"+
      DoubleToString(ctx.rr_to_tp1,6)+"|"+
      DoubleToString(ctx.fib_retracement,4)+"|"+
      IntegerToString(ctx.h_attempt)+"|"+
      DoubleToString(ctx.ema_distance_usd,6)+"|"+
      (ctx.sr_confluence?"1":"0")+"|"+
      (ctx.ma_confluence?"1":"0")+"|"+
      (ctx.pin_bar?"1":"0")+"|"+
      (ctx.hammer?"1":"0")+"|"+
      (ctx.engulfing?"1":"0")+"|"+
      (ctx.strong_reversal_bar?"1":"0")+"|"+
      (ctx.ai_allow_trade?"1":"0")+"|"+
      (ctx.ai_is_error?"1":"0")+"|"+
      IntegerToString(ctx.ai_confidence)+"|"+
      LarkPersistEncode(ctx.ai_reason);
}

bool ParseLarkTradeContext(const string line,LarkTradeContext &ctx)
{
   ResetLarkTradeContext(ctx);
   string fields[];
   const int count=StringSplit(line,'|',fields);
   if(count!=19 && count!=20 && count!=21) return false;
   ctx.signal_id=LarkPersistDecode(fields[0]);
   ctx.order_ticket=(ulong)StringToInteger(fields[1]);
   ctx.created_at=(datetime)StringToInteger(fields[2]);
   ctx.direction=(ENUM_TRADE_DIRECTION)StringToInteger(fields[3]);
   ctx.signal_route=(ENUM_SIGNAL_ROUTE)StringToInteger(fields[4]);
   ctx.planned_entry=StringToDouble(fields[5]);
   ctx.planned_sl=StringToDouble(fields[6]);
   ctx.rr_to_tp1=StringToDouble(fields[7]);
   ctx.fib_retracement=StringToDouble(fields[8]);
   ctx.h_attempt=(int)StringToInteger(fields[9]);
   ctx.ema_distance_usd=StringToDouble(fields[10]);
   ctx.sr_confluence=(fields[11]=="1");
   ctx.ma_confluence=(fields[12]=="1");
   ctx.pin_bar=(fields[13]=="1");
   ctx.hammer=(fields[14]=="1");
   ctx.engulfing=(fields[15]=="1");
   ctx.strong_reversal_bar=(fields[16]=="1");
   ctx.ai_allow_trade=(fields[17]=="1");
   if(count>=21)
   {
      // V3_9_14+ format: [17]=allow, [18]=is_error, [19]=confidence, [20]=reason
      ctx.ai_is_error=(fields[18]=="1");
      ctx.ai_confidence=(int)StringToInteger(fields[19]);
      ctx.ai_reason=LarkPersistDecode(fields[20]);
   }
   else if(count==20)
   {
      // 20 fields may be either V3_9_13 legacy ([18]=confidence, [19]=reason)
      // or V3_9_14+ with empty reason (StringSplit drops trailing empty,
      // so [18]=is_error, [19]=confidence).
      if((fields[18]=="0" || fields[18]=="1") && IsIntegerString(fields[19]))
      {
         ctx.ai_is_error=(fields[18]=="1");
         ctx.ai_confidence=(int)StringToInteger(fields[19]);
         ctx.ai_reason="";
      }
      else
      {
         ctx.ai_is_error=false;
         ctx.ai_confidence=(int)StringToInteger(fields[18]);
         ctx.ai_reason=LarkPersistDecode(fields[19]);
      }
   }
   else
   {
      // count==19: V3_9_13 legacy format with empty reason
      ctx.ai_is_error=false;
      ctx.ai_confidence=(int)StringToInteger(fields[18]);
      ctx.ai_reason="";
   }
   return (ctx.signal_id!="");
}

class CLarkContextStore
{
private:
   string m_context_file;
   string m_sent_file;
public:
   void Init(const string symbol,const long magic)
   {
      const string token=LarkSafeFileToken(symbol)+"_"+IntegerToString((long)magic);
      m_context_file="XAUAI_LarkContext_"+token+".csv";
      m_sent_file="XAUAI_LarkSentDeals_"+token+".csv";
   }

   bool Save(const LarkTradeContext &ctx,string &error)
   {
      error="";
      const int h=FileOpen(m_context_file,FILE_WRITE|FILE_TXT|FILE_ANSI|FILE_SHARE_READ,0,CP_UTF8);
      if(h==INVALID_HANDLE)
      {
         error="无法写入Lark开仓原因快照，MT5错误="+IntegerToString(GetLastError());
         return false;
      }
      FileWriteString(h,SerializeLarkTradeContext(ctx)+"\r\n");
      FileFlush(h);
      FileClose(h);
      return true;
   }

   bool Load(const string signal_id,const ulong order_ticket,LarkTradeContext &ctx,string &error)
   {
      error="";
      ResetLarkTradeContext(ctx);
      const int h=FileOpen(m_context_file,FILE_READ|FILE_TXT|FILE_ANSI|FILE_SHARE_READ|FILE_SHARE_WRITE,0,CP_UTF8);
      if(h==INVALID_HANDLE)
      {
         error="Lark开仓原因快照不存在";
         return false;
      }
      const string line=FileReadString(h);
      FileClose(h);
      if(!ParseLarkTradeContext(line,ctx))
      {
         error="Lark开仓原因快照格式无效";
         return false;
      }
      if(signal_id!="" && ctx.signal_id!=signal_id)
      {
         error="Lark开仓原因SignalID不匹配";
         return false;
      }
      if(order_ticket>0 && ctx.order_ticket>0 && ctx.order_ticket!=order_ticket)
      {
         error="Lark开仓原因OrderTicket不匹配";
         return false;
      }
      return true;
   }

   bool IsDealSent(const ulong deal_ticket)
   {
      if(deal_ticket==0) return false;
      const int h=FileOpen(m_sent_file,FILE_READ|FILE_TXT|FILE_ANSI|FILE_SHARE_READ|FILE_SHARE_WRITE,0,CP_UTF8);
      if(h==INVALID_HANDLE) return false;
      const string target=IntegerToString((long)deal_ticket);
      bool found=false;
      while(!FileIsEnding(h))
      {
         string line=FileReadString(h);
         StringTrimLeft(line); StringTrimRight(line);
         if(line==target){ found=true; break; }
      }
      FileClose(h);
      return found;
   }

   bool MarkDealSent(const ulong deal_ticket,string &error)
   {
      error="";
      if(deal_ticket==0){ error="DealTicket无效"; return false; }
      if(IsDealSent(deal_ticket)) return true;
      const int h=FileOpen(m_sent_file,FILE_READ|FILE_WRITE|FILE_TXT|FILE_ANSI|
                           FILE_SHARE_READ|FILE_SHARE_WRITE,0,CP_UTF8);
      if(h==INVALID_HANDLE)
      {
         error="无法写入Lark已发送Deal记录，MT5错误="+IntegerToString(GetLastError());
         return false;
      }
      FileSeek(h,0,SEEK_END);
      FileWriteString(h,IntegerToString((long)deal_ticket)+"\r\n");
      FileFlush(h);
      FileClose(h);
      return true;
   }
};

string ReadLarkWebhookFile(const string filename)
{
   const int h=FileOpen(filename,FILE_READ|FILE_TXT|FILE_ANSI|FILE_SHARE_READ,0,CP_UTF8);
   if(h==INVALID_HANDLE) return "";
   string value=FileReadString(h);
   FileClose(h);
   StringTrimLeft(value); StringTrimRight(value);
   return value;
}

class CLarkNotifier
{
private:
   bool   m_enabled;
   string m_webhook;
   int    m_timeout_ms;
public:
   CLarkNotifier(){ m_enabled=false; m_webhook=""; m_timeout_ms=3000; }

   bool Init(const bool enabled,const ENUM_LARK_SECRET_SOURCE source,
             const string input_webhook,const string webhook_file,
             const int timeout_ms,string &error)
   {
      error="";
      m_enabled=false;
      m_webhook="";
      m_timeout_ms=(int)MathMax(500.0,MathMin((double)timeout_ms,10000.0));
      if(!enabled) return true;
      if((bool)MQLInfoInteger(MQL_TESTER)) return true;
      m_webhook=(source==LARK_SECRET_FROM_FILE ? ReadLarkWebhookFile(webhook_file) : input_webhook);
      StringTrimLeft(m_webhook); StringTrimRight(m_webhook);
      if(m_webhook=="")
      {
         error="Lark Webhook为空，已禁用开仓卡片通知";
         return false;
      }
      if(StringFind(m_webhook,"https://")!=0)
      {
         error="Lark Webhook格式无效，必须以https://开头";
         m_webhook="";
         return false;
      }
      m_enabled=true;
      return true;
   }

   bool IsEnabled() const { return m_enabled; }

   bool SendCard(const string card_json,string &error)
   {
      error="";
      if(!m_enabled || (bool)MQLInfoInteger(MQL_TESTER)) return false;
      if(card_json==""){ error="Lark卡片JSON为空"; return false; }
      char data[];
      StringToCharArray(card_json,data,0,WHOLE_ARRAY,CP_UTF8);
      if(ArraySize(data)>0) ArrayResize(data,ArraySize(data)-1);
      const string headers="Content-Type: application/json; charset=utf-8\r\n";
      for(int attempt=1;attempt<=3;attempt++)
      {
         char result[];
         string result_headers="";
         ResetLastError();
         const int http_code=WebRequest("POST",m_webhook,headers,m_timeout_ms,
                                        data,result,result_headers);
         const int mt5_error=GetLastError();
         string response=CharArrayToString(result,0,WHOLE_ARRAY,CP_UTF8);
         StringReplace(response,"\r"," ");
         StringReplace(response,"\n"," ");
         StringReplace(result_headers,"\r"," ");
         StringReplace(result_headers,"\n"," ");
         if(StringLen(response)>500) response=StringSubstr(response,0,500);
         if(StringLen(result_headers)>500) result_headers=StringSubstr(result_headers,0,500);
         const bool lark_ok=(StringFind(response,"\"code\":0")>=0 ||
                             StringFind(response,"\"StatusCode\":0")>=0);
         if(http_code==200 && lark_ok) return true;
         error="Lark发送失败 | Attempt="+IntegerToString(attempt)+"/3 | HTTP="+
               IntegerToString(http_code)+" | MT5Error="+IntegerToString(mt5_error)+
               " | Response="+response+" | Headers="+result_headers;
         if(attempt<3) Sleep(250);
      }
      return false;
   }
};
// ===== END: Lark开仓卡片通知旁路模块 =====

#ifndef XAUAI_STAGED_EXIT_UNIT_TEST
input group "【基础设置】"
input long InpMagicNumber=V310_EA_MAGIC; // V3.10.7 EA Trader A Magic
input ENUM_AI_MODE InpAIMode=AI_OFF; // AI运行模式

input group "【趋势参数】"
input int InpEMAPeriod=20; // EMA周期
input int InpATRPeriod=14; // ATR周期
input int InpRSIPeriod=14; // RSI周期
input int InpMACDFast=12; // MACD快线
input int InpMACDSlow=26; // MACD慢线
input int InpMACDSignal=9; // MACD信号线
input double InpMinThreeBarMoveUSD=10.0; // 连续三根推进K线最小总幅度（美元）
input double InpEMANearDistanceUSD=3.0; // H2/H3回调点距EMA20最大距离（美元）

input group "【Swing参数】"
input int InpPivotLeft=2; // Pivot左侧确认K线数
input int InpPivotRight=2; // Pivot右侧确认K线数
input double InpMinImpulseATR=1.5; // 最小推动波段ATR倍数

input group "【Fib参数】"
input double InpFibMin=38.2; // Fib有效区下限
input double InpFibMax=61.8; // Fib有效区上限
input double InpFibInvalidBufferATR=0.10; // Fib失效ATR缓冲
input double InpSRToleranceATR=0.20; // S/R共振ATR距离

input group "【Price Action参数】"
input int InpMinAttemptSeparationBars=1; // H尝试最小间隔K线
input double InpPinBarWickBodyRatio=2.0; // Pin Bar影线实体比
input double InpStrongBarBodyRatio=0.60; // 强反转K实体比例
input ENUM_DIVERGENCE_MODE InpDivergenceMode=DIVERGENCE_OFF; // 背离模式
input double InpMinRSIDivergence=3.0; // RSI背离最小差值

input group "【AI参数】"
input string InpDeepSeekModel="deepseek-v4-flash"; // DeepSeek模型
input ENUM_API_KEY_SOURCE InpAPIKeySource=KEY_FROM_FILE; // API Key来源
input string InpDeepSeekApiKey=""; // API Key（日志永不输出）
input string InpAPIKeyFile="deepseek_api_key.txt"; // MQL5/Files下Key文件
input string InpReplayFile="AI_Decisions_Replay.csv"; // AI回放文件
input int InpAIConfidenceThreshold=70; // AI最低放行置信度
input int InpAIRetryCount=1; // API失败最大重试次数
input int InpAITimeoutMs=3000; // API超时毫秒
input double InpMaxPostAIMoveATR=0.25; // AI返回后最大允许价格移动ATR
input bool InpAIFailOpenGrace=true; // AI审核异常降级放行（重试失败后按本地信号继续）


input group "【AI盯盘数据采集】"
input bool InpObservationLoggingEnabled=true; // 启用只读盯盘与复盘数据采集
input string InpObservationRootFolder=V310_DATA_ROOT; // V3.10.7独立公共Files根目录
input bool InpObservationInTester=false; // 策略测试器是否写入盯盘数据（默认关闭）
input bool InpEconomicCalendarLoggingEnabled=true; // 记录MT5内置USD高影响经济日历

input group "【经纪商收盘复盘】"
input bool InpBrokerCloseReviewEnabled=true; // 按XAUUSD.s最后交易时段触发每日复盘
input int InpBrokerCloseReviewDelayMinutes=5; // 收盘后等待数据落盘分钟数
input int InpBrokerCloseFallbackHour=23; // 无法读取交易时段时备用收盘小时（服务器时间）
input int InpBrokerCloseFallbackMinute=55; // 无法读取交易时段时备用收盘分钟（服务器时间）

input group "【入场参数】"
input bool InpV310LongOnly=false; // V3.10.7=false启用BUY+SELL镜像；true仅保留BUY
input double InpV310SignalStopUSD=2.0; // 信号K低点下方固定美元止损
input double InpV310IntegerStopExtraUSD=0.3; // 整数止损额外下移美元
input double InpV310LocationToleranceATR=0.15; // 四类位置容差
input double InpV310ImpulseBufferATR=0.10; // 新冲动突破ATR缓冲
input int InpV310M15Lookback=100; // 仅使用最近100根已收盘M15
input int InpV310M15RangeWindow=20; // M15 RangeLike窗口
input int InpV310M5RangeMinBars=5; // M5回调RangeLike最少K线
input double InpV310RangeOverlap=0.60; // 相邻K线重叠阈值
input double InpV310RangeOverlapShare=0.60; // 重叠占比阈值
input double InpV310RangeNetEfficiencyMax=0.35; // 净效率上限
input int InpEntryBufferPoints=0; // 入场点数缓冲
input double InpEntryBufferATR=0.05; // 入场ATR缓冲
input int InpStopBufferPoints=0; // 止损点数缓冲
input double InpStopBufferATR=0.10; // 止损ATR缓冲
input double InpEMASignalBarStopUSD=2.0; // EMA H2/H3、L2/L3路径：信号K高低点向外扩的固定美元距离
input double InpEMAMaxStopExpansionRatio=1.50; // EMA新止损相对Legacy风险最大扩张倍数(>=1.0)

input group "【风险控制】"
input double InpRiskPercent=0.5; // 单笔账户风险(%)，硬上限1%
input double InpMaxSLATR=2.0; // 最大止损ATR倍数
input double InpMinRRToTP1=0.80; // 旧Swing结构空间最低R（原1.20）
input double InpMaxSpreadATRRatio=0.08; // 最大Spread/ATR比例
input int InpAbsoluteMaxSpreadPoints=0; // 绝对最大点差，0为关闭
input double InpMaxSignalBarUSD=10.0; // 信号K线长度必须严格小于此美元值

input group "【每日换日风险控制】"
input bool InpRolloverGuardEnabled=true; // 启用服务器换日时段禁开仓并删除挂单
input int InpRolloverStartHour=23; // 换日保护开始小时（服务器时间）
input int InpRolloverStartMinute=30; // 换日保护开始分钟（服务器时间）
input int InpRolloverEndHour=1; // 换日保护结束小时（服务器时间）
input int InpRolloverEndMinute=30; // 换日保护结束分钟（服务器时间）

input group "【周末风险控制】"
input bool InpWeekendGuardEnabled=true; // 启用周末禁开仓和强制清仓
input int InpWeekendNoNewTradeMinutes=180; // 周五休市前多少分钟停止开仓并删除挂单
input int InpWeekendForceCloseMinutes=60; // 周五休市前多少分钟强制平掉本EA持仓
input int InpWeekendFallbackFridayCloseHour=23; // 无法读取交易时段时备用休市小时（服务器时间）
input int InpWeekendFallbackFridayCloseMinute=55; // 无法读取交易时段时备用休市分钟（服务器时间）

input group "【出场管理】"
input int InpPendingExpiryBars=3; // 挂单有效信号周期K线数
input bool InpUseStagedExit=true; // 启用1R、2R分段止盈
input double InpTP1RiskMultiple=1.0; // 第一止盈R倍数
input double InpTP1ClosePercent=50.0; // 第一止盈占原始仓位比例
input bool InpMoveToBreakEvenAfterTP1=true; // 第一止盈后移动保本
input double InpBreakEvenSpreadMultiplier=0.0; // V3.10.7保本精确到实际入场价，无缓冲
input int InpBreakEvenMinBufferPoints=0; // V3.10.7保本不增加点数
input double InpTP2RiskMultiple=2.0; // 第二止盈R倍数
input double InpTP2ClosePercent=100.0; // 第二止盈全部退出剩余仓位
input double InpTP2LockRiskMultiple=1.0; // 第二止盈后锁定的R倍数
input bool InpUseStructureTargetAsServerTP=false; // V3.10.7标准模式不使用Runner/结构TP

input group "【日志与提醒】"
input bool InpDebugMode=false; // 输出详细规则诊断
input bool InpWriteCandidateCSV=true; // 写入候选CSV
input bool InpEnablePopupAlert=true; // 启用MT5弹窗提醒
input bool InpEnablePushNotification=false; // 启用手机推送提醒

input group "【Lark开仓通知】"
input bool InpLarkOpenNotifyEnabled=true; // 启用真实成交后的Lark卡片通知
input ENUM_LARK_SECRET_SOURCE InpLarkWebhookSource=LARK_SECRET_FROM_FILE; // Webhook来源
input string InpLarkWebhook=""; // 完整Webhook（日志永不输出）
input string InpLarkWebhookFile="lark_webhook.txt"; // MQL5/Files下Webhook文件
input int InpLarkTimeoutMs=3000; // Lark请求超时毫秒

CIndicatorManager g_indicators;
CMarketStructure g_structure;
CFibonacciModule g_fibonacci;
CPriceAction g_price_action;
CSignalEngine g_signal_engine;
CDeepSeekClient g_ai;
CRiskManager g_risk;
CTradeManager g_trade_slots[V3109C32_MAX_SLOTS];
// Transitional compatibility alias. Candidate routing is switched from slot 0
// to the selected free slot in the following integration step.
#define g_trades g_trade_slots[0]
CCsvLogger g_csv;
CObservationLogger g_observation;
CEconomicCalendarLogger g_calendar;
CLarkNotifier g_lark;
CLarkContextStore g_lark_context_store;
CProcessedSignalSet g_processed;
CProcessedSignalSet g_notified_events; // 本次EA运行期间已输出的事件Key，防止重复刷屏
PendingSignalWaitState g_waiting_pending; // 距离不足时仅保存信号，不创建真实订单
bool g_independent_risk_mode_logged=false;
int g_independent_risk_history_error_date=0;
datetime g_last_bar=0;
datetime g_last_daily_review_check_server=0;
datetime g_last_daily_review_diagnostic_server=0;
// 跨周遗留敞口只在每个交易周首次有效Tick检查一次。
// 若清理失败则不写入本周标记；市场关闭时限频重试，避免每个Tick重复发送平仓请求。
datetime g_prior_week_cleanup_checked_week_start=0;
bool g_prior_week_cleanup_pending=false;
datetime g_weekend_force_flat_retry_after=0;
datetime g_weekend_force_flat_retry_week_start=0;
const int WEEKEND_FORCE_FLAT_MARKET_CLOSED_RETRY_SECONDS=60;
const int WEEKEND_FORCE_FLAT_ERROR_RETRY_SECONDS=5;
ENUM_TIMEFRAMES g_signal_timeframe=PERIOD_M5;
string g_last_scan_text="尚未扫描";
string g_ai_connection_text="未测试";
string g_weekend_status_text="等待计算";
string g_rollover_status_text="等待计算";
ScanResult g_last_scan_result;
EmaHybridStats g_ema_hybrid;

// ===== BEGIN: Parallel AI / EA entry audit V2 (observation only) =====
// This exporter never calls the trade subsystem.  It only publishes immutable
// closed-M5 inputs and a physically separate EA decision trace to FILE_COMMON.
const string PARALLEL_AUDIT_RULE_VERSION="EA_OPEN_V3_10_9_C33_TF1_PURE_TREND_FREQUENCY_R1";
const string V310_PARAMETER_VERSION="M15_EARLY_TREND_M5_FREQUENCY_3SLOT_C33TF1";
const int V310_CLOSED_SHIFT=1;
const int V310_M15_LOOKBACK=100;
const int V310_C3_MACRO_LOOKBACK=500;
datetime g_v310_last_m15_closed=0;
datetime g_v310_last_m5_closed=0;
V310M15Result g_v310_m15_context;
V310M15Metrics g_v310_m15_metrics;
V3109M15TrendContext g_v3109_m15_trend;
V3109M15TrendMemory g_v3109_m15_memory;
int g_v3109_c3_m15_macro_bias=0;
bool g_v3109_c3_buy_tactical_counter=false;
bool g_v3109_c3_sell_tactical_counter=false;
bool g_v3109_c33_early_buy=false;
bool g_v3109_c33_early_sell=false;
string g_v3109_c33_early_reason="C33_EARLY_NONE";
bool g_v3109_c33_m15_replay_ready=false;
V310PullbackState g_v310_pullback;
V310TradePlan g_v310_candidate_plan;
V310SignalResult g_v310_candidate_signal;
V310Bar g_v310_pullback_bars[];
bool g_v310_impulse_confirmed=false;
bool g_v310_candidate_ready=false;
datetime g_v310_candidate_signal_time=0;
double g_v310_impulse_low=0.0;
double g_v310_impulse_high=0.0;
double g_v310_nearest_resistance=0.0;
V310Bar g_v310_m15_target_bars[];
int g_v310_routea_target_diff_buy=0;
V310Bar g_v310_candidate_bar;
double g_v310_candidate_atr=0.0;
double g_v310_candidate_resistance=0.0;
double g_v310_pending_signal_low=0.0;
bool g_v310_candidate_ai_reviewed=false;
AIDecision g_v310_candidate_ai_decision;
string g_v310_candidate_route="";
string g_v310_emitted_setup_key="";
bool g_v3109_c3_buy_counter=false;
int g_v3109_c3_buy_attempt=0;

// V3.10.7 mirrored SELL state. BUY globals above remain the V3.10 baseline path.
V310M15BearResult g_v310_sell_m15_context;
V310M15BearMetrics g_v310_sell_m15_metrics;
V310BearPullbackState g_v310_sell_pullback;
V310TradePlan g_v310_sell_candidate_plan;
V310BearSignalResult g_v310_sell_candidate_signal;
V310SignalResult g_v310_sell_candidate_location;
V310Bar g_v310_sell_pullback_bars[];
bool g_v310_sell_impulse_confirmed=false;
bool g_v310_sell_candidate_ready=false;
datetime g_v310_sell_candidate_signal_time=0;
double g_v310_sell_impulse_high=0.0;
double g_v310_sell_impulse_low=0.0;
double g_v310_sell_nearest_support=0.0;
V310Bar g_v310_sell_m15_target_bars[];
int g_v310_routea_target_diff_sell=0;
V310Bar g_v310_sell_candidate_bar;
double g_v310_sell_candidate_atr=0.0;
double g_v310_sell_candidate_support=0.0;
double g_v310_pending_signal_high=0.0;
bool g_v310_sell_candidate_ai_reviewed=false;
AIDecision g_v310_sell_candidate_ai_decision;
string g_v310_sell_candidate_route="";
string g_v310_sell_emitted_setup_key="";
bool g_v3109_c3_sell_counter=false;
int g_v3109_c3_sell_attempt=0;

int g_v3109_c32_candidate_slot=-1;
datetime g_v3109_c32_last_fill_bar_time=0;
V3109C32MicroCycleState g_v3109_c32_micro_cycles[2];

struct V3109C32PendingContext
{
   bool active;
   int slot_id;
   long slot_magic;
   ENUM_TRADE_DIRECTION direction;
   bool countertrend;
   int attempt_no;
   datetime trend_segment_id;
   datetime micro_pullback_start_time;
   datetime signal_bar_time;
   double signal_low;
   double signal_high;
   double planned_risk_usd;
   double aggregate_risk_usd;
   string setup_key;
};
V3109C32PendingContext g_v3109_c32_pending_contexts[V3109C32_MAX_SLOTS];

// C3 primary-trend segment accounting.  A countertrend fill is permitted only
// after three same-primary fills and at most once per M15 exhaustion event.
V3109C3QuotaState g_v3109_c3_quota;
bool g_v3109_c3_pending_counter=false;
ENUM_TRADE_DIRECTION g_v3109_c3_pending_direction=DIR_NONE;
int g_v3109_c3_pending_attempt=0;
string g_v3109_c3_pending_setup_key="";

// V3.10.7 observation-only per-side diagnostics. These fields never gate trading.
struct V3102SideDiagnostics
{
   bool signal_valid;
   bool location_valid;
   bool plan_evaluated;
   bool plan_valid;
   string plan_reason;
   string reset_reason;
};
V3102SideDiagnostics g_v310_buy_diag;
V3102SideDiagnostics g_v310_sell_diag;

void V3109C32ResetPendingContext(V3109C32PendingContext &ctx)
{
   ZeroMemory(ctx);
   ctx.active=false;
   ctx.slot_id=-1;
   ctx.slot_magic=-1;
   ctx.direction=DIR_NONE;
   ctx.setup_key="";
}

void V3109C32ResetAllPendingContexts()
{
   for(int slot=0;slot<V3109C32_MAX_SLOTS;slot++)
      V3109C32ResetPendingContext(g_v3109_c32_pending_contexts[slot]);
}

void V3102ClearSideDiagnostics(V3102SideDiagnostics &diag,const bool keep_reset_reason=false)
{
   const string reset_reason=(keep_reset_reason ? diag.reset_reason : "");
   ZeroMemory(diag);
   diag.plan_reason="NOT_EVALUATED";
   diag.reset_reason=reset_reason;
}

const int PARALLEL_AUDIT_SCHEMA_VERSION=2;
const int PARALLEL_AUDIT_HASH_MATERIAL_VERSION=1;
const string PARALLEL_AUDIT_INPUT_DIR="Parallel_AI_V2\\Independent_Input\\Pending";
const string PARALLEL_AUDIT_TRACE_DIR="Parallel_AI_V2\\EA_Trace\\Pending";

string g_parallel_audit_snapshot_id="";
string g_parallel_audit_input_hash="";
bool g_parallel_audit_input_ready=false;
double g_parallel_audit_ema20=0.0;
double g_parallel_audit_atr14=0.0;
double g_parallel_audit_rsi14=0.0;
double g_parallel_audit_macd_hist=0.0;
double g_parallel_audit_fib_retracement=0.0;

class CParallelAuditExporter
{
public:
   bool Ready() const
   {
      return g_parallel_audit_input_ready;
   }
};

CParallelAuditExporter g_parallel_audit_exporter;

string ParallelAuditNumber(const double value)
{
   string text=DoubleToString(value,8);
   while(StringFind(text,".")>=0 && StringSubstr(text,StringLen(text)-1)=="0")
      text=StringSubstr(text,0,StringLen(text)-1);
   if(StringSubstr(text,StringLen(text)-1)==".") text=StringSubstr(text,0,StringLen(text)-1);
   if(text=="-0" || text=="") return "0";
   return text;
}

string ParallelAuditBool(const bool value)
{
   return value ? "true" : "false";
}

string ParallelAuditSnapshotId(const string symbol,const datetime m5_time)
{
   string safe_symbol=symbol;
   StringReplace(safe_symbol," ","_");
   StringReplace(safe_symbol,"/","_");
   StringReplace(safe_symbol,"\\","_");
   MqlDateTime parts;
   TimeToStruct(m5_time,parts);
   return safe_symbol+"_M5_"+
      StringFormat("%04d%02d%02d_%02d%02d",parts.year,parts.mon,parts.day,parts.hour,parts.min);
}

string ParallelAuditNormalizeSnapshotId(string value)
{
   StringReplace(value,".","");
   StringReplace(value,":","");
   StringReplace(value," ","_");
   // Restore the symbol separator removed from the date portion only.
   const string marker="_M5_";
   const int split=StringFind(value,marker);
   if(split>0)
   {
      string symbol=StringSubstr(value,0,split);
      string suffix=StringSubstr(value,split);
      if(symbol=="XAUUSDs") symbol="XAUUSD.s";
      value=symbol+suffix;
   }
   return value;
}

string ParallelAuditInputHash(const string material)
{
   uchar data[];
   uchar key[];
   uchar digest[];
   const int copied=StringToCharArray(material,data,0,WHOLE_ARRAY,CP_UTF8);
   if(copied<=0) return "";
   if(ArraySize(data)>0 && data[ArraySize(data)-1]==0)
      ArrayResize(data,ArraySize(data)-1);
   if(CryptEncode(CRYPT_HASH_SHA256,data,key,digest)<=0) return "";
   string result="";
   for(int i=0;i<ArraySize(digest);i++) result+=StringFormat("%02x",digest[i]);
   return result;
}

string ParallelAuditParametersJson()
{
   return "{"+
      "\"absolute_max_spread_points\":"+IntegerToString(InpAbsoluteMaxSpreadPoints)+","+
      "\"ai_confidence_threshold\":"+IntegerToString(InpAIConfidenceThreshold)+","+
      "\"atr_period\":"+IntegerToString(InpATRPeriod)+","+
      "\"ema_near_distance_usd\":"+ParallelAuditNumber(InpEMANearDistanceUSD)+","+
      "\"ema_period\":"+IntegerToString(InpEMAPeriod)+","+
      "\"ema_signal_bar_stop_usd\":"+ParallelAuditNumber(InpEMASignalBarStopUSD)+","+
      "\"ema_max_stop_expansion_ratio\":"+ParallelAuditNumber(InpEMAMaxStopExpansionRatio)+","+
      "\"entry_buffer_atr\":"+ParallelAuditNumber(InpEntryBufferATR)+","+
      "\"entry_buffer_points\":"+IntegerToString(InpEntryBufferPoints)+","+
      "\"fib_invalid_buffer_atr\":"+ParallelAuditNumber(InpFibInvalidBufferATR)+","+
      "\"fib_max\":"+ParallelAuditNumber(InpFibMax)+","+
      "\"fib_min\":"+ParallelAuditNumber(InpFibMin)+","+
      "\"location_tolerance_atr\":"+ParallelAuditNumber(InpV310LocationToleranceATR)+","+
      "\"macd_fast\":"+IntegerToString(InpMACDFast)+","+
      "\"macd_signal\":"+IntegerToString(InpMACDSignal)+","+
      "\"macd_slow\":"+IntegerToString(InpMACDSlow)+","+
      "\"max_post_ai_move_atr\":"+ParallelAuditNumber(InpMaxPostAIMoveATR)+","+
      "\"max_signal_bar_usd\":"+ParallelAuditNumber(InpMaxSignalBarUSD)+","+
      "\"max_sl_atr\":"+ParallelAuditNumber(InpMaxSLATR)+","+
      "\"max_spread_atr_ratio\":"+ParallelAuditNumber(InpMaxSpreadATRRatio)+","+
      "\"min_attempt_separation_bars\":"+IntegerToString(InpMinAttemptSeparationBars)+","+
      "\"min_impulse_atr\":"+ParallelAuditNumber(InpMinImpulseATR)+","+
      "\"min_rr_to_tp1\":"+ParallelAuditNumber(InpMinRRToTP1)+","+
      "\"min_three_bar_move_usd\":"+ParallelAuditNumber(InpMinThreeBarMoveUSD)+","+
      "\"pin_bar_wick_body_ratio\":"+ParallelAuditNumber(InpPinBarWickBodyRatio)+","+
      "\"pivot_left\":"+IntegerToString(InpPivotLeft)+","+
      "\"pivot_right\":"+IntegerToString(InpPivotRight)+","+
      "\"rsi_period\":"+IntegerToString(InpRSIPeriod)+","+
      "\"sr_tolerance_atr\":"+ParallelAuditNumber(InpSRToleranceATR)+","+
      "\"stop_buffer_atr\":"+ParallelAuditNumber(InpStopBufferATR)+","+
      "\"stop_buffer_points\":"+IntegerToString(InpStopBufferPoints)+","+
      "\"strong_bar_body_ratio\":"+ParallelAuditNumber(InpStrongBarBodyRatio)+"}";
}

string ParallelAuditParametersMaterial()
{
   return "param.absolute_max_spread_points="+IntegerToString(InpAbsoluteMaxSpreadPoints)+"\n"+
      "param.ai_confidence_threshold="+IntegerToString(InpAIConfidenceThreshold)+"\n"+
      "param.atr_period="+IntegerToString(InpATRPeriod)+"\n"+
      "param.ema_max_stop_expansion_ratio="+ParallelAuditNumber(InpEMAMaxStopExpansionRatio)+"\n"+
      "param.ema_near_distance_usd="+ParallelAuditNumber(InpEMANearDistanceUSD)+"\n"+
      "param.ema_period="+IntegerToString(InpEMAPeriod)+"\n"+
      "param.ema_signal_bar_stop_usd="+ParallelAuditNumber(InpEMASignalBarStopUSD)+"\n"+
      "param.entry_buffer_atr="+ParallelAuditNumber(InpEntryBufferATR)+"\n"+
      "param.entry_buffer_points="+IntegerToString(InpEntryBufferPoints)+"\n"+
      "param.fib_invalid_buffer_atr="+ParallelAuditNumber(InpFibInvalidBufferATR)+"\n"+
      "param.fib_max="+ParallelAuditNumber(InpFibMax)+"\n"+
      "param.fib_min="+ParallelAuditNumber(InpFibMin)+"\n"+
      "param.location_tolerance_atr="+ParallelAuditNumber(InpV310LocationToleranceATR)+"\n"+
      "param.macd_fast="+IntegerToString(InpMACDFast)+"\n"+
      "param.macd_signal="+IntegerToString(InpMACDSignal)+"\n"+
      "param.macd_slow="+IntegerToString(InpMACDSlow)+"\n"+
      "param.max_post_ai_move_atr="+ParallelAuditNumber(InpMaxPostAIMoveATR)+"\n"+
      "param.max_signal_bar_usd="+ParallelAuditNumber(InpMaxSignalBarUSD)+"\n"+
      "param.max_sl_atr="+ParallelAuditNumber(InpMaxSLATR)+"\n"+
      "param.max_spread_atr_ratio="+ParallelAuditNumber(InpMaxSpreadATRRatio)+"\n"+
      "param.min_attempt_separation_bars="+IntegerToString(InpMinAttemptSeparationBars)+"\n"+
      "param.min_impulse_atr="+ParallelAuditNumber(InpMinImpulseATR)+"\n"+
      "param.min_rr_to_tp1="+ParallelAuditNumber(InpMinRRToTP1)+"\n"+
      "param.min_three_bar_move_usd="+ParallelAuditNumber(InpMinThreeBarMoveUSD)+"\n"+
      "param.pin_bar_wick_body_ratio="+ParallelAuditNumber(InpPinBarWickBodyRatio)+"\n"+
      "param.pivot_left="+IntegerToString(InpPivotLeft)+"\n"+
      "param.pivot_right="+IntegerToString(InpPivotRight)+"\n"+
      "param.rsi_period="+IntegerToString(InpRSIPeriod)+"\n"+
      "param.sr_tolerance_atr="+ParallelAuditNumber(InpSRToleranceATR)+"\n"+
      "param.stop_buffer_atr="+ParallelAuditNumber(InpStopBufferATR)+"\n"+
      "param.stop_buffer_points="+IntegerToString(InpStopBufferPoints)+"\n"+
      "param.strong_bar_body_ratio="+ParallelAuditNumber(InpStrongBarBodyRatio)+"\n";
}

string ParallelAuditAccountStateJson(const bool risk_locked,const datetime server_now)
{
   const bool exposure=V3109C32HasManagedExposure();
   const bool trade_allowed=(bool)TerminalInfoInteger(TERMINAL_TRADE_ALLOWED);
   return "{"+
      "\"account_login\":"+IntegerToString(AccountInfoInteger(ACCOUNT_LOGIN))+","+
      "\"existing_exposure\":"+ParallelAuditBool(exposure)+","+
      "\"risk_locked\":"+ParallelAuditBool(risk_locked)+","+
      "\"trade_allowed\":"+ParallelAuditBool(trade_allowed)+"}";
}

string ParallelAuditAccountStateMaterial(const bool risk_locked,const datetime server_now)
{
   const bool exposure=V3109C32HasManagedExposure();
   const bool trade_allowed=(bool)TerminalInfoInteger(TERMINAL_TRADE_ALLOWED);
   return "state.account_login="+IntegerToString(AccountInfoInteger(ACCOUNT_LOGIN))+"\n"+
      "state.existing_exposure="+ParallelAuditBool(exposure)+"\n"+
      "state.risk_locked="+ParallelAuditBool(risk_locked)+"\n"+
      "state.trade_allowed="+ParallelAuditBool(trade_allowed)+"\n";
}

string BuildParallelCanonicalMaterial(const MarketSnapshot &bars[],const MqlRates &m15_bars[],const double bid,
                                      const double ask,const bool risk_locked,
                                      const string snapshot_id)
{
   if(ArraySize(bars)!=200 || ArraySize(m15_bars)!=100) return "";
   const datetime server_now=TimeTradeServer();
   double tick_size=SymbolInfoDouble(_Symbol,SYMBOL_TRADE_TICK_SIZE);
   if(tick_size<=0.0) tick_size=_Point;
   string material="hash_material_version=1\n"+
      "schema_version=2\n"+
      "rule_version="+PARALLEL_AUDIT_RULE_VERSION+"\n"+
      "parameter_version="+V310_PARAMETER_VERSION+"\n"+
      "snapshot_id="+snapshot_id+"\n"+
      "symbol="+_Symbol+"\n"+
      "timeframe=M5\n"+
      "m5_time="+TimeToString(bars[199].time,TIME_DATE|TIME_SECONDS)+"\n"+
      "bid="+ParallelAuditNumber(bid)+"\n"+
      "ask="+ParallelAuditNumber(ask)+"\n"+
      "tick_size="+ParallelAuditNumber(tick_size)+"\n"+
      ParallelAuditParametersMaterial()+
      ParallelAuditAccountStateMaterial(risk_locked,server_now);
   for(int i=0;i<200;i++)
   {
      material+="bar."+StringFormat("%03d",i)+"="+
         TimeToString(bars[i].time,TIME_DATE|TIME_SECONDS)+"|"+
         ParallelAuditNumber(bars[i].open)+"|"+
         ParallelAuditNumber(bars[i].high)+"|"+
         ParallelAuditNumber(bars[i].low)+"|"+
         ParallelAuditNumber(bars[i].close)+"|"+
         IntegerToString((long)bars[i].tick_volume);
      material+="\n";
   }
   for(int i=0;i<100;i++)
   {
      material+="m15bar."+StringFormat("%03d",i)+"="+
         TimeToString(m15_bars[i].time,TIME_DATE|TIME_SECONDS)+"|"+
         ParallelAuditNumber(m15_bars[i].open)+"|"+
         ParallelAuditNumber(m15_bars[i].high)+"|"+
         ParallelAuditNumber(m15_bars[i].low)+"|"+
         ParallelAuditNumber(m15_bars[i].close)+"|"+
         IntegerToString((long)m15_bars[i].tick_volume);
      if(i<99) material+="\n";
   }
   return material;
}

string BuildParallelIndependentInputJson(const MarketSnapshot &bars[],const MqlRates &m15_bars[],const double bid,
                                         const double ask,const bool risk_locked,
                                         const string snapshot_id,const string input_hash)
{
   if(ArraySize(bars)!=200 || ArraySize(m15_bars)!=100) return "";
   const datetime server_now=TimeTradeServer();
   double tick_size=SymbolInfoDouble(_Symbol,SYMBOL_TRADE_TICK_SIZE);
   if(tick_size<=0.0) tick_size=_Point;
   string json="{\"hash_material_version\":1,\"schema_version\":2,"+
      "\"rule_version\":\""+PARALLEL_AUDIT_RULE_VERSION+"\","+
      "\"parameter_version\":\""+V310_PARAMETER_VERSION+"\","+
      "\"snapshot_id\":\""+JsonEscape(snapshot_id)+"\","+
      "\"symbol\":\""+JsonEscape(_Symbol)+"\",\"timeframe\":\"M5\","+
      "\"m5_time\":\""+TimeToString(bars[199].time,TIME_DATE|TIME_SECONDS)+"\","+
      "\"bid\":"+ParallelAuditNumber(bid)+",\"ask\":"+ParallelAuditNumber(ask)+","+
      "\"tick_size\":"+ParallelAuditNumber(tick_size)+","+
      "\"parameters\":"+ParallelAuditParametersJson()+","+
      "\"account_state\":"+ParallelAuditAccountStateJson(risk_locked,server_now)+","+
      "\"bars\":[";
   for(int i=0;i<200;i++)
   {
      if(i>0) json+=",";
      json+="{\"time\":\""+TimeToString(bars[i].time,TIME_DATE|TIME_SECONDS)+"\","+
         "\"open\":"+ParallelAuditNumber(bars[i].open)+","+
         "\"high\":"+ParallelAuditNumber(bars[i].high)+","+
         "\"low\":"+ParallelAuditNumber(bars[i].low)+","+
         "\"close\":"+ParallelAuditNumber(bars[i].close)+","+
         "\"tick_volume\":"+IntegerToString((long)bars[i].tick_volume)+"}";
   }
   json+="],\"m15_bars\":[";
   for(int i=0;i<100;i++)
   {
      if(i>0) json+=",";
      json+="{\"time\":\""+TimeToString(m15_bars[i].time,TIME_DATE|TIME_SECONDS)+"\","+
         "\"open\":"+ParallelAuditNumber(m15_bars[i].open)+","+
         "\"high\":"+ParallelAuditNumber(m15_bars[i].high)+","+
         "\"low\":"+ParallelAuditNumber(m15_bars[i].low)+","+
         "\"close\":"+ParallelAuditNumber(m15_bars[i].close)+","+
         "\"tick_volume\":"+IntegerToString((long)m15_bars[i].tick_volume)+"}";
   }
   return json+"],\"input_hash\":\""+input_hash+"\"}";
}

bool AtomicWriteParallelAuditJson(const string directory,const string filename,
                                  const string json,string &error)
{
   error="";
   const string base=g_observation.BasePath()+"\\Parallel_AI_V2";
   const string final_dir=g_observation.BasePath()+"\\"+directory;
   FolderCreate(base,FILE_COMMON);
   FolderCreate(final_dir,FILE_COMMON);
   const string final_path=final_dir+"\\"+filename;
   const string temp_path=final_path+".tmp";
   const int handle=FileOpen(temp_path,FILE_WRITE|FILE_TXT|FILE_ANSI|FILE_COMMON|
                             FILE_SHARE_READ,0,CP_UTF8);
   if(handle==INVALID_HANDLE)
   {
      error="parallel audit temp open failed: "+IntegerToString(GetLastError());
      return false;
   }
   FileWriteString(handle,json);
   FileFlush(handle);
   FileClose(handle);
   if(!FileMove(temp_path,FILE_COMMON,final_path,FILE_REWRITE|FILE_COMMON))
   {
      error="parallel audit atomic publish failed: "+IntegerToString(GetLastError());
      FileDelete(temp_path,FILE_COMMON);
      return false;
   }
   return true;
}

bool WriteParallelIndependentInput(const MarketSnapshot &bars[],const double bid,
                                   const double ask,const bool risk_locked,string &error)
{
   error="";
   g_parallel_audit_input_ready=false;
   if(!g_observation.IsEnabled() || ArraySize(bars)!=200) return true;
   MqlRates m15_series[]; ArraySetAsSeries(m15_series,true);
   if(CopyRates(_Symbol,PERIOD_M15,1,100,m15_series)!=100)
   { error="parallel audit M15 history unavailable"; return false; }
   MqlRates m15_bars[]; ArrayResize(m15_bars,100);
   for(int i=0;i<100;i++) m15_bars[i]=m15_series[99-i];
   const string snapshot_id=ParallelAuditSnapshotId(_Symbol,bars[199].time);
   const string material=BuildParallelCanonicalMaterial(bars,m15_bars,bid,ask,risk_locked,snapshot_id);
   const string input_hash=ParallelAuditInputHash(material);
   if(material=="" || input_hash=="")
   {
      error="parallel audit canonical hash failed";
      return false;
   }
   const string json=BuildParallelIndependentInputJson(bars,m15_bars,bid,ask,risk_locked,
                                                        snapshot_id,input_hash);
   if(!AtomicWriteParallelAuditJson(PARALLEL_AUDIT_INPUT_DIR,snapshot_id+".json",json,error))
      return false;
   g_parallel_audit_snapshot_id=snapshot_id;
   g_parallel_audit_input_hash=input_hash;
   g_parallel_audit_ema20=bars[199].ema20;
   g_parallel_audit_atr14=bars[199].atr14;
   g_parallel_audit_rsi14=bars[199].rsi14;
   g_parallel_audit_macd_hist=bars[199].macd_hist;
   g_parallel_audit_fib_retracement=0.0;
   g_parallel_audit_input_ready=true;
   return true;
}

bool WriteParallelV310Trace(const bool candidate_seen,const ENUM_TRADE_DIRECTION candidate_direction,const string execution_status,string &error)
{
   error="";
   if(!g_parallel_audit_input_ready) return true;
   const bool candidate_is_sell=(candidate_seen && candidate_direction==DIR_SELL);
   const string action=(candidate_seen ? "OPEN" : "WAIT");
   const string direction=(candidate_seen ? DirectionLabel(candidate_direction) : "NONE");
   const bool ai_reviewed=(candidate_is_sell ? g_v310_sell_candidate_ai_reviewed : g_v310_candidate_ai_reviewed);
   const bool ai_is_error=(candidate_is_sell ? g_v310_sell_candidate_ai_decision.is_error : g_v310_candidate_ai_decision.is_error);
   const bool ai_allow=(candidate_is_sell ? g_v310_sell_candidate_ai_decision.allow_trade : g_v310_candidate_ai_decision.allow_trade);
   const int ai_confidence=(candidate_is_sell ? g_v310_sell_candidate_ai_decision.confidence : g_v310_candidate_ai_decision.confidence);
   const string ai_status=(!ai_reviewed ? "NOT_RUN" :
      (ai_is_error ? "ERROR" : (ai_allow ? "APPROVED" : "REJECTED")));

   // Backward-compatible selected calculation block plus explicit two-sided diagnostics.
   const bool selected_m15_allow=(candidate_is_sell ? g_v310_sell_m15_context.allow_m5_scan : g_v310_m15_context.allow_m5_scan);
   const string selected_h_state=(candidate_is_sell ? g_v310_sell_pullback.h_state : g_v310_pullback.h_state);
   const int selected_attempt_count=(candidate_is_sell ? g_v310_sell_pullback.attempt_count : g_v310_pullback.attempt_count);
   const double selected_entry=(candidate_is_sell ? g_v310_sell_candidate_plan.entry : g_v310_candidate_plan.entry);
   const double selected_final_stop=(candidate_is_sell ? g_v310_sell_candidate_plan.final_stop : g_v310_candidate_plan.final_stop);

   string json="{\"schema_version\":2,\"rule_version\":\""+
      PARALLEL_AUDIT_RULE_VERSION+"\",\"snapshot_id\":\""+
      JsonEscape(g_parallel_audit_snapshot_id)+"\",\"symbol\":\""+JsonEscape(_Symbol)+
      "\",\"timeframe\":\"M5\",\"m5_time\":\""+
      TimeToString(g_v310_last_m5_closed,TIME_DATE|TIME_SECONDS)+"\",\"input_hash\":\""+
      g_parallel_audit_input_hash+"\",\"primary_state\":\""+
      V3109C21PrimaryLabel(g_v3109_m15_trend.primary)+"\",\"local_phase\":\""+
      V3109C21LocalPhaseLabel(g_v3109_m15_trend.local_phase)+
      "\",\"calculation\":{\"admission_model\":\"V3102_STRATEGY01_H2_LS\","+
      "\"m15_allow\":"+ParallelAuditBool(selected_m15_allow)+
      ",\"h_state\":\""+JsonEscape(selected_h_state)+"\",\"attempt_count\":"+
      IntegerToString(selected_attempt_count)+",\"entry\":"+
      ParallelAuditNumber(selected_entry)+",\"final_stop\":"+
      ParallelAuditNumber(selected_final_stop)+"},"+
      "\"buy_state\":{"+
      "\"m15_allow\":"+ParallelAuditBool(g_v310_m15_context.allow_m5_scan)+
      ",\"pullback_active\":"+ParallelAuditBool(g_v310_pullback.active)+
      ",\"locked\":"+ParallelAuditBool(g_v310_pullback.locked)+
      ",\"h_state\":\""+JsonEscape(g_v310_pullback.h_state)+"\",\"attempt_count\":"+IntegerToString(g_v310_pullback.attempt_count)+
      ",\"reason_code\":\""+JsonEscape(g_v310_pullback.reason_code)+"\",\"signal_valid\":"+ParallelAuditBool(g_v310_buy_diag.signal_valid)+
      ",\"location_valid\":"+ParallelAuditBool(g_v310_buy_diag.location_valid)+
      ",\"plan_evaluated\":"+ParallelAuditBool(g_v310_buy_diag.plan_evaluated)+
      ",\"plan_valid\":"+ParallelAuditBool(g_v310_buy_diag.plan_valid)+
      ",\"plan_reason\":\""+JsonEscape(g_v310_buy_diag.plan_reason)+"\",\"candidate_ready\":"+ParallelAuditBool(g_v310_candidate_ready)+
      ",\"reset_reason\":\""+JsonEscape(g_v310_buy_diag.reset_reason)+"\"},"+
      "\"sell_state\":{"+
      "\"m15_allow\":"+ParallelAuditBool((!InpV310LongOnly) && g_v310_sell_m15_context.allow_m5_scan)+
      ",\"pullback_active\":"+ParallelAuditBool(g_v310_sell_pullback.active)+
      ",\"locked\":"+ParallelAuditBool(g_v310_sell_pullback.locked)+
      ",\"h_state\":\""+JsonEscape(g_v310_sell_pullback.h_state)+"\",\"attempt_count\":"+IntegerToString(g_v310_sell_pullback.attempt_count)+
      ",\"reason_code\":\""+JsonEscape(g_v310_sell_pullback.reason_code)+"\",\"signal_valid\":"+ParallelAuditBool(g_v310_sell_diag.signal_valid)+
      ",\"location_valid\":"+ParallelAuditBool(g_v310_sell_diag.location_valid)+
      ",\"plan_evaluated\":"+ParallelAuditBool(g_v310_sell_diag.plan_evaluated)+
      ",\"plan_valid\":"+ParallelAuditBool(g_v310_sell_diag.plan_valid)+
      ",\"plan_reason\":\""+JsonEscape(g_v310_sell_diag.plan_reason)+"\",\"candidate_ready\":"+ParallelAuditBool(g_v310_sell_candidate_ready)+
      ",\"reset_reason\":\""+JsonEscape(g_v310_sell_diag.reset_reason)+"\"},"+
      "\"gates\":{},"+
      "\"local_candidate\":{\"action\":\""+action+"\",\"direction\":\""+direction+
      "\",\"route\":\"STRATEGY01_H2\"},\"ea_ai_review\":{\"status\":\""+
      JsonEscape(ai_status)+"\",\"confidence\":"+
      IntegerToString(ai_confidence)+"},\"execution\":{\"status\":\""+
      JsonEscape(execution_status)+"\"}}";
   return AtomicWriteParallelAuditJson(PARALLEL_AUDIT_TRACE_DIR,
      g_parallel_audit_snapshot_id+".json",json,error);
}

string ParallelAuditGateStatus(const int gate_index,const ScanResult &scan)
{
   int failed_gate=10;
   if(scan.stage==STAGE_ENVIRONMENT) failed_gate=1;
   else if(scan.stage==STAGE_EXPOSURE) failed_gate=2;
   else if(scan.stage==STAGE_TREND) failed_gate=3;
   else if(scan.stage==STAGE_STRUCTURE)
   {
      if(StringFind(scan.reason,"推动波段小于")>=0 ||
         StringFind(scan.reason,"推动幅度")>=0) failed_gate=4;
      else if(StringFind(scan.reason,"Fib")>=0 ||
              StringFind(scan.reason,"回调")>=0 ||
              StringFind(scan.reason,"推动波段无效")>=0) failed_gate=5;
      else failed_gate=3;
   }
   else if(scan.stage==STAGE_FIB) failed_gate=5;
   else if(scan.stage==STAGE_PRICE_ACTION) failed_gate=6;
   else if(scan.stage==STAGE_SIGNAL_BAR) failed_gate=7;
   else if(scan.stage==STAGE_RISK_LOCK) failed_gate=1;
   else if(scan.stage==STAGE_SPREAD) failed_gate=10;
   else if(scan.stage==STAGE_DUPLICATE) failed_gate=2;
   else if(scan.stage==STAGE_CANDIDATE)
   {
      if(StringFind(scan.reason,"TP1")>=0 || StringFind(scan.reason,"盈亏比")>=0 ||
         StringFind(scan.reason,"目标")>=0) failed_gate=9;
      else failed_gate=8;
   }
   const bool terminal_fail=(scan.outcome==SCAN_REJECT || scan.outcome==SCAN_SKIP);
   if(!terminal_fail || scan.outcome==SCAN_PASS) return "PASS";
   if(gate_index<failed_gate) return "PASS";
   if(gate_index==failed_gate) return "FAIL";
   return "NOT_EVALUATED";
}

string ParallelAuditNormalBlock(const string execution_status,const string reason)
{
   if(execution_status=="SESSION_CHANGED") return "SESSION_CHANGED";
   if(execution_status=="SPREAD_CHANGED") return "SPREAD_CHANGED";
   if(execution_status=="PRICE_MOVED") return "PRICE_MOVED";
   if(execution_status=="ENTRY_PASSED") return "ENTRY_PASSED";
   if(execution_status=="EXPOSURE_EXISTS") return "EXPOSURE_EXISTS";
   if(execution_status=="ACCOUNT_LOCKED") return "ACCOUNT_LOCKED";
   if(execution_status=="BROKER_RESTRICTION") return "BROKER_RESTRICTION";
   if(StringFind(reason,"10018")>=0 || StringFind(reason,"MARKET_CLOSED")>=0 ||
      StringFind(reason,"市场关闭")>=0) return "MARKET_CLOSED";
   if(StringFind(reason,"权限")>=0 || StringFind(reason,"NO_PERMISSION")>=0)
      return "NO_PERMISSION";
   if(StringFind(reason,"保证金")>=0 || StringFind(reason,"MARGIN")>=0)
      return "INSUFFICIENT_MARGIN";
   if(StringFind(reason,"Retcode=")>=0 || StringFind(reason,"retcode=")>=0)
      return "MT5_RETCODE";
   return "";
}

string BuildParallelEATraceJson(const ScanResult &scan,const string local_action,
                                const ENUM_TRADE_DIRECTION direction,const string route,
                                const double entry,const double sl,const double tp1,
                                const double tp2,const double rr,const string ai_status,
                                const int ai_confidence,const string ai_reason,
                                const string execution_status,const string execution_reason,
                                const double legacy_sl=0.0,const double signal_sl=0.0,
                                const double exit_unit=0.0,const int stop_mode=0,
                                const double expansion_ratio=0.0)
{
   const string normal_block=ParallelAuditNormalBlock(execution_status,execution_reason);
   const bool pending_created=(execution_status=="PENDING_CREATED");
   const bool prechecks_passed=(pending_created || execution_status=="ORDER_FAILED");
   string gates="{";
   for(int gate=1;gate<=10;gate++)
   {
      if(gate>1) gates+=",";
      gates+="\"G"+StringFormat("%02d",gate)+"\":{\"status\":\""+
             ParallelAuditGateStatus(gate,scan)+"\"}";
   }
   gates+="}";
   return "{\"schema_version\":2,\"rule_version\":\""+
      PARALLEL_AUDIT_RULE_VERSION+"\",\"snapshot_id\":\""+
      JsonEscape(g_parallel_audit_snapshot_id)+"\",\"symbol\":\""+JsonEscape(_Symbol)+
      "\",\"timeframe\":\"M5\",\"m5_time\":\""+
      TimeToString(scan.bar_time,TIME_DATE|TIME_SECONDS)+"\",\"input_hash\":\""+
      g_parallel_audit_input_hash+"\",\"calculation\":{\"ema20\":"+
      ParallelAuditNumber(g_parallel_audit_ema20)+",\"atr14\":"+
      ParallelAuditNumber(g_parallel_audit_atr14)+",\"rsi14\":"+
      ParallelAuditNumber(g_parallel_audit_rsi14)+",\"macd_hist\":"+
      ParallelAuditNumber(g_parallel_audit_macd_hist)+
      (g_parallel_audit_fib_retracement>0.0 ? ",\"fib_retracement\":"+
       ParallelAuditNumber(g_parallel_audit_fib_retracement) : "")+
      ",\"entry\":"+
      ParallelAuditNumber(entry)+",\"sl\":"+ParallelAuditNumber(sl)+
      ",\"tp1\":"+ParallelAuditNumber(tp1)+",\"tp2\":"+
      ParallelAuditNumber(tp2)+",\"rr\":"+ParallelAuditNumber(rr)+"},"+
      "\"stop_selection\":{\"legacy_sl\":"+ParallelAuditNumber(legacy_sl)+
      ",\"signal_sl\":"+ParallelAuditNumber(signal_sl)+
      ",\"exit_unit\":"+ParallelAuditNumber(exit_unit)+
      ",\"stop_mode\":\""+StopModeLabel((ENUM_STOP_MODE)stop_mode)+"\""+
      ",\"exit_distance_mode\":\""+ExitDistanceModeLabel(ExitDistanceModeFor((ENUM_STOP_MODE)stop_mode))+"\""+
      ",\"expansion_ratio\":"+ParallelAuditNumber(expansion_ratio)+"},"+
      "\"gates\":"+gates+",\"local_candidate\":{\"action\":\""+
      JsonEscape(local_action)+"\",\"direction\":\""+DirectionLabel(direction)+
      "\",\"route\":\""+JsonEscape(route)+"\",\"stage\":\""+
      ObservationStageLabel(scan.stage)+"\",\"outcome\":\""+
      ObservationOutcomeLabel(scan.outcome)+"\",\"reason\":\""+
      JsonEscape(scan.reason)+"\"},\"ea_ai_review\":{\"status\":\""+
      JsonEscape(ai_status)+"\",\"confidence\":"+IntegerToString(ai_confidence)+
      ",\"reason\":\""+JsonEscape(ai_reason)+"\"},\"execution\":{\"status\":\""+
      JsonEscape(execution_status)+"\",\"reason\":\""+
      JsonEscape(execution_reason)+"\",\"normal_block\":\""+
      JsonEscape(normal_block)+"\",\"mt5_retcode\":0,\"prechecks_passed\":"+
      ParallelAuditBool(prechecks_passed)+",\"pending_created\":"+
      ParallelAuditBool(pending_created)+"}}";
}

bool WriteParallelEATrace(const ScanResult &scan,const string local_action="WAIT",
                          const ENUM_TRADE_DIRECTION direction=DIR_NONE,
                          const string route="NONE",const double entry=0.0,
                          const double sl=0.0,const double tp1=0.0,const double tp2=0.0,
                          const double rr=0.0,const string ai_status="NOT_RUN",
                          const int ai_confidence=0,const string ai_reason="",
                          const string execution_status="NOT_RUN",
                          const string execution_reason="",
                          const double legacy_sl=0.0,const double signal_sl=0.0,
                          const double exit_unit=0.0,const int stop_mode=0,
                          const double expansion_ratio=0.0)
{
   if(!g_parallel_audit_input_ready) return true;
   const string snapshot_id=g_parallel_audit_snapshot_id;
   const string input_hash=g_parallel_audit_input_hash;
   if(snapshot_id=="" || input_hash=="") return false;
   const string json=BuildParallelEATraceJson(scan,local_action,direction,route,entry,sl,tp1,tp2,
                                               rr,ai_status,ai_confidence,ai_reason,
                                               execution_status,execution_reason,
                                               legacy_sl,signal_sl,exit_unit,stop_mode,
                                               expansion_ratio);
   string error="";
   if(!AtomicWriteParallelAuditJson(PARALLEL_AUDIT_TRACE_DIR,snapshot_id+".json",json,error))
   {
      Print("[Parallel AI审计异常] EA轨迹写入失败 | ",error);
      return false;
   }
   return true;
}

bool WriteParallelCandidateEATrace(const ScanResult &scan,const CandidateSignal &candidate,
                                   const AIDecision &decision,const string ai_status,
                                   const string execution_status,const string execution_reason)
{
   const double risk=MathAbs(candidate.planned_entry-candidate.planned_sl);
   const double tp2=(candidate.direction==DIR_BUY ? candidate.planned_entry+2.0*risk :
                                                    candidate.planned_entry-2.0*risk);
   return WriteParallelEATrace(scan,"OPEN",candidate.direction,
      SignalRouteLabel(candidate.signal_route,candidate.direction),candidate.planned_entry,
      candidate.planned_sl,candidate.tp1,tp2,candidate.rr_to_tp1,ai_status,
      decision.confidence,decision.reason,execution_status,execution_reason,
      candidate.legacy_sl,candidate.signal_sl,candidate.exit_unit,
      (int)candidate.stop_mode,candidate.expansion_ratio);
}
// ===== END: Parallel AI / EA entry audit V2 =====

bool IsTester(){ return (bool)MQLInfoInteger(MQL_TESTER); }
bool IsNewBar()
{
   const datetime current=iTime(_Symbol,g_signal_timeframe,0);
   if(current<=0 || current==g_last_bar) return false;
   g_last_bar=current;
   return true;
}

string FormatConnectionSuccess(const AIConnectionResult &connection)
{
   return "[AI连接] 成功 | HTTP="+IntegerToString(connection.http_status)+
          " | Model="+connection.model+
          " | TimeMs="+IntegerToString(connection.response_time_ms);
}

string FormatConnectionFailure(const AIConnectionResult &connection)
{
   return "[AI连接] 失败 | HTTP="+IntegerToString(connection.http_status)+
          " | MT5Error="+IntegerToString(connection.mt5_error)+
          " | Reason="+connection.reason;
}

bool NotifyEvent(const string key,const string message)
{
   // 同一个事件Key在本次EA运行期间只输出一次。
   // 去重必须发生在Print之前，否则专家日志仍会按Tick重复刷屏。
   if(key!="" && !g_notified_events.Add(key)) return false;

   Print(message);
   if(!IsTester())
   {
      if(InpEnablePopupAlert) Alert(message);
      if(InpEnablePushNotification && !SendNotification(message))
         Print("[异常] 手机推送失败，错误=",GetLastError());
   }
   return true;
}

void UpdateChartStatus()
{
   Comment("XAUUSD M5 AI Pullback V3.9.20 STAGED EXIT",
           "\nSymbol: ",_Symbol,
           " | TF: ",SignalTimeframeLabel(g_signal_timeframe),
           "\nAI Mode: ",EnumToString(InpAIMode),
           "\nAI Connection: ",g_ai_connection_text,
           "\nRollover Guard: ",g_rollover_status_text,
           "\nWeekend Guard: ",g_weekend_status_text,
           "\nWaiting Signal: ",(g_waiting_pending.active ? g_waiting_pending.candidate.signal_id : "none"),
           "\nLast Scan: ",g_last_scan_text);
}

void RecordObservationEvent(const string event_type,const string signal_id,const string stage,
                            const string outcome,const string reason,const string details,
                            const ulong order_ticket=0,const ulong position_id=0,
                            const ulong deal_ticket=0,const ENUM_TRADE_DIRECTION direction=DIR_NONE,
                            const double volume=0.0,const double price=0.0,const double profit=0.0)
{
   if(!g_observation.IsEnabled()) return;
   if(!g_observation.WriteEvent(TimeTradeServer(),event_type,signal_id,stage,outcome,reason,details,
                                order_ticket,position_id,deal_ticket,direction,volume,price,profit))
      Print("[AI盯盘数据异常] 事件写入失败 | Type=",event_type," | Error=",GetLastError());
}

void RecordServerSLEvent(const string source,
                         const string signal_id,
                         const ulong position_id,
                         const ENUM_TRADE_DIRECTION direction,
                         const double volume,
                         const double planned_initial_sl,
                         const double submitted_sl,
                         const double server_sl_before_modify,
                         const double requested_new_sl,
                         const double server_sl_after_modify,
                         const bool modify_success,
                         const uint retcode,
                         const string retcode_description)
{
   if(!g_observation.IsEnabled()) return;
   const string details=
      "source="+source+
      " | planned_initial_sl="+DoubleToString(planned_initial_sl,_Digits)+
      " | submitted_sl="+DoubleToString(submitted_sl,_Digits)+
      " | server_sl_before_modify="+DoubleToString(server_sl_before_modify,_Digits)+
      " | requested_new_sl="+DoubleToString(requested_new_sl,_Digits)+
      " | server_sl_after_modify="+DoubleToString(server_sl_after_modify,_Digits)+
      " | modify_success="+(modify_success ? "true" : "false")+
      " | retcode="+IntegerToString((int)retcode)+
      " | retcode_description="+retcode_description;
   RecordObservationEvent("server_sl_snapshot",signal_id,"position","pass",source,details,
                          0,position_id,0,direction,volume,0.0,0.0);
}

bool SaveLarkTradeContext(const LarkTradeContext &ctx,string &error)
{
   return g_lark_context_store.Save(ctx,error);
}

bool LoadLarkTradeContext(const string signal_id,const ulong order_ticket,
                          LarkTradeContext &ctx,string &error)
{
   return g_lark_context_store.Load(signal_id,order_ticket,ctx,error);
}

bool IsLarkDealAlreadySent(const ulong deal_ticket)
{
   return g_lark_context_store.IsDealSent(deal_ticket);
}

bool MarkLarkDealSent(const ulong deal_ticket,string &error)
{
   return g_lark_context_store.MarkDealSent(deal_ticket,error);
}

void BuildLarkTradeContext(const CandidateSignal &candidate,const AIDecision &decision,
                           const ulong order_ticket,LarkTradeContext &ctx)
{
   ResetLarkTradeContext(ctx);
   ctx.signal_id=candidate.signal_id;
   ctx.order_ticket=order_ticket;
   ctx.created_at=TimeTradeServer();
   ctx.direction=candidate.direction;
   ctx.signal_route=candidate.signal_route;
   ctx.planned_entry=candidate.planned_entry;
   ctx.planned_sl=candidate.planned_sl;
   ctx.rr_to_tp1=candidate.rr_to_tp1;
   ctx.fib_retracement=candidate.fib_retracement;
   ctx.h_attempt=candidate.h_attempt;
   ctx.ema_distance_usd=candidate.ema_distance_usd;
   ctx.sr_confluence=candidate.sr_confluence;
   ctx.ma_confluence=candidate.ma_confluence;
   ctx.pin_bar=candidate.pin_bar;
   ctx.hammer=candidate.hammer;
   ctx.engulfing=candidate.engulfing;
   ctx.strong_reversal_bar=candidate.strong_reversal_bar;
   ctx.ai_allow_trade=decision.allow_trade;
   ctx.ai_is_error=decision.is_error;
   ctx.ai_confidence=decision.confidence;
   ctx.ai_reason=decision.reason;
}

string LarkYesNo(const bool value)
{
   return value ? "✅" : "—";
}

string LarkPriceText(const double value)
{
   return value>0.0 ? DoubleToString(value,_Digits) : "—";
}

string LarkPatternText(const LarkTradeContext &ctx)
{
   string text="";
   if(ctx.pin_bar) text+=(text=="" ? "Pin Bar" : " + Pin Bar");
   if(ctx.hammer) text+=(text=="" ? "Hammer" : " + Hammer");
   if(ctx.engulfing) text+=(text=="" ? "Engulfing" : " + Engulfing");
   if(ctx.strong_reversal_bar) text+=(text=="" ? "Strong Reversal" : " + Strong Reversal");
   return text=="" ? "—" : text;
}

string LarkAttemptText(const LarkTradeContext &ctx)
{
   if(ctx.h_attempt<=0) return "—";
   return AttemptLabel(ctx.direction,ctx.h_attempt);
}

struct ClosedTradeFacts
{
   bool available;
   ulong position_id;
   ENUM_TRADE_DIRECTION direction;
   datetime open_time;
   datetime close_time;
   double initial_volume;
   double closed_volume;
   double entry_price;
   double exit_price;
   double gross_profit;
   double commission;
   double swap;
   double fee;
   double net_profit;
};

void ResetClosedTradeFacts(ClosedTradeFacts &facts)
{
   ZeroMemory(facts);
   facts.available=false;
   facts.direction=DIR_NONE;
}

bool AggregateClosedTradeFacts(const string symbol,const long magic,
                               const ulong position_id,ClosedTradeFacts &facts,
                               string &error)
{
   ResetClosedTradeFacts(facts);
   error="";
   if(symbol=="" || position_id==0)
   {
      error="最终结算汇总参数无效";
      return false;
   }
   if(!HistorySelect(0,TimeCurrent()))
   {
      error="无法选择成交历史，MT5错误="+IntegerToString(GetLastError());
      return false;
   }

   double entry_value=0.0;
   double exit_value=0.0;
   for(int i=0;i<HistoryDealsTotal();i++)
   {
      const ulong deal=HistoryDealGetTicket(i);
      if(deal==0 || HistoryDealGetString(deal,DEAL_SYMBOL)!=symbol ||
         HistoryDealGetInteger(deal,DEAL_MAGIC)!=magic ||
         (ulong)HistoryDealGetInteger(deal,DEAL_POSITION_ID)!=position_id)
         continue;

      const long entry=HistoryDealGetInteger(deal,DEAL_ENTRY);
      const long type=HistoryDealGetInteger(deal,DEAL_TYPE);
      const double volume=HistoryDealGetDouble(deal,DEAL_VOLUME);
      const double price=HistoryDealGetDouble(deal,DEAL_PRICE);
      const datetime time=(datetime)HistoryDealGetInteger(deal,DEAL_TIME);
      facts.gross_profit+=HistoryDealGetDouble(deal,DEAL_PROFIT);
      facts.commission+=HistoryDealGetDouble(deal,DEAL_COMMISSION);
      facts.swap+=HistoryDealGetDouble(deal,DEAL_SWAP);
      facts.fee+=HistoryDealGetDouble(deal,DEAL_FEE);

      if(entry==DEAL_ENTRY_IN || entry==DEAL_ENTRY_INOUT)
      {
         facts.initial_volume+=volume;
         entry_value+=price*volume;
         if(facts.open_time==0 || time<facts.open_time) facts.open_time=time;
         if(type==DEAL_TYPE_BUY) facts.direction=DIR_BUY;
         else if(type==DEAL_TYPE_SELL) facts.direction=DIR_SELL;
      }
      if(entry==DEAL_ENTRY_OUT || entry==DEAL_ENTRY_OUT_BY || entry==DEAL_ENTRY_INOUT)
      {
         facts.closed_volume+=volume;
         exit_value+=price*volume;
         if(time>facts.close_time) facts.close_time=time;
      }
   }
   if(facts.initial_volume<=0.0 || facts.closed_volume<=0.0)
   {
      error="未找到完整的开仓和退出成交记录";
      return false;
   }
   facts.position_id=position_id;
   facts.entry_price=entry_value/facts.initial_volume;
   facts.exit_price=exit_value/facts.closed_volume;
   facts.net_profit=facts.gross_profit+facts.commission+facts.swap+facts.fee;
   facts.available=true;
   return true;
}

double CalculatePlannedRiskMoney(const ENUM_TRADE_DIRECTION direction,
                                 const double entry_price,const double stop_loss,
                                 const double volume)
{
   if(direction==DIR_NONE || entry_price<=0.0 || stop_loss<=0.0 || volume<=0.0)
      return 0.0;
   double projected=0.0;
   const ENUM_ORDER_TYPE order_type=(direction==DIR_BUY ? ORDER_TYPE_BUY : ORDER_TYPE_SELL);
   if(!OrderCalcProfit(order_type,_Symbol,volume,entry_price,stop_loss,projected))
      return 0.0;
   return MathAbs(projected);
}

bool V3109C32SlotIsOccupied(const int slot)
{
   if(slot<0 || slot>=V3109C32_MAX_SLOTS) return false;
   const TradeRuntimeState state=g_trade_slots[slot].State();
   return state.state!=STATE_IDLE ||
          g_trade_slots[slot].HasManagedPendingOrders() ||
          g_trade_slots[slot].HasManagedPositions();
}

int V3109C32OccupiedSlotCount()
{
   int count=0;
   for(int slot=0;slot<V3109C32_MAX_SLOTS;slot++)
      if(V3109C32SlotIsOccupied(slot)) count++;
   return count;
}

int V3109C32FindFreeSlot()
{
   bool occupied[V3109C32_MAX_SLOTS];
   for(int slot=0;slot<V3109C32_MAX_SLOTS;slot++)
      occupied[slot]=V3109C32SlotIsOccupied(slot);
   return V3109C32FindFirstFreeSlot(occupied);
}

ENUM_TRADE_DIRECTION V3109C32ExposureDirection()
{
   ENUM_TRADE_DIRECTION direction=DIR_NONE;
   for(int direction=0;direction<2;direction++)
      V3109C32InitMicroCycle(g_v3109_c32_micro_cycles[direction]);
   V3109C32ResetAllPendingContexts();
   for(int slot=0;slot<V3109C32_MAX_SLOTS;slot++)
   {
      if(!V3109C32SlotIsOccupied(slot)) continue;
      const TradeRuntimeState state=g_trade_slots[slot].State();
      if(state.direction!=DIR_BUY && state.direction!=DIR_SELL) continue;
      if(direction==DIR_NONE) direction=state.direction;
      else if(direction!=state.direction) return DIR_NONE;
   }
   return direction;
}

double V3109C32AggregateInitialRiskUsd()
{
   double total=0.0;
   for(int slot=0;slot<V3109C32_MAX_SLOTS;slot++)
   {
      if(!V3109C32SlotIsOccupied(slot)) continue;
      const TradeRuntimeState state=g_trade_slots[slot].State();
      const double measured=CalculatePlannedRiskMoney(state.direction,state.entry,
                                                       state.initial_sl,state.initial_volume);
      const double fallback=V3109C32RiskBudgetUsd(AccountInfoDouble(ACCOUNT_EQUITY),
                                                  InpRiskPercent);
      total+=(measured>0.0 ? measured : fallback);
   }
   return total;
}

datetime V3109C32TrendSegmentId()
{
   if(g_v3109_m15_memory.state_started_at>0)
      return g_v3109_m15_memory.state_started_at;
   return g_v310_last_m15_closed;
}

int V3109C32SelectLiveSlot(const ENUM_TRADE_DIRECTION direction,
                           const datetime signal_bar_time,
                           const double candidate_risk_usd,string &reason)
{
   bool occupied[V3109C32_MAX_SLOTS];
   for(int slot=0;slot<V3109C32_MAX_SLOTS;slot++)
      occupied[slot]=V3109C32SlotIsOccupied(slot);
   const bool early_trend=(direction==DIR_BUY ? g_v3109_c33_early_buy :
                           (direction==DIR_SELL ? g_v3109_c33_early_sell : false));
   if(!V3109C33EarlySlotAllowed(early_trend,V3109C32OccupiedSlotCount()))
   {
      reason="EARLY_TREND_ONE_SLOT";
      return -1;
   }
   const double equity=AccountInfoDouble(ACCOUNT_EQUITY);
   return V3109C32SelectAdmissibleSlot(occupied,(int)V3109C32ExposureDirection(),
      (int)direction,g_v3109_c32_last_fill_bar_time,signal_bar_time,
      equity,InpRiskPercent,V3109C32AggregateInitialRiskUsd(),candidate_risk_usd,
      reason);
}

bool V3109C32CycleAlreadyPending(const ENUM_TRADE_DIRECTION direction,
                                 const datetime trend_segment,
                                 const datetime pullback_start_time)
{
   for(int slot=0;slot<V3109C32_MAX_SLOTS;slot++)
   {
      const V3109C32PendingContext ctx=g_v3109_c32_pending_contexts[slot];
      if(ctx.active && ctx.direction==direction &&
         ctx.trend_segment_id==trend_segment &&
         ctx.micro_pullback_start_time==pullback_start_time)
         return true;
   }
   return false;
}

bool V3109C32PrepareCandidateCycle(const ENUM_TRADE_DIRECTION direction,
                                   const datetime pullback_start_time,
                                   const int attempt_no,
                                   const datetime signal_bar_time,
                                   string &setup_key,string &reason)
{
   setup_key="";
   reason="";
   if((direction!=DIR_BUY && direction!=DIR_SELL) || pullback_start_time<=0 ||
      signal_bar_time<=0)
   {
      reason="INVALID_MICRO_CYCLE";
      return false;
   }
   const int direction_index=(direction==DIR_BUY ? 0 : 1);
   const datetime trend_segment=V3109C32TrendSegmentId();
   if(V3109C32CycleAlreadyPending(direction,trend_segment,pullback_start_time))
   {
      reason="MICRO_CYCLE_PENDING";
      return false;
   }
   V3109C32StartMicroCycle(g_v3109_c32_micro_cycles[direction_index],(int)direction,
                           trend_segment,pullback_start_time);
   if(!V3109C32CanEmitCycle(g_v3109_c32_micro_cycles[direction_index],attempt_no,
                            signal_bar_time))
   {
      reason="MICRO_CYCLE_CONSUMED_OR_INVALID";
      return false;
   }
   const double provisional_risk=V3109C32RiskBudgetUsd(
      AccountInfoDouble(ACCOUNT_EQUITY),InpRiskPercent);
   const int slot=V3109C32SelectLiveSlot(direction,signal_bar_time,
                                         provisional_risk,reason);
   if(slot<0) return false;
   setup_key=V3109C32MicroSetupKey((int)direction,trend_segment,pullback_start_time,
                                   attempt_no,signal_bar_time);
   g_v3109_c32_candidate_slot=slot;
   return setup_key!="";
}

void V3109C32BindPendingContext(const int slot,
                                const ENUM_TRADE_DIRECTION direction,
                                const bool countertrend,const int attempt_no,
                                const datetime pullback_start_time,
                                const datetime signal_bar_time,
                                const double signal_low,const double signal_high,
                                const string setup_key,
                                const double planned_risk_usd=0.0,
                                const double aggregate_risk_usd=0.0)
{
   if(slot<0 || slot>=V3109C32_MAX_SLOTS) return;
   V3109C32ResetPendingContext(g_v3109_c32_pending_contexts[slot]);
   g_v3109_c32_pending_contexts[slot].active=true;
   g_v3109_c32_pending_contexts[slot].slot_id=slot;
   g_v3109_c32_pending_contexts[slot].slot_magic=V3109C32SlotMagic(InpMagicNumber,slot);
   g_v3109_c32_pending_contexts[slot].direction=direction;
   g_v3109_c32_pending_contexts[slot].countertrend=countertrend;
   g_v3109_c32_pending_contexts[slot].attempt_no=attempt_no;
   g_v3109_c32_pending_contexts[slot].trend_segment_id=V3109C32TrendSegmentId();
   g_v3109_c32_pending_contexts[slot].micro_pullback_start_time=pullback_start_time;
   g_v3109_c32_pending_contexts[slot].signal_bar_time=signal_bar_time;
   g_v3109_c32_pending_contexts[slot].signal_low=signal_low;
   g_v3109_c32_pending_contexts[slot].signal_high=signal_high;
   g_v3109_c32_pending_contexts[slot].planned_risk_usd=planned_risk_usd;
   g_v3109_c32_pending_contexts[slot].aggregate_risk_usd=aggregate_risk_usd;
   g_v3109_c32_pending_contexts[slot].setup_key=setup_key;
}

void V3109C32RecoverPendingContext(const int slot)
{
   if(slot<0 || slot>=V3109C32_MAX_SLOTS) return;
   const TradeRuntimeState state=g_trade_slots[slot].State();
   if(state.state!=STATE_PENDING_ORDER || !g_trade_slots[slot].HasManagedPendingOrders())
      return;
   const datetime recovered_time=(state.pending_created>0 ? state.pending_created : TimeTradeServer());
   const int attempt_no=(StringFind(state.signal_id,"_A2_")>=0 ? 2 : 1);
   V3109C32BindPendingContext(slot,state.direction,
      StringFind(state.signal_id,"COUNTER")>=0,attempt_no,recovered_time,recovered_time,
      0.0,0.0,"RECOVERED|"+state.signal_id);
   g_v3109_c32_pending_contexts[slot].trend_segment_id=recovered_time;
   Print("V3109_C32_RECOVER|slot=",slot,"|magic=",
         V3109C32SlotMagic(InpMagicNumber,slot),"|state=PENDING_ORDER");
}

void V3109C32ManageAllSlots(const datetime server_now)
{
   for(int slot=0;slot<V3109C32_MAX_SLOTS;slot++)
   {
      string event_message="";
      g_trade_slots[slot].SyncPendingState(event_message);
      if(event_message!="") Print("V3109_C32_SLOT|slot=",slot,"|",event_message);
      if(g_v3109_c32_pending_contexts[slot].active &&
         !g_trade_slots[slot].HasManagedPendingOrders() &&
         !g_trade_slots[slot].HasManagedPositions())
         V3109C32ResetPendingContext(g_v3109_c32_pending_contexts[slot]);
      string expiry_error="";
      if(!g_trade_slots[slot].DeleteExpiredPending(server_now,expiry_error) && expiry_error!="")
         Print("V3109_C32_SLOT|slot=",slot,"|pending_expiry_error=",expiry_error);
   }
}

bool V3109C32HasPriorWeekManagedExposure(const datetime server_now)
{
   for(int slot=0;slot<V3109C32_MAX_SLOTS;slot++)
      if(g_trade_slots[slot].HasPriorWeekManagedExposure(server_now)) return true;
   return false;
}

bool V3109C32HasManagedPendingOrders()
{
   for(int slot=0;slot<V3109C32_MAX_SLOTS;slot++)
      if(g_trade_slots[slot].HasManagedPendingOrders()) return true;
   return false;
}

bool V3109C32HasManagedPositions()
{
   for(int slot=0;slot<V3109C32_MAX_SLOTS;slot++)
      if(g_trade_slots[slot].HasManagedPositions()) return true;
   return false;
}

bool V3109C32HasManagedExposure()
{
   return V3109C32HasManagedPendingOrders() || V3109C32HasManagedPositions();
}

bool V3109C32CancelAllManagedPending(string &error)
{
   error="";
   bool all_ok=true;
   for(int slot=0;slot<V3109C32_MAX_SLOTS;slot++)
   {
      string slot_error="";
      if(!g_trade_slots[slot].CancelAllManagedPending(slot_error))
      {
         all_ok=false;
         if(error!="") error+=" | ";
         error+="slot="+IntegerToString(slot)+": "+slot_error;
      }
   }
   return all_ok;
}

bool V3109C32CloseAllManagedPositions(string &error)
{
   error="";
   bool all_ok=true;
   for(int slot=0;slot<V3109C32_MAX_SLOTS;slot++)
   {
      string slot_error="";
      if(!g_trade_slots[slot].CloseAllManagedPositions(slot_error))
      {
         all_ok=false;
         if(error!="") error+=" | ";
         error+="slot="+IntegerToString(slot)+": "+slot_error;
      }
   }
   return all_ok;
}

string LifecycleSafeText(string value)
{
   StringReplace(value,";",",");
   StringReplace(value,"\r"," ");
   StringReplace(value,"\n"," ");
   return value;
}

string LifecycleBoolText(const bool value)
{
   return value ? "1" : "0";
}

string LifecycleExitStage(const TradeRuntimeState &state,const bool final_exit)
{
   if(final_exit)
   {
      if(state.tp2_done || state.state==STATE_RUNNER) return "RUNNER";
      if(state.tp1_done || state.tp1_reached) return "REMAINDER";
      return "INITIAL_EXIT";
   }
   if(state.tp2_done || state.state==STATE_RUNNER) return "TP2";
   if(state.tp1_done || state.tp1_reached) return "TP1";
   return "OTHER_EXIT";
}

bool WriteTradeLifecycleEvent(const TradeRuntimeState &state,const ulong position_id,
                              const ulong deal_ticket,const string event_kind,
                              const string stage,const string close_reason,
                              const long slot_magic,const int slot_id,string &error)
{
   error="";
   // 策略测试器运行时禁止写入正式 Common Files 生命周期目录，避免测试仓位污染真实交易数据。
   if(IsTester()) return true;
   if(position_id==0 || deal_ticket==0 || event_kind=="" ||
      !HistoryDealSelect(deal_ticket))
   {
      error="交易生命周期参数或成交历史无效";
      return false;
   }
   const string base_path=g_observation.BasePath();
   if(base_path=="")
   {
      error="AI复盘公共目录尚未初始化";
      return false;
   }
   const string lifecycle_dir=base_path+"\\Trade_Lifecycle";
   FolderCreate(lifecycle_dir,FILE_COMMON);
   const string file_path=lifecycle_dir+"\\Position_"+
      IntegerToString((long)position_id)+".csv";
   const string event_id=IntegerToString((long)deal_ticket)+"_"+event_kind;
   ResetLastError();
   const int handle=FileOpen(file_path,FILE_READ|FILE_WRITE|FILE_TXT|FILE_ANSI|
                             FILE_COMMON|FILE_SHARE_READ|FILE_SHARE_WRITE,0,CP_UTF8);
   if(handle==INVALID_HANDLE)
   {
      error="无法打开交易生命周期文件，MT5错误="+IntegerToString(GetLastError());
      return false;
   }
   while(!FileIsEnding(handle))
   {
      const string existing=FileReadString(handle);
      if(StringFind(existing,event_id+";")==0)
      {
         FileClose(handle);
         return true;
      }
   }

   const double profit=HistoryDealGetDouble(deal_ticket,DEAL_PROFIT);
   const double commission=HistoryDealGetDouble(deal_ticket,DEAL_COMMISSION);
   const double swap=HistoryDealGetDouble(deal_ticket,DEAL_SWAP);
   const double fee=HistoryDealGetDouble(deal_ticket,DEAL_FEE);
   const double volume=HistoryDealGetDouble(deal_ticket,DEAL_VOLUME);
   const double price=HistoryDealGetDouble(deal_ticket,DEAL_PRICE);
   const datetime deal_time=(datetime)HistoryDealGetInteger(deal_ticket,DEAL_TIME);
   ENUM_TRADE_DIRECTION direction=state.direction;
   if(direction==DIR_NONE)
   {
      const long deal_type=HistoryDealGetInteger(deal_ticket,DEAL_TYPE);
      if(event_kind=="OPEN")
         direction=(deal_type==DEAL_TYPE_BUY ? DIR_BUY : DIR_SELL);
      else
         direction=(deal_type==DEAL_TYPE_SELL ? DIR_BUY : DIR_SELL);
   }
   const double initial_volume=(state.initial_volume>0.0 ? state.initial_volume :
                                (event_kind=="OPEN" ? volume : 0.0));
   const double initial_risk=CalculatePlannedRiskMoney(direction,
      (state.entry>0.0 ? state.entry : price),state.initial_sl,initial_volume);
   const string header="event_id;server_time;event_kind;stage;position_id;deal_ticket;"+
      "signal_id;direction;volume;price;profit;commission;swap;fee;initial_volume;"+
      "initial_sl;initial_risk;tp1_price;tp2_price;tp1_done;tp2_done;"+
      "runner_active;close_reason;slot_id;slot_magic;strategy_version;rule_version;parameter_version;"+
      "trader;h_state;reason_code;actual_r;server_sl_status";
   const double actual_r=(direction==DIR_BUY ? state.entry-state.initial_sl : state.initial_sl-state.entry);
   const string line=event_id+";"+
      TimeToString(deal_time,TIME_DATE|TIME_SECONDS)+";"+
      LifecycleSafeText(event_kind)+";"+LifecycleSafeText(stage)+";"+
      IntegerToString((long)position_id)+";"+IntegerToString((long)deal_ticket)+";"+
      LifecycleSafeText(state.signal_id)+";"+DirectionLabel(direction)+";"+
      DoubleToString(volume,8)+";"+DoubleToString(price,8)+";"+
      DoubleToString(profit,8)+";"+DoubleToString(commission,8)+";"+
      DoubleToString(swap,8)+";"+DoubleToString(fee,8)+";"+
      DoubleToString(initial_volume,8)+";"+DoubleToString(state.initial_sl,8)+";"+
      DoubleToString(initial_risk,8)+";"+DoubleToString(state.tp1_price,8)+";"+
      DoubleToString(state.tp2_price,8)+";"+LifecycleBoolText(state.tp1_done)+";"+
      LifecycleBoolText(state.tp2_done)+";"+
      LifecycleBoolText(event_kind!="FINAL_EXIT" && state.tp2_done)+";"+
      LifecycleSafeText(close_reason)+";"+IntegerToString(slot_id)+";"+
      IntegerToString(slot_magic)+
      ";3.107;"+PARALLEL_AUDIT_RULE_VERSION+";"+V310_PARAMETER_VERSION+
      ";A;"+LifecycleSafeText(g_v310_pullback.h_state)+";"+
      LifecycleSafeText(close_reason)+";"+DoubleToString(actual_r,8)+";"+
      (state.initial_sl>0.0 ? "PROTECTED" : "UNPROTECTED");
   if(FileSize(handle)==0) FileWriteString(handle,header+"\r\n");
   FileSeek(handle,0,SEEK_END);
   FileWriteString(handle,line+"\r\n");
   FileFlush(handle);
   FileClose(handle);
   return true;
}

bool QueueLarkFactsToCommonOutbox(const string facts_json,const string notification_id,
                                  const string notification_type,const ulong deal_ticket,
                                  const ulong position_id,const datetime created_server,
                                  string &error)
{
   error="";
   if(facts_json=="" || notification_id=="" || notification_type=="" || position_id==0)
   {
      error="Lark事实队列参数无效";
      return false;
   }
   const string base_path=g_observation.BasePath();
   if(base_path=="")
   {
      error="AI复盘公共目录尚未初始化";
      return false;
   }
   const string outbox_root=base_path+"\\Lark_Outbox";
   const string pending_dir=outbox_root+"\\Pending";
   const string sent_dir=outbox_root+"\\Sent";
   const string uncertain_dir=outbox_root+"\\Uncertain";
   FolderCreate(outbox_root,FILE_COMMON);
   FolderCreate(pending_dir,FILE_COMMON);
   FolderCreate(sent_dir,FILE_COMMON);
   FolderCreate(uncertain_dir,FILE_COMMON);
   const string filename=notification_id+".json";
   const string final_path=pending_dir+"\\"+filename;
   if(FileIsExist(final_path,FILE_COMMON) ||
      FileIsExist(sent_dir+"\\"+filename,FILE_COMMON) ||
      FileIsExist(uncertain_dir+"\\"+filename,FILE_COMMON)) return true;
   const string temp_path=final_path+".tmp";
   const string envelope="{\"schema_version\":2,"+
      "\"notification_id\":\""+JsonEscape(notification_id)+"\","+
      "\"notification_type\":\""+JsonEscape(notification_type)+"\","+
      "\"created_server\":\""+
         JsonEscape(TimeToString(created_server,TIME_DATE|TIME_SECONDS))+"\","+
      "\"deal_ticket\":\""+IntegerToString((long)deal_ticket)+"\","+
      "\"position_id\":\""+IntegerToString((long)position_id)+"\","+
      "\"attempts\":0,\"facts\":"+facts_json+"}";
   ResetLastError();
   const int handle=FileOpen(temp_path,FILE_WRITE|FILE_TXT|FILE_ANSI|FILE_COMMON|
                             FILE_SHARE_READ,0,CP_UTF8);
   if(handle==INVALID_HANDLE)
   {
      error="无法写入Lark事实临时文件，MT5错误="+IntegerToString(GetLastError());
      return false;
   }
   FileWriteString(handle,envelope);
   FileFlush(handle);
   FileClose(handle);
   ResetLastError();
   if(!FileMove(temp_path,FILE_COMMON,final_path,FILE_COMMON|FILE_REWRITE))
   {
      error="无法提交Lark事实文件，MT5错误="+IntegerToString(GetLastError());
      FileDelete(temp_path,FILE_COMMON);
      return false;
   }
   return true;
}

string BuildOpenTradeCard(const LarkTradeContext &ctx,const TradeRuntimeState &filled_state,
                          const ulong position_id,const ulong deal_ticket,
                          const double deal_price,const double deal_volume,
                          const datetime deal_time,const bool context_loaded)
{
   const string direction=DirectionLabel(filled_state.direction);
   const string theme=(filled_state.direction==DIR_SELL ? "red" : "green");
   const string route=(context_loaded ? SignalRouteLabel(ctx.signal_route,ctx.direction) :
                       SignalRouteLabel(filled_state.signal_route,filled_state.direction));
   string md="**EA复盘｜开仓通知**\n\n**📌 交易信息**\n";
   md+="**EA：** XAUUSD_M5_AI_Pullback_V3_9_20\n";
   md+="**品种：** "+_Symbol+"\n";
   md+="**周期：** "+SignalTimeframeLabel(g_signal_timeframe)+"\n";
   md+="**方向：** "+direction+"\n";
   md+="**成交时间：** "+TimeToString(deal_time,TIME_DATE|TIME_SECONDS)+"\n";
   md+="**实际成交价：** "+DoubleToString(deal_price,_Digits)+"\n";
   md+="**手数：** "+DoubleToString(deal_volume,2)+"\n";
   md+="**初始SL：** "+LarkPriceText(filled_state.initial_sl)+"\n";
   const double planned_risk=CalculatePlannedRiskMoney(filled_state.direction,deal_price,
                                                       filled_state.initial_sl,deal_volume);
   md+="**预计最大风险：** "+(planned_risk>0.0 ?
      DoubleToString(planned_risk,2)+" "+AccountInfoString(ACCOUNT_CURRENCY) : "—")+
      "（计划值，非实际亏损）\n";
   md+="**1R目标：** "+LarkPriceText(filled_state.tp1_price)+"\n";
   md+="**2R目标：** "+LarkPriceText(filled_state.tp2_price)+"\n";
   md+="**Order：** "+IntegerToString((long)filled_state.order_ticket)+"\n";
   md+="**Position：** "+IntegerToString((long)position_id)+"\n";
   md+="**Deal：** "+IntegerToString((long)deal_ticket)+"\n";
   md+="**Signal ID：** "+(context_loaded ? ctx.signal_id : filled_state.signal_id)+"\n\n";

   md+="**📈 为什么开单**\n";
   md+="**触发路径：** "+route+"\n";
   if(context_loaded)
   {
      md+="**Fib回调：** "+DoubleToString(ctx.fib_retracement,1)+"%\n";
      md+="**H2/H3：** "+LarkAttemptText(ctx)+"\n";
      md+="**距EMA20：** "+DoubleToString(ctx.ema_distance_usd,2)+" USD\n";
      md+="**S/R共振：** "+LarkYesNo(ctx.sr_confluence)+"\n";
      md+="**EMA共振：** "+LarkYesNo(ctx.ma_confluence)+"\n";
      md+="**PA形态：** "+LarkPatternText(ctx)+"\n";
      md+="**结构空间：** "+DoubleToString(ctx.rr_to_tp1,2)+"R\n\n";
      md+="**🤖 AI审核**\n";
      md+="**结果：** "+(ctx.ai_allow_trade ? "允许交易" : "未允许")+"\n";
      if(ctx.ai_is_error) md+="**状态：** ⚠️ AI审核异常·降级放行\n";
      md+="**置信度：** "+IntegerToString(ctx.ai_confidence)+"\n";
      md+="**理由：** "+ctx.ai_reason;
   }
   else
   {
      md+="**说明：** 原候选/AI开仓原因快照读取失败，交易事实仍按实际成交发送。\n\n";
      md+="**🤖 AI审核：** 快照不可用";
   }

   return "{\"msg_type\":\"interactive\",\"card\":{"+
      "\"schema\":\"2.0\","+
      "\"header\":{\"template\":\""+theme+"\",\"title\":{"+
         "\"tag\":\"plain_text\",\"content\":\"EA复盘｜交易#"+
         IntegerToString((long)position_id)+"｜"+direction+"｜开仓\"}},"+
       "\"body\":{\"elements\":[{\"tag\":\"markdown\",\"content\":\""+
          JsonEscape(md)+"\"}]}}}";
}

string BuildFinalTradeCard(const ClosedTradeFacts &facts,const TradeRuntimeState &state,
                           const ulong deal_ticket,const string close_reason)
{
   const string direction=DirectionLabel(facts.direction);
   const string currency=AccountInfoString(ACCOUNT_CURRENCY);
   const string theme=(facts.net_profit>0.0 ? "green" :
                       (facts.net_profit<0.0 ? "red" : "grey"));
   string md="**EA复盘｜最终结算**\n\n**📌 交易信息**\n";
   md+="**交易编号：** "+IntegerToString((long)facts.position_id)+"\n";
   md+="**品种：** "+_Symbol+"\n";
   md+="**方向：** "+direction+"\n";
   md+="**开仓时间：** "+TimeToString(facts.open_time,TIME_DATE|TIME_SECONDS)+"\n";
   md+="**最终退出：** "+TimeToString(facts.close_time,TIME_DATE|TIME_SECONDS)+"\n";
   md+="**初始手数：** "+DoubleToString(facts.initial_volume,2)+"\n";
   md+="**平均入场价：** "+DoubleToString(facts.entry_price,_Digits)+"\n";
   md+="**平均退出价：** "+DoubleToString(facts.exit_price,_Digits)+"\n";
   md+="**初始SL：** "+LarkPriceText(state.initial_sl)+"\n";
   md+="**TP1：** "+(state.tp1_done ? "已执行" : "未执行")+"\n";
   md+="**TP2：** "+(state.tp2_done ? "已执行" : "未执行")+"\n";
   md+="**退出原因：** "+close_reason+"\n";
   md+="**最终Deal：** "+IntegerToString((long)deal_ticket)+"\n\n";
   md+="**💰 盈亏结算（"+currency+"）**\n";
   md+="**毛利润：** "+DoubleToString(facts.gross_profit,2)+"\n";
   md+="**手续费：** "+DoubleToString(facts.commission,2)+"\n";
   md+="**隔夜费：** "+DoubleToString(facts.swap,2)+"\n";
   md+="**其他费用：** "+DoubleToString(facts.fee,2)+"\n";
   md+="**净盈亏：** "+DoubleToString(facts.net_profit,2)+" "+currency;
   return "{\"msg_type\":\"interactive\",\"card\":{"+
      "\"schema\":\"2.0\","+
      "\"header\":{\"template\":\""+theme+"\",\"title\":{"+
         "\"tag\":\"plain_text\",\"content\":\"EA复盘｜交易#"+
         IntegerToString((long)facts.position_id)+"｜"+direction+"｜最终结算\"}},"+
      "\"body\":{\"elements\":[{\"tag\":\"markdown\",\"content\":\""+
         JsonEscape(md)+"\"}]}}}";
}

bool QueueLarkCardToCommonOutbox(const string card_json,const string notification_id,
                                 const string notification_type,const ulong deal_ticket,
                                 const ulong position_id,const datetime created_server,
                                 string &error)
{
   error="";
   if(card_json=="" || notification_id=="" || notification_type=="" || position_id==0)
   {
      error="Lark补发队列参数无效";
      return false;
   }
   const string base_path=g_observation.BasePath();
   if(base_path=="")
   {
      error="AI复盘公共目录尚未初始化";
      return false;
   }
   const string outbox_root=base_path+"\\Lark_Outbox";
   const string pending_dir=g_observation.BasePath()+"\\Lark_Outbox\\Pending";
   const string sent_dir=g_observation.BasePath()+"\\Lark_Outbox\\Sent";
   ResetLastError();
   FolderCreate(outbox_root,FILE_COMMON);
   FolderCreate(pending_dir,FILE_COMMON);
   FolderCreate(sent_dir,FILE_COMMON);

   const string final_path=pending_dir+"\\"+notification_id+".json";
   const string sent_path=sent_dir+"\\"+notification_id+".json";
   if(FileIsExist(final_path,FILE_COMMON) || FileIsExist(sent_path,FILE_COMMON)) return true;
   const string temp_path=final_path+".tmp";
   const string envelope="{\"schema_version\":1,"+
      "\"notification_id\":\""+JsonEscape(notification_id)+"\","+
      "\"notification_type\":\""+JsonEscape(notification_type)+"\","+
      "\"created_server\":\""+
         JsonEscape(TimeToString(created_server,TIME_DATE|TIME_SECONDS))+"\","+
      "\"deal_ticket\":\""+IntegerToString((long)deal_ticket)+"\","+
      "\"position_id\":\""+IntegerToString((long)position_id)+"\","+
      "\"attempts\":0,\"card\":"+card_json+"}";

   ResetLastError();
   const int handle=FileOpen(temp_path,FILE_WRITE|FILE_TXT|FILE_ANSI|FILE_COMMON|
                             FILE_SHARE_READ,0,CP_UTF8);
   if(handle==INVALID_HANDLE)
   {
      error="无法写入Lark补发临时文件，MT5错误="+IntegerToString(GetLastError());
      return false;
   }
   FileWriteString(handle,envelope);
   FileFlush(handle);
   FileClose(handle);
   ResetLastError();
   if(!FileMove(temp_path,FILE_COMMON,final_path,FILE_COMMON|FILE_REWRITE))
   {
      error="无法提交Lark补发文件，MT5错误="+IntegerToString(GetLastError());
      FileDelete(temp_path,FILE_COMMON);
      return false;
   }
   return true;
}

bool LegacySendFinalTradeCard(const TradeRuntimeState &state,const ulong position_id,
                              const ulong deal_ticket,const string close_reason,
                              const long slot_magic)
{
   if(!InpLarkOpenNotifyEnabled || IsTester()) return false;
   ClosedTradeFacts facts;
   string aggregate_error="";
   if(!AggregateClosedTradeFacts(_Symbol,slot_magic,position_id,facts,aggregate_error))
   {
      Print("[Lark通知异常] 最终结算汇总失败 | Position=",position_id,
            " | ",aggregate_error,"；EA交易继续运行");
      return false;
   }
   const string card=BuildFinalTradeCard(facts,state,deal_ticket,close_reason);
   const string notification_id="V310_PA_H2_CLOSE_"+IntegerToString((long)position_id);
   string queue_error="";
   if(!QueueLarkCardToCommonOutbox(card,notification_id,"closed_trade",deal_ticket,
                                   position_id,facts.close_time,queue_error))
   {
      Print("[Lark通知异常] 最终结算卡片排队失败 | Position=",position_id,
            " | ",queue_error,"；EA交易继续运行");
      return false;
   }
   Print("[Lark补发队列] 最终结算卡片已持久化 | Position=",position_id,
         " | Net=",DoubleToString(facts.net_profit,2));
   return true;
}

string BuildFinalTradeFactsJson(const ClosedTradeFacts &facts,
                                const TradeRuntimeState &state,
                                const ulong deal_ticket,const string close_reason,
                                const long slot_magic,const int slot_id)
{
   const double initial_risk=CalculatePlannedRiskMoney(facts.direction,
      (facts.entry_price>0.0 ? facts.entry_price : state.entry),
      state.initial_sl,
      (facts.initial_volume>0.0 ? facts.initial_volume : state.initial_volume));
    string json="{\"strategy_version\":\""+V310_EA_VERSION+"\",\"rule_version\":\""+
      PARALLEL_AUDIT_RULE_VERSION+"\",\"parameter_version\":\""+V310_PARAMETER_VERSION+
      "\",\"trader\":\"A\",\"slot_id\":"+IntegerToString(slot_id)+","+
      "\"slot_magic\":\""+IntegerToString(slot_magic)+"\",\"signal_id\":\""+
      JsonEscape(state.signal_id)+"\","+
      "\"direction\":\""+JsonEscape(DirectionLabel(facts.direction))+"\","+
      "\"open_time\":\""+JsonEscape(TimeToString(facts.open_time,TIME_DATE|TIME_SECONDS))+"\","+
      "\"close_time\":\""+JsonEscape(TimeToString(facts.close_time,TIME_DATE|TIME_SECONDS))+"\","+
      "\"entry_price\":"+DoubleToString(facts.entry_price,8)+","+
      "\"exit_price\":"+DoubleToString(facts.exit_price,8)+","+
      "\"initial_volume\":"+DoubleToString(facts.initial_volume,8)+","+
      "\"initial_sl\":"+DoubleToString(state.initial_sl,8)+","+
      "\"tp1_price\":"+DoubleToString(state.tp1_price,8)+","+
      "\"tp2_price\":"+DoubleToString(state.tp2_price,8)+","+
      "\"tp1_done\":"+(state.tp1_done ? "true" : "false")+","+
      "\"tp2_done\":"+(state.tp2_done ? "true" : "false")+","+
      "\"gross_profit\":"+DoubleToString(facts.gross_profit,8)+","+
      "\"commission\":"+DoubleToString(facts.commission,8)+","+
      "\"swap\":"+DoubleToString(facts.swap,8)+","+
      "\"fee\":"+DoubleToString(facts.fee,8)+","+
      "\"final_deal_ticket\":\""+IntegerToString((long)deal_ticket)+"\","+
      "\"close_reason\":\""+JsonEscape(close_reason)+"\",";
   if(facts.available)
      json+="\"net_profit\":"+DoubleToString(facts.net_profit,8)+",";
   if(initial_risk>0.0)
      json+="\"initial_risk\":"+DoubleToString(initial_risk,8)+",";
   json+="\"status\":\"closed\"}";
   return json;
}

bool SendFinalTradeCard(const TradeRuntimeState &state,const ulong position_id,
                        const ulong deal_ticket,const string close_reason,
                        const long slot_magic,const int slot_id)
{
   if(!InpLarkOpenNotifyEnabled || IsTester()) return false;
   ClosedTradeFacts facts;
   string aggregate_error="";
   if(!AggregateClosedTradeFacts(_Symbol,slot_magic,position_id,facts,aggregate_error))
   {
      ResetClosedTradeFacts(facts);
      facts.position_id=position_id;
      facts.direction=state.direction;
      facts.initial_volume=state.initial_volume;
      facts.entry_price=state.entry;
      facts.open_time=state.pending_created;
      if(HistoryDealSelect(deal_ticket))
      {
         const long deal_type=HistoryDealGetInteger(deal_ticket,DEAL_TYPE);
         if(facts.direction==DIR_NONE)
            facts.direction=(deal_type==DEAL_TYPE_SELL ? DIR_BUY : DIR_SELL);
         facts.close_time=(datetime)HistoryDealGetInteger(deal_ticket,DEAL_TIME);
         facts.exit_price=HistoryDealGetDouble(deal_ticket,DEAL_PRICE);
         facts.closed_volume=HistoryDealGetDouble(deal_ticket,DEAL_VOLUME);
         facts.gross_profit=HistoryDealGetDouble(deal_ticket,DEAL_PROFIT);
         facts.commission=HistoryDealGetDouble(deal_ticket,DEAL_COMMISSION);
         facts.swap=HistoryDealGetDouble(deal_ticket,DEAL_SWAP);
         facts.fee=HistoryDealGetDouble(deal_ticket,DEAL_FEE);
      }
      Print("[Lark通知异常] 最终结算历史汇总不完整 | Position=",position_id,
            " | ",aggregate_error,"；仍按生命周期事实发送最终卡");
   }
   const string facts_json=BuildFinalTradeFactsJson(facts,state,deal_ticket,close_reason,
                                                    slot_magic,slot_id);
   const string notification_id="V310_PA_H2_"+IntegerToString((long)position_id)+"_FINAL";
   string queue_error="";
   const datetime created=(facts.close_time>0 ? facts.close_time : TimeTradeServer());
   if(!QueueLarkFactsToCommonOutbox(facts_json,notification_id,"final_trade_facts",
                                    deal_ticket,position_id,created,queue_error))
   {
      Print("[Lark通知异常] 最终结算事实排队失败 | Position=",position_id,
            " | ",queue_error,"；EA交易继续运行");
      return false;
   }
   Print("[Lark事实队列] 最终结算已持久化 | Position=",position_id,
         " | HistoryComplete=",(facts.available ? "true" : "false"));
   return true;
}

bool WriteDailyReviewCloseTrigger(const DailyReviewSession &session,string &error)
{
   error="";
   const string base_path=g_observation.BasePath();
   if(base_path=="" || !session.available)
   {
      error="复盘触发参数或公共目录无效";
      return false;
   }
   const string trigger_root=base_path+"\\Session_Close_Triggers";
   const string pending_dir=trigger_root+"\\Pending";
   const string processed_dir=trigger_root+"\\Processed";
   FolderCreate(trigger_root,FILE_COMMON);
   FolderCreate(pending_dir,FILE_COMMON);
   FolderCreate(processed_dir,FILE_COMMON);

   string close_token=TimeToString(session.session_close,TIME_DATE|TIME_MINUTES);
   StringReplace(close_token,".","");
   StringReplace(close_token,":","");
   StringReplace(close_token," ","_");
   const string trigger_id="CLOSE_"+LarkSafeFileToken(_Symbol)+"_"+close_token;
   const string filename=trigger_id+".json";
   const string pending_path=base_path+"\\Session_Close_Triggers\\Pending\\"+filename;
   const string processed_path=base_path+"\\Session_Close_Triggers\\Processed\\"+filename;
   if(FileIsExist(pending_path,FILE_COMMON) || FileIsExist(processed_path,FILE_COMMON))
      return true;

   const string json="{\"schema_version\":1,"+
      "\"trigger_id\":\""+JsonEscape(trigger_id)+"\","+
      "\"review_key\":\""+JsonEscape(session.review_key)+"\","+
      "\"symbol\":\""+JsonEscape(_Symbol)+"\","+
      "\"session_open_server\":\""+
         JsonEscape(TimeToString(session.session_open,TIME_DATE|TIME_SECONDS))+"\","+
      "\"session_close_server\":\""+
         JsonEscape(TimeToString(session.session_close,TIME_DATE|TIME_SECONDS))+"\","+
      "\"ready_server\":\""+
         JsonEscape(TimeToString(session.ready_server,TIME_DATE|TIME_SECONDS))+"\","+
      "\"created_server\":\""+
         JsonEscape(TimeToString(TimeTradeServer(),TIME_DATE|TIME_SECONDS))+"\","+
      "\"used_fallback\":"+(session.used_fallback?"true":"false")+","+
      "\"reason\":\""+JsonEscape(session.reason)+"\"}";
   const string temp_path=pending_path+".tmp";
   ResetLastError();
   const int handle=FileOpen(temp_path,FILE_WRITE|FILE_TXT|FILE_ANSI|FILE_COMMON|
                             FILE_SHARE_READ,0,CP_UTF8);
   if(handle==INVALID_HANDLE)
   {
      error="无法写入经纪商收盘复盘触发，MT5错误="+IntegerToString(GetLastError());
      return false;
   }
   FileWriteString(handle,json);
   FileFlush(handle);
   FileClose(handle);
   ResetLastError();
   if(!FileMove(temp_path,FILE_COMMON,pending_path,FILE_COMMON|FILE_REWRITE))
   {
      error="无法提交经纪商收盘复盘触发，MT5错误="+IntegerToString(GetLastError());
      FileDelete(temp_path,FILE_COMMON);
      return false;
   }
   return true;
}

void ProcessDailyReviewCloseTriggers(const datetime server_now)
{
   if(!InpBrokerCloseReviewEnabled || IsTester() || !g_observation.IsEnabled() ||
      server_now<=0) return;
   for(int day_offset=-1;day_offset<=0;day_offset++)
   {
      const datetime reference_day=(datetime)((long)server_now+day_offset*86400);
      DailyReviewSession session;
      if(!ResolveDailyReviewSession(_Symbol,reference_day,
         InpBrokerCloseReviewDelayMinutes,InpBrokerCloseFallbackHour,
         InpBrokerCloseFallbackMinute,session)) continue;
      if(server_now<session.ready_server ||
         server_now>session.ready_server+18*3600) continue;
      string error="";
      if(!WriteDailyReviewCloseTrigger(session,error))
      {
         Print("[收盘复盘触发异常] Review=",session.review_key," | ",error);
         continue;
      }
      static string last_logged_trigger="";
      const string log_key=session.review_key+"_"+
         TimeToString(session.session_close,TIME_DATE|TIME_MINUTES);
      if(last_logged_trigger!=log_key)
      {
         last_logged_trigger=log_key;
         Print("[收盘复盘触发] Review=",session.review_key," | Session=",
               TimeToString(session.session_open,TIME_DATE|TIME_SECONDS)," - ",
               TimeToString(session.session_close,TIME_DATE|TIME_SECONDS),
               " | UsedFallback=",(session.used_fallback?"true":"false"));
      }
   }
}

void MaybeProcessDailyReviewCloseTriggers(const datetime server_now,const bool force,
                                          const string source)
{
   if(!InpBrokerCloseReviewEnabled || IsTester() || !g_observation.IsEnabled() ||
      server_now<=0) return;
   if(!force && g_last_daily_review_check_server>0 &&
      server_now>=g_last_daily_review_check_server &&
      server_now-g_last_daily_review_check_server<30)
      return;
   g_last_daily_review_check_server=server_now;

   const bool log_diagnostic=(force || g_last_daily_review_diagnostic_server<=0 ||
      server_now<g_last_daily_review_diagnostic_server ||
      server_now-g_last_daily_review_diagnostic_server>=3600);
   if(log_diagnostic)
   {
      g_last_daily_review_diagnostic_server=server_now;
      DailyReviewSession session;
      const datetime reference_day=(datetime)((long)server_now-86400);
      if(ResolveDailyReviewSession(_Symbol,reference_day,
         InpBrokerCloseReviewDelayMinutes,InpBrokerCloseFallbackHour,
         InpBrokerCloseFallbackMinute,session))
      {
         string status="READY";
         if(server_now<session.ready_server) status="WAITING";
         else if(server_now>session.ready_server+18*3600) status="EXPIRED";
         Print("[收盘复盘调度诊断] Source=",source,
               " | Status=",status,
               " | Review=",session.review_key,
               " | SessionOpen=",TimeToString(session.session_open,TIME_DATE|TIME_SECONDS),
               " | SessionClose=",TimeToString(session.session_close,TIME_DATE|TIME_SECONDS),
               " | Ready=",TimeToString(session.ready_server,TIME_DATE|TIME_SECONDS),
               " | ServerNow=",TimeToString(server_now,TIME_DATE|TIME_SECONDS),
               " | UsedFallback=",(session.used_fallback ? "true" : "false"));
      }
      else
      {
         Print("[收盘复盘调度诊断] Source=",source,
               " | Status=NO_SESSION_OR_WEEKEND | Reference=",
               TimeToString(reference_day,TIME_DATE|TIME_SECONDS),
               " | Reason=",session.reason);
      }
   }
   ProcessDailyReviewCloseTriggers(server_now);
}

bool LegacySendOpenTradeCard(const TradeRuntimeState &before_state,
                             const TradeRuntimeState &filled_state,
                             const ulong position_id,const ulong deal_ticket,
                             const double deal_price,const double deal_volume,
                             const datetime deal_time)
{
   if(!InpLarkOpenNotifyEnabled || IsTester()) return false;
   if(deal_ticket==0 || IsLarkDealAlreadySent(deal_ticket)) return true;

   LarkTradeContext ctx;
   string load_error="";
   const bool context_loaded=LoadLarkTradeContext(before_state.signal_id,
                                                   before_state.order_ticket,
                                                   ctx,load_error);
   if(!context_loaded)
   {
      ResetLarkTradeContext(ctx);
      ctx.signal_id=before_state.signal_id;
      ctx.order_ticket=before_state.order_ticket;
      ctx.direction=filled_state.direction;
      ctx.signal_route=filled_state.signal_route;
      Print("[Lark通知异常] 开仓原因快照读取失败 | SignalID=",before_state.signal_id,
            " | Error=",load_error,"；仍发送最小成交卡片");
   }

   const string card=BuildOpenTradeCard(ctx,filled_state,position_id,deal_ticket,
                                        deal_price,deal_volume,deal_time,context_loaded);
   string send_error="";
   bool direct_sent=false;
   if(g_lark.IsEnabled())
   {
      if(!g_lark.SendCard(card,send_error)) direct_sent=false;
      else direct_sent=true;
   }
   if(!direct_sent)
   {
      if(send_error=="") send_error="MT5直发通道未启用";
      string queue_error="";
      const string notification_id="V310_PA_H2_OPEN_"+IntegerToString((long)position_id);
      if(!QueueLarkCardToCommonOutbox(card,notification_id,"open_trade",deal_ticket,
                                      position_id,deal_time,queue_error))
      {
         Print("[Lark通知异常] 开仓卡片直发和补发排队均失败 | Deal=",deal_ticket,
               " | Direct=",send_error," | Queue=",queue_error,"；EA交易继续运行");
         return false;
      }
      string queued_mark_error="";
      if(!MarkLarkDealSent(deal_ticket,queued_mark_error))
         Print("[Lark通知异常] 已进入补发队列但Deal去重记录失败 | Deal=",deal_ticket,
               " | ",queued_mark_error);
      Print("[Lark补发队列] 开仓卡片已持久化，等待Python发送 | Deal=",deal_ticket,
            " | Direct=",send_error);
      return true;
   }

   string mark_error="";
   if(!MarkLarkDealSent(deal_ticket,mark_error))
      Print("[Lark通知异常] 已发送但Deal去重记录失败 | Deal=",deal_ticket,
            " | ",mark_error);
   else
      Print("[Lark开仓通知] 卡片发送成功 | Deal=",deal_ticket,
            " | Position=",position_id," | SignalID=",before_state.signal_id);
   return true;
}

string BuildOpenTradeFactsJson(const LarkTradeContext &ctx,
                               const TradeRuntimeState &filled_state,
                               const double deal_price,const double deal_volume,
                               const datetime deal_time,const bool context_loaded,
                               const long slot_magic,const int slot_id)
{
   const double frozen_volume=(filled_state.initial_volume>0.0 ?
                               filled_state.initial_volume : deal_volume);
   const double initial_risk=CalculatePlannedRiskMoney(filled_state.direction,
      deal_price,filled_state.initial_sl,frozen_volume);
   const string signal_id=(context_loaded ? ctx.signal_id : filled_state.signal_id);
   const string route=(context_loaded ? SignalRouteLabel(ctx.signal_route,ctx.direction) :
                       SignalRouteLabel(filled_state.signal_route,filled_state.direction));
   const double actual_r=MathAbs(deal_price-filled_state.initial_sl);
    string json="{\"strategy_version\":\""+V310_EA_VERSION+"\",\"rule_version\":\""+
      PARALLEL_AUDIT_RULE_VERSION+"\",\"parameter_version\":\""+V310_PARAMETER_VERSION+
      "\",\"trader\":\"A\",\"slot_id\":"+IntegerToString(slot_id)+","+
      "\"slot_magic\":\""+IntegerToString(slot_magic)+"\",\"h_state\":\"H2\",\"actual_r\":"+
      DoubleToString(actual_r,8)+",\"server_sl_status\":\"PROTECTED\",\"signal_id\":\""+
      JsonEscape(signal_id)+"\","+
      "\"direction\":\""+JsonEscape(DirectionLabel(filled_state.direction))+"\","+
      "\"open_time\":\""+JsonEscape(TimeToString(deal_time,TIME_DATE|TIME_SECONDS))+"\","+
      "\"entry_price\":"+DoubleToString(deal_price,8)+","+
      "\"initial_volume\":"+DoubleToString(frozen_volume,8)+","+
      "\"initial_sl\":"+DoubleToString(filled_state.initial_sl,8)+","+
      "\"initial_risk\":"+DoubleToString(initial_risk,8)+","+
      "\"tp1_price\":"+DoubleToString(filled_state.tp1_price,8)+","+
      "\"tp2_price\":"+DoubleToString(filled_state.tp2_price,8)+","+
      "\"signal_route\":\""+JsonEscape(route)+"\","+
      "\"context_loaded\":"+(context_loaded ? "true" : "false")+",";
   if(context_loaded)
   {
      json+="\"fib_retracement\":"+DoubleToString(ctx.fib_retracement,4)+","+
         "\"h_attempt\":"+IntegerToString(ctx.h_attempt)+","+
         "\"ema_distance_usd\":"+DoubleToString(ctx.ema_distance_usd,4)+","+
         "\"sr_confluence\":"+(ctx.sr_confluence ? "true" : "false")+","+
         "\"ma_confluence\":"+(ctx.ma_confluence ? "true" : "false")+","+
         "\"pattern\":\""+JsonEscape(LarkPatternText(ctx))+"\","+
         "\"ai_allow_trade\":"+(ctx.ai_allow_trade ? "true" : "false")+","+
         "\"ai_is_error\":"+(ctx.ai_is_error ? "true" : "false")+","+
         "\"ai_confidence\":"+IntegerToString(ctx.ai_confidence)+","+
         "\"ai_reason\":\""+JsonEscape(ctx.ai_reason)+"\",";
   }
   json+="\"status\":\"open\"}";
   return json;
}

bool SendOpenTradeCard(const TradeRuntimeState &before_state,
                       const TradeRuntimeState &filled_state,
                       const ulong position_id,const ulong deal_ticket,
                       const double deal_price,const double deal_volume,
                       const datetime deal_time,const long slot_magic,
                       const int slot_id)
{
   if(!InpLarkOpenNotifyEnabled || IsTester()) return false;
   if(deal_ticket==0 || position_id==0) return false;
   LarkTradeContext ctx;
   string load_error="";
   const bool context_loaded=LoadLarkTradeContext(before_state.signal_id,
                                                   before_state.order_ticket,
                                                   ctx,load_error);
   if(!context_loaded)
   {
      ResetLarkTradeContext(ctx);
      ctx.signal_id=before_state.signal_id;
      ctx.order_ticket=before_state.order_ticket;
      ctx.direction=filled_state.direction;
      ctx.signal_route=filled_state.signal_route;
      Print("[Lark通知异常] 开仓原因快照读取失败 | SignalID=",before_state.signal_id,
            " | Error=",load_error,"；仍按实际成交事实生成开仓卡");
   }
   const string facts_json=BuildOpenTradeFactsJson(ctx,filled_state,deal_price,
                                                   deal_volume,deal_time,context_loaded,
                                                   slot_magic,slot_id);
   const string notification_id="V310_PA_H2_"+IntegerToString((long)position_id)+"_OPEN";
   string queue_error="";
   if(!QueueLarkFactsToCommonOutbox(facts_json,notification_id,"open_trade_facts",
                                    deal_ticket,position_id,deal_time,queue_error))
   {
      Print("[Lark通知异常] 开仓事实排队失败 | Deal=",deal_ticket,
            " | ",queue_error,"；EA交易继续运行");
      return false;
   }
   Print("[Lark事实队列] 开仓事实已持久化，等待Python生成点评并发送 | Deal=",
         deal_ticket," | Position=",position_id);
   return true;
}

void EmitScanResult(const ScanResult &scan)
{
   g_last_scan_result=scan;
   g_last_scan_text=FormatScanResult(scan);
   Print(g_last_scan_text);
   string event_type="scan_result";
   if(scan.outcome==SCAN_REJECT) event_type="local_reject";
   else if(scan.stage==STAGE_RISK_LOCK) event_type="risk_block";
   RecordObservationEvent(event_type,"",ObservationStageLabel(scan.stage),
                          ObservationOutcomeLabel(scan.outcome),scan.reason,g_last_scan_text);
   // The trace is published after the blind input file and may be atomically
   // replaced later as EA AI-review/execution facts become available.
   WriteParallelEATrace(scan);
   const TradeRuntimeState runtime_state=g_trades.State();
   if(!g_observation.WriteMarketSnapshot(TimeTradeServer(),scan,g_weekend_status_text,
                                         g_rollover_status_text,runtime_state))
      Print("[AI盯盘数据异常] 市场快照写入失败 | Error=",GetLastError());
   UpdateChartStatus();
}


ENUM_PENDING_DISTANCE_STATUS EvaluatePendingDistance(const ENUM_TRADE_DIRECTION direction,
                                                     const double entry,
                                                     const double bid,const double ask,
                                                     const double point,const double tick_size,
                                                     const int stops_level,
                                                     double &minimum_distance,string &reason)
{
   minimum_distance=0.0;
   reason="";
   if((direction!=DIR_BUY && direction!=DIR_SELL) || entry<=0.0 ||
      bid<=0.0 || ask<=0.0 || point<=0.0)
   {
      reason="挂单距离复检输入无效";
      return PENDING_DISTANCE_INVALID;
   }
   minimum_distance=MathMax(0.0,(double)stops_level)*point;
   const double effective_tick_size=(tick_size>0.0 ? tick_size : point);
   const double epsilon=MathMax(point,effective_tick_size)*0.1;
   if(direction==DIR_BUY)
   {
      if(entry<=ask)
      {
         reason="买入挂单价已被Ask达到或越过";
         return PENDING_DISTANCE_ENTRY_PASSED;
      }
      if(entry+epsilon<ask+minimum_distance)
      {
         reason="买入挂单距离不足";
         return PENDING_DISTANCE_TOO_CLOSE;
      }
   }
   else
   {
      if(entry>=bid)
      {
         reason="卖出挂单价已被Bid达到或越过";
         return PENDING_DISTANCE_ENTRY_PASSED;
      }
      if(entry-epsilon>bid-minimum_distance)
      {
         reason="卖出挂单距离不足";
         return PENDING_DISTANCE_TOO_CLOSE;
      }
   }
   return PENDING_DISTANCE_READY;
}

void ArmWaitingPendingSignal(const CandidateSignal &candidate,const AIDecision &decision,
                             const datetime expiry,const double bid,const double ask,
                             const int stops_level,const double minimum_distance)
{
   ResetPendingSignalWaitState(g_waiting_pending);
   g_waiting_pending.active=true;
   g_waiting_pending.candidate=candidate;
   g_waiting_pending.decision=decision;
   g_waiting_pending.created_at=TimeTradeServer();
   g_waiting_pending.expiry=expiry;
   const double market_price=(candidate.direction==DIR_BUY ? ask : bid);
   const string message="[待挂信号] 距离不足，进入等待 | ID="+candidate.signal_id+
      " | Direction="+DirectionLabel(candidate.direction)+
      " | Entry="+DoubleToString(candidate.planned_entry,_Digits)+
      " | Current="+DoubleToString(market_price,_Digits)+
      " | StopsLevel="+IntegerToString(stops_level)+
      " | MinDistance="+DoubleToString(minimum_distance,_Digits)+
      " | Expiry="+ObservationTimeText(expiry)+
      " | 未创建真实订单";
   NotifyEvent(candidate.signal_id+"_WAIT_DISTANCE",message);
   RecordObservationEvent("pending_wait_started",candidate.signal_id,"order","wait",
                          "挂单距离不足，等待经纪商最小距离满足",message,0,0,0,
                          candidate.direction,0.0,candidate.planned_entry,0.0);
}

void ClearWaitingPendingSignal(const string event_type,const string outcome,
                               const string reason,const string message)
{
   if(!g_waiting_pending.active) return;
   const CandidateSignal candidate=g_waiting_pending.candidate;
   if(message!="") Print(message);
   RecordObservationEvent(event_type,candidate.signal_id,"order",outcome,reason,
                          message,0,0,0,candidate.direction,0.0,
                          candidate.planned_entry,0.0);
   ResetPendingSignalWaitState(g_waiting_pending);
}

bool SubmitWaitingPendingSignal(string &error)
{
   error="";
   if(!g_waiting_pending.active) return false;
   const CandidateSignal candidate=g_waiting_pending.candidate;
   const AIDecision decision=g_waiting_pending.decision;
   const datetime expiry=g_waiting_pending.expiry;

   const double tick_size=SymbolInfoDouble(_Symbol,SYMBOL_TRADE_TICK_SIZE);
   double tick_value=SymbolInfoDouble(_Symbol,SYMBOL_TRADE_TICK_VALUE_LOSS);
   if(tick_value<=0.0) tick_value=SymbolInfoDouble(_Symbol,SYMBOL_TRADE_TICK_VALUE);
   const double volume=CalculateRiskVolume(AccountInfoDouble(ACCOUNT_EQUITY),InpRiskPercent,
      candidate.planned_entry,candidate.planned_sl,tick_size,tick_value,
      SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_MIN),SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_MAX),
      SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_STEP),error);
   if(volume<=0.0) return false;

   if(!g_trades.PlacePending(candidate,volume,expiry,error)) return false;

   const TradeRuntimeState pending_state=g_trades.State();
   RecordObservationEvent("pending_created",candidate.signal_id,"order","pass",
                          "待挂距离满足后创建挂单","expiry="+ObservationTimeText(expiry),
                          pending_state.order_ticket,pending_state.position_id,0,
                          candidate.direction,volume,candidate.planned_entry,0.0);
   if(InpLarkOpenNotifyEnabled)
   {
      LarkTradeContext lark_ctx;
      BuildLarkTradeContext(candidate,decision,pending_state.order_ticket,lark_ctx);
      string lark_context_error="";
      if(!SaveLarkTradeContext(lark_ctx,lark_context_error))
         Print("[Lark通知异常] 开仓原因快照保存失败 | SignalID=",candidate.signal_id,
               " | Error=",lark_context_error,"；EA交易继续运行");
   }
   NotifyEvent(candidate.signal_id+"_PENDING",
               "[挂单] 成功 | ID="+candidate.signal_id);
   WriteParallelCandidateEATrace(g_last_scan_result,candidate,decision,"PASS",
                                 "PENDING_CREATED","等待距离满足后挂单创建成功");
   Print("[待挂信号] 距离满足，已提交真实挂单 | ID=",candidate.signal_id,
         " | Entry=",DoubleToString(candidate.planned_entry,_Digits),
         " | Expiry=",ObservationTimeText(expiry));
   ResetPendingSignalWaitState(g_waiting_pending);
   return true;
}

bool ProcessWaitingPendingSignal(const datetime server_now,
                                 const WeekendGuardStatus &weekend_guard,
                                 const RolloverGuardStatus &rollover_guard)
{
   if(!g_waiting_pending.active) return false;
   const CandidateSignal candidate=g_waiting_pending.candidate;

   if(g_waiting_pending.expiry<=0 || server_now>=g_waiting_pending.expiry)
   {
      const string message="[待挂信号] 等待超时，未创建真实订单 | ID="+candidate.signal_id+
         " | Entry="+DoubleToString(candidate.planned_entry,_Digits);
      ClearWaitingPendingSignal("pending_wait_expired","expired","等待超过信号有效期",message);
      return false;
   }
   if(!CanOpenNewTradeNow(weekend_guard))
   {
      const string message="[待挂信号] 周末风控进入禁开窗口，等待信号作废 | ID="+
         candidate.signal_id+" | 未创建真实订单";
      ClearWaitingPendingSignal("pending_wait_cleared","blocked",weekend_guard.reason,message);
      return false;
   }
   if(!CanOpenNewTradeDuringRollover(rollover_guard))
   {
      const string message="[待挂信号] 换日风控进入禁开窗口，等待信号作废 | ID="+
         candidate.signal_id+" | 未创建真实订单";
      ClearWaitingPendingSignal("pending_wait_cleared","blocked",rollover_guard.reason,message);
      return false;
   }
   if(g_risk.IsEntryLocked())
   {
      const string message="[待挂信号] 本EA风控已锁定，等待信号作废 | ID="+
         candidate.signal_id+" | 未创建真实订单";
      ClearWaitingPendingSignal("pending_wait_cleared","blocked","本EA禁止新开仓",message);
      return false;
   }
   if(V3109C32HasManagedExposure())
   {
      const string message="[待挂信号] 已存在本EA持仓或真实挂单，等待信号作废 | ID="+
         candidate.signal_id;
      ClearWaitingPendingSignal("pending_wait_cleared","blocked","已有本EA敞口",message);
      return false;
   }

   MqlTick tick;
   if(!SymbolInfoTick(_Symbol,tick) || tick.bid<=0.0 || tick.ask<=0.0)
   {
      NotifyEvent(candidate.signal_id+"_WAIT_TICK_ERROR",
                  "[待挂信号] 暂时无法读取Bid/Ask，继续等待 | ID="+candidate.signal_id);
      return true;
   }

   const double point=SymbolInfoDouble(_Symbol,SYMBOL_POINT);
   const double tick_size=SymbolInfoDouble(_Symbol,SYMBOL_TRADE_TICK_SIZE);
   const int stops_level=(int)SymbolInfoInteger(_Symbol,SYMBOL_TRADE_STOPS_LEVEL);
   double minimum_distance=0.0;
   string distance_reason="";
   const ENUM_PENDING_DISTANCE_STATUS distance_status=EvaluatePendingDistance(
      candidate.direction,candidate.planned_entry,tick.bid,tick.ask,point,tick_size,
      stops_level,minimum_distance,distance_reason);

   if(distance_status==PENDING_DISTANCE_ENTRY_PASSED)
   {
      const string message="[待挂信号] 原入场价已被市场触达，信号作废，未追价 | ID="+
         candidate.signal_id+" | Entry="+DoubleToString(candidate.planned_entry,_Digits)+
         " | Current="+DoubleToString(candidate.direction==DIR_BUY ? tick.ask : tick.bid,_Digits);
      ClearWaitingPendingSignal("pending_wait_missed","expired",distance_reason,message);
      return false;
   }
   if(distance_status==PENDING_DISTANCE_INVALID)
   {
      const string message="[待挂信号] 距离复检输入异常，等待信号作废 | ID="+
         candidate.signal_id+" | "+distance_reason;
      ClearWaitingPendingSignal("pending_wait_cleared","error",distance_reason,message);
      return false;
   }
   if(distance_status==PENDING_DISTANCE_TOO_CLOSE)
      return true;

   string spread_error="";
   if(!g_risk.CheckSpread(tick.bid,tick.ask,candidate.atr14,InpMaxSpreadATRRatio,
                          InpAbsoluteMaxSpreadPoints,_Point,spread_error))
      return true;

   const double live_price=(candidate.direction==DIR_BUY ? tick.ask : tick.bid);
   string move_error="";
   if(!g_risk.CheckPostAIMove(candidate.direction,candidate.planned_entry,live_price,
                              candidate.atr14,InpMaxPostAIMoveATR,move_error))
   {
      const string message="[待挂信号] 行情已不满足AI后价格复检，等待信号作废 | ID="+
         candidate.signal_id+" | "+move_error;
      ClearWaitingPendingSignal("pending_wait_cleared","reject",move_error,message);
      return false;
   }

   string submit_error="";
   if(SubmitWaitingPendingSignal(submit_error)) return true;

   // PlacePending会再次读取最新Tick；若两次检查之间价格变化导致距离再次不足，则继续等待，
   // 不创建订单、不产生取消单，也不重复发送失败通知。
   if(StringFind(submit_error,"挂单距离不足")>=0)
      return true;
   if(StringFind(submit_error,"达到或越过")>=0)
   {
      const string message="[待挂信号] 原入场价已被市场触达，信号作废，未追价 | ID="+
         candidate.signal_id+" | "+submit_error;
      ClearWaitingPendingSignal("pending_wait_missed","expired",submit_error,message);
      return false;
   }

   const string message="[待挂信号] 提交真实挂单失败，等待信号作废 | ID="+
      candidate.signal_id+" | "+submit_error;
   ClearWaitingPendingSignal("execution_error","error",submit_error,message);
   NotifyEvent(candidate.signal_id+"_PENDING_FAIL","[挂单] 失败 | "+submit_error);
   return false;
}

int OnInit()
{
   g_signal_timeframe=PERIOD_M5;
   V3109ResetM15TrendMemory(g_v3109_m15_memory);
   if((ENUM_TIMEFRAMES)_Period!=PERIOD_M5)
   {
      Print("[异常] 本EA只能在M5图表或M5回测周期运行 | 当前周期=",
            EnumToString((ENUM_TIMEFRAMES)_Period)," | 要求周期=M5");
      return INIT_PARAMETERS_INCORRECT;
   }
   const bool demo=(AccountInfoInteger(ACCOUNT_TRADE_MODE)==ACCOUNT_TRADE_MODE_DEMO);
   if(!CanInitializeEnvironment(demo,IsTester(),InpAIMode))
   {
      Print(IsTester() && InpAIMode==AI_LIVE ?
            "AI_LIVE不能用于Strategy Tester，请使用AI_OFF或AI_REPLAY。" :
            "本EA仅允许策略测试器或模拟账户，实盘账户拒绝运行。");
      return INIT_FAILED;
   }
   if(AccountInfoInteger(ACCOUNT_MARGIN_MODE)!=ACCOUNT_MARGIN_MODE_RETAIL_HEDGING)
   {
      Print("V3109_C32_INIT_REJECT|reason=HEDGING_ACCOUNT_REQUIRED");
      return INIT_FAILED;
   }
   if(InpMagicNumber<=0 || InpMagicNumber>9223372036854775805)
   {
      Print("V3109_C32_INIT_REJECT|reason=INVALID_BASE_MAGIC");
      return INIT_PARAMETERS_INCORRECT;
   }
   string staged_parameter_error="";
   if(InpUseStagedExit && !ValidateStagedExitParameters(InpTP1RiskMultiple,InpTP1ClosePercent,
      InpBreakEvenSpreadMultiplier,InpBreakEvenMinBufferPoints,InpTP2RiskMultiple,
      InpTP2ClosePercent,InpTP2LockRiskMultiple,staged_parameter_error))
   {
      Print("[参数错误] ",staged_parameter_error);
      return INIT_PARAMETERS_INCORRECT;
   }
   if(!InpUseStagedExit && (InpTP1ClosePercent<30.0 || InpTP1ClosePercent>50.0))
   {
      Print("[参数错误] Legacy模式TP1平仓比例必须在30%-50%之间");
      return INIT_PARAMETERS_INCORRECT;
   }
   if(InpEMASignalBarStopUSD<=0.0)
   {
      Print("[参数错误] EMA信号K止损扩展美元值必须大于0");
      return INIT_PARAMETERS_INCORRECT;
   }
   if(InpEMAMaxStopExpansionRatio<1.0)
   {
      Print("[参数错误] EMA最大止损扩张比例必须>=1.0");
      return INIT_PARAMETERS_INCORRECT;
   }
   if(InpRiskPercent<0.1 || InpRiskPercent>1.0 ||
      InpAIConfidenceThreshold<0 || InpAIConfidenceThreshold>100 ||
      InpEMAPeriod<1 || InpMinThreeBarMoveUSD<=0.0 ||
      InpEMANearDistanceUSD<=0.0 || InpMaxSignalBarUSD<=0.0 ||
      InpBrokerCloseReviewDelayMinutes<1 || InpBrokerCloseReviewDelayMinutes>60 ||
      InpBrokerCloseFallbackHour<0 || InpBrokerCloseFallbackHour>23 ||
      InpBrokerCloseFallbackMinute<0 || InpBrokerCloseFallbackMinute>59 ||
      InpRolloverStartHour<0 || InpRolloverStartHour>23 ||
      InpRolloverStartMinute<0 || InpRolloverStartMinute>59 ||
      InpRolloverEndHour<0 || InpRolloverEndHour>23 ||
      InpRolloverEndMinute<0 || InpRolloverEndMinute>59 ||
      (InpRolloverGuardEnabled &&
       InpRolloverStartHour==InpRolloverEndHour &&
       InpRolloverStartMinute==InpRolloverEndMinute) ||
      InpWeekendNoNewTradeMinutes<0 || InpWeekendNoNewTradeMinutes>1440 ||
      InpWeekendForceCloseMinutes<0 || InpWeekendForceCloseMinutes>1440 ||
      InpWeekendNoNewTradeMinutes<InpWeekendForceCloseMinutes ||
      InpWeekendFallbackFridayCloseHour<0 || InpWeekendFallbackFridayCloseHour>23 ||
      InpWeekendFallbackFridayCloseMinute<0 || InpWeekendFallbackFridayCloseMinute>59)
   { Print("[异常] input参数超出安全范围"); return INIT_PARAMETERS_INCORRECT; }
   string error;
   if(!g_indicators.Init(_Symbol,g_signal_timeframe,InpEMAPeriod,InpATRPeriod,InpRSIPeriod,
                         InpMACDFast,InpMACDSlow,InpMACDSignal,error))
   { Print("[异常] ",error); return INIT_FAILED; }
   if(!g_ai.Init(InpAIMode,InpAPIKeySource,InpDeepSeekApiKey,InpAPIKeyFile,
                 InpReplayFile,InpDeepSeekModel,InpAITimeoutMs,InpAIRetryCount,
                 InpAIFailOpenGrace,error))
   { Print("[异常] ",error); return INIT_FAILED; }
   if(InpAIMode==AI_LIVE)
   {
      AIConnectionResult connection;
      if(!g_ai.TestConnection(connection,error))
      {
         g_ai_connection_text=FormatConnectionFailure(connection);
         Print(g_ai_connection_text);
         if(InpEnablePopupAlert && !IsTester()) Alert(g_ai_connection_text);
         return INIT_FAILED;
      }
      g_ai_connection_text=FormatConnectionSuccess(connection);
      Print(g_ai_connection_text);
      if(InpEnablePopupAlert && !IsTester()) Alert(g_ai_connection_text);
   }
   else
   {
      g_ai_connection_text=(InpAIMode==AI_OFF ? "AI_OFF本地模式" : "AI_REPLAY回放模式");
      Print("[AI连接] ",g_ai_connection_text,"，不访问网络");
   }
   for(int slot=0;slot<V3109C32_MAX_SLOTS;slot++)
   {
      const long slot_magic=V3109C32SlotMagic(InpMagicNumber,slot);
      g_trade_slots[slot].Init(_Symbol,slot_magic,InpUseStagedExit,
         InpTP1RiskMultiple,InpTP1ClosePercent,InpMoveToBreakEvenAfterTP1,
         InpBreakEvenSpreadMultiplier,InpBreakEvenMinBufferPoints,
         InpTP2RiskMultiple,InpTP2ClosePercent,InpTP2LockRiskMultiple,
         InpUseStructureTargetAsServerTP);
      g_trade_slots[slot].Restore();
      V3109C32RecoverPendingContext(slot);
   }
   g_csv.Init();
   g_lark_context_store.Init(_Symbol,InpMagicNumber);
   if(InpLarkOpenNotifyEnabled && !IsTester())
      Print("[Lark通知] Python持久化队列模式已启用 | 每笔交易仅OPEN与FINAL各一张");
   ResetScanResult(g_last_scan_result,g_signal_timeframe,0);
   ResetPendingSignalWaitState(g_waiting_pending);
   string observation_error="";
   if(!g_observation.Init(_Symbol,InpMagicNumber,InpObservationRootFolder,
                          InpObservationLoggingEnabled,InpObservationInTester,
                          InpEMAPeriod,InpATRPeriod,InpRSIPeriod,InpMACDFast,
                          InpMACDSlow,InpMACDSignal,observation_error))
      Print("[AI盯盘数据异常] ",observation_error,"；EA交易继续运行");
   g_calendar.Init(g_observation.BasePath(),InpEconomicCalendarLoggingEnabled &&
                   g_observation.IsEnabled());
   if(!IsTester() && InpBrokerCloseReviewEnabled)
   {
      ResetLastError();
      if(!EventSetTimer(30))
         Print("[收盘复盘触发异常] 无法启动30秒定时器，MT5错误=",GetLastError());
      else
         Print("[收盘复盘调度] 30秒定时器已启动");
   }
   MaybeProcessDailyReviewCloseTriggers(TimeTradeServer(),true,"OnInit");
   g_last_bar=iTime(_Symbol,g_signal_timeframe,0);
   RolloverGuardStatus initial_rollover_guard=EvaluateRolloverGuard(TimeTradeServer(),
      InpRolloverGuardEnabled,InpRolloverStartHour,InpRolloverStartMinute,
      InpRolloverEndHour,InpRolloverEndMinute);
   g_rollover_status_text=FormatRolloverGuardStatus(initial_rollover_guard);
   Print("[换日风控] ",g_rollover_status_text);
   WeekendGuardStatus initial_weekend_guard=EvaluateWeekendGuard(_Symbol,TimeTradeServer(),
      InpWeekendGuardEnabled,InpWeekendNoNewTradeMinutes,InpWeekendForceCloseMinutes,
      InpWeekendFallbackFridayCloseHour,InpWeekendFallbackFridayCloseMinute);
   g_weekend_status_text=FormatWeekendGuardStatus(initial_weekend_guard);
   Print("[周末风控] ",g_weekend_status_text," | 禁开提前=",InpWeekendNoNewTradeMinutes,
         "分钟 | 强平提前=",InpWeekendForceCloseMinutes,"分钟");
   Print("[周末风控时段诊断] FridayClose=",
         TimeToString(initial_weekend_guard.friday_close,TIME_DATE|TIME_SECONDS),
         " | NoNewAfter=",
         TimeToString(initial_weekend_guard.no_new_entry_time,TIME_DATE|TIME_SECONDS),
         " | ForceFlatAfter=",
         TimeToString(initial_weekend_guard.force_close_time,TIME_DATE|TIME_SECONDS),
         " | UsedFallback=",(initial_weekend_guard.used_fallback?"true":"false"));
   Print("[初始化] XAUUSD M15/M5 H2 V3.10.7 Structure-SL 已启动 | Symbol=",_Symbol,
         " | TF=",SignalTimeframeLabel(g_signal_timeframe));
   Print("V3109_C33TF1_RUN_START|time=",
         TimeToString(TimeTradeServer(),TIME_DATE|TIME_SECONDS),
         "|base_magic=",IntegerToString(InpMagicNumber),
         "|slots=",IntegerToString(V3109C32_MAX_SLOTS),
         "|risk_percent=",DoubleToString(InpRiskPercent,4),
         "|aggregate_risk_percent=",
         DoubleToString(V3109C32_MAX_AGGREGATE_RISK_PERCENT,4),
         "|stop_buffer_usd=",DoubleToString(InpEMASignalBarStopUSD,2));
   return INIT_SUCCEEDED;
}

void PrintEmaHybridSummary()
{
   if(!IsTester()) return;
   const int signal_used=g_ema_hybrid.signal_sl_used;
   const double usage=(g_ema_hybrid.candidates>0 ?
                       100.0*signal_used/g_ema_hybrid.candidates : 0.0);
   const double avg_ratio=(signal_used>0 ? g_ema_hybrid.expansion_sum/signal_used : 0.0);
   Print("[EMA混合止损统计]");
   Print("EMA/BOTH候选=",IntegerToString(g_ema_hybrid.candidates),
         " | 使用SignalSL=",IntegerToString(signal_used),
         " | 使用LegacySL=",IntegerToString(g_ema_hybrid.legacy_sl_used),
         " | SignalSL使用率=",DoubleToString(usage,1),"%");
   Print("SIGNAL_NOT_WIDER=",IntegerToString(g_ema_hybrid.not_wider),
         " | EXPANSION_TOO_LARGE=",IntegerToString(g_ema_hybrid.expansion_too_large),
         " | SIGNAL_GT_MAX_SL_ATR=",IntegerToString(g_ema_hybrid.gt_max_sl_atr),
         " | SIGNAL_RR_TOO_LOW=",IntegerToString(g_ema_hybrid.rr_too_low));
   Print("BUY SignalSL=",IntegerToString(g_ema_hybrid.buy_signal_sl),
         " | SELL SignalSL=",IntegerToString(g_ema_hybrid.sell_signal_sl),
         " | SignalSL平均ExpansionRatio=",DoubleToString(avg_ratio,3),
         " | SignalSL最大ExpansionRatio=",DoubleToString(g_ema_hybrid.expansion_max,3));
}

void OnDeinit(const int reason)
{
   EventKillTimer();
   g_indicators.Release();
   g_observation.Release();
   Comment("");
   PrintEmaHybridSummary();
   Print("V3103_TARGETSPACE_ROUTEA_DIFF_SUMMARY|buy=",IntegerToString(g_v310_routea_target_diff_buy),
         "|sell=",IntegerToString(g_v310_routea_target_diff_sell));
}

void OnTimer()
{
   MaybeProcessDailyReviewCloseTriggers(TimeTradeServer(),false,"OnTimer");
}

bool V310LoadClosedBars(const ENUM_TIMEFRAMES timeframe,const int count,V310Bar &out[])
{
   MqlRates rates[]; ArraySetAsSeries(rates,true);
   if(CopyRates(_Symbol,timeframe,V310_CLOSED_SHIFT,count,rates)!=count) return false;
   ArrayResize(out,count);
   for(int i=0;i<count;i++) V310SetBar(out[i],rates[i].time,rates[i].open,rates[i].high,rates[i].low,rates[i].close);
   return true;
}

double V310EMAFromNewest(const V310Bar &bars[],const int period)
{
   if(ArraySize(bars)<period || period<1) return 0.0;
   const double alpha=2.0/(period+1.0);
   double ema=bars[ArraySize(bars)-1].close;
   for(int i=ArraySize(bars)-2;i>=0;i--) ema=alpha*bars[i].close+(1.0-alpha)*ema;
   return ema;
}

bool V3109C33BuildEarlyTrendInput(const V310Bar &bars[],const double tick_size,
                                  V3109C33EarlyTrendInput &in)
{
   ZeroMemory(in);
   if(ArraySize(bars)<40 || tick_size<=0.0 || g_v310_m15_metrics.atr14<=0.0)
      return false;

   // Ignore the newest three M15 bars when building the breakout reference.
   // The gate therefore remains active long enough for M5 to wait for a small
   // pullback instead of authorizing only the breakout candle itself.
   in.reference_high=bars[3].high;
   in.reference_low=bars[3].low;
   for(int i=4;i<=10;i++)
   {
      in.reference_high=MathMax(in.reference_high,bars[i].high);
      in.reference_low=MathMin(in.reference_low,bars[i].low);
   }

   for(int shift=0;shift<4;shift++)
   {
      const int count=ArraySize(bars)-shift;
      V310Bar history[];
      ArrayResize(history,count);
      for(int i=0;i<count;i++) history[i]=bars[shift+i];
      const double ema=V310EMAFromNewest(history,20);
      if(ema<=0.0) return false;
      if(bars[shift].close>ema) in.above_ema_count4++;
      else if(bars[shift].close<ema) in.below_ema_count4++;
   }

   in.primary_state=(int)g_v3109_m15_trend.primary;
   in.macro_bias=g_v3109_c3_m15_macro_bias;
   in.close_price=bars[0].close;
   in.ema20=g_v310_m15_metrics.ema20;
   in.ema_slope_atr=g_v310_m15_metrics.ema_slope_atr;
   in.atr14=g_v310_m15_metrics.atr14;
   in.protected_low_intact=g_v310_m15_metrics.protected_swing_low_intact;
   in.protected_high_intact=g_v310_sell_m15_metrics.protected_swing_high_intact;
   in.tick_size=tick_size;
   return true;
}

void V3109C33ApplyEarlyTrendGate(const V310Bar &bars[],const double tick_size)
{
   g_v3109_c33_early_buy=false;
   g_v3109_c33_early_sell=false;
   g_v3109_c33_early_reason="C33_EARLY_NONE";

   V3109C33EarlyTrendInput in;
   if(!V3109C33BuildEarlyTrendInput(bars,tick_size,in))
   {
      g_v3109_c33_early_reason="C33_EARLY_INPUT_INVALID";
      return;
   }

   const ENUM_V3109_C33_EARLY_TREND early=V3109C33EvaluateEarlyTrend(in);
   if(early==V3109C33_EARLY_BUY)
   {
      g_v3109_c33_early_buy=true;
      g_v310_m15_context.allow_m5_scan=true;
      g_v310_m15_context.hard_exhaustion=false;
      g_v310_m15_context.reason_code="C33_EARLY_BULL_BREAKOUT";
      g_v310_sell_m15_context.allow_m5_scan=false;
      g_v3109_c33_early_reason="C33_EARLY_BULL_BREAKOUT";
   }
   else if(early==V3109C33_EARLY_SELL)
   {
      g_v3109_c33_early_sell=true;
      g_v310_sell_m15_context.allow_m5_scan=true;
      g_v310_sell_m15_context.hard_exhaustion=false;
      g_v310_sell_m15_context.reason_code="C33_EARLY_BEAR_BREAKDOWN";
      g_v310_m15_context.allow_m5_scan=false;
      g_v3109_c33_early_reason="C33_EARLY_BEAR_BREAKDOWN";
   }

   if(InpDebugMode)
      Print("V3109_C33_EARLY|time=",TimeToString(bars[0].time,TIME_DATE|TIME_MINUTES),
         "|reason=",g_v3109_c33_early_reason,
         "|primary=",V3109C21PrimaryLabel(g_v3109_m15_trend.primary),
         "|macro=",IntegerToString(g_v3109_c3_m15_macro_bias),
         "|close=",DoubleToString(in.close_price,_Digits),
         "|ema=",DoubleToString(in.ema20,_Digits),
         "|slope=",DoubleToString(in.ema_slope_atr,4),
         "|ref_high=",DoubleToString(in.reference_high,_Digits),
         "|ref_low=",DoubleToString(in.reference_low,_Digits),
         "|above4=",IntegerToString(in.above_ema_count4),
         "|below4=",IntegerToString(in.below_ema_count4));
}

bool V3109C33WarmStartM15Trend(const double spread_price,const double tick_size,
                               string &error)
{
   error="";
   if(g_v3109_c33_m15_replay_ready) return true;
   if(tick_size<=0.0)
   {
      error="invalid tick size";
      return false;
   }

   // Rebuild four trading days of causal M15 decisions.  Each replay point
   // receives only bars that were already closed at that point; shift 0 is
   // intentionally excluded because V310UpdateM15Context handles it next.
   const int replay_points=400;
   const int history_count=replay_points+V3109C21_PRIMARY_LOOKBACK;
   V310Bar history[];
   if(!V310LoadClosedBars(PERIOD_M15,history_count,history))
   {
      error="M15 replay history unavailable";
      return false;
   }

   V3109C33TrendReplayFrame frames[];
   ArrayResize(frames,replay_points);
   int frame_count=0;
   for(int shift=replay_points;shift>=1;shift--)
   {
      V310Bar metric_bars[];
      ArrayResize(metric_bars,V310_M15_LOOKBACK);
      for(int i=0;i<V310_M15_LOOKBACK;i++) metric_bars[i]=history[shift+i];

      V310Bar primary_bars[];
      ArrayResize(primary_bars,V3109C21_PRIMARY_LOOKBACK);
      for(int i=0;i<V3109C21_PRIMARY_LOOKBACK;i++)
         primary_bars[i]=history[shift+i];

      V310M15Metrics bull;
      V310M15BearMetrics bear;
      double impulse_low=0.0,impulse_high=0.0,resistance=0.0;
      double sell_impulse_high=0.0,sell_impulse_low=0.0,support=0.0;
      if(!V310DeriveM15Metrics(metric_bars,bull,impulse_low,impulse_high,resistance) ||
         !V3101DeriveM15BearMetrics(metric_bars,bear,sell_impulse_high,
                                    sell_impulse_low,support))
         continue;

      V3109C21PrimaryPivots pivots;
      ZeroMemory(pivots);
      V3109C21DeriveConfiguredPrimaryPivots(primary_bars,pivots);
      frames[frame_count].closed_bar_time=metric_bars[0].time;
      frames[frame_count].bull=bull;
      frames[frame_count].bear=bear;
      frames[frame_count].pivots=pivots;
      frame_count++;
   }

   if(frame_count<=0)
   {
      error="M15 replay produced no valid frames";
      return false;
   }
   ArrayResize(frames,frame_count);
   V3109M15TrendContext replay_trend;
   const int replayed=V3109C33ReplayTrendFrames(frames,spread_price,tick_size,
                                                g_v3109_m15_memory,replay_trend);
   if(replayed!=frame_count)
   {
      error="M15 replay frame count mismatch";
      return false;
   }
   g_v3109_c33_m15_replay_ready=true;
   Print("V3109_C33_M15_REPLAY|frames=",IntegerToString(replayed),
      "|last_time=",TimeToString(frames[frame_count-1].closed_bar_time,
                                  TIME_DATE|TIME_MINUTES),
      "|primary=",V3109C21PrimaryLabel(g_v3109_m15_memory.primary_memory.primary),
      "|phase=",V3109C21LocalPhaseLabel(g_v3109_m15_memory.primary_memory.local_phase));
   return true;
}

// EMA20 values for the three closed M5 bars preceding the current confirmation bar.
// bars[] is newest-first and contains only closed bars.
bool V3103RecentM5EMA20(const V310Bar &bars[],double &recent_ema[])
{
   if(ArraySize(bars)<23) return false;
   ArrayResize(recent_ema,3);
   for(int i=0;i<3;i++)
   {
      const int start=i+1;
      const int count=ArraySize(bars)-start;
      V310Bar history[];
      ArrayResize(history,count);
      for(int j=0;j<count;j++) history[j]=bars[start+j];
      recent_ema[i]=V310EMAFromNewest(history,20);
      if(recent_ema[i]<=0.0) return false;
   }
   return true;
}

// Route B evaluates its EMA direction and interaction over one full EMA20
// cycle.  bars[] is newest-first; each value is still computed from closed
// history only.
bool V3103M5EMA20Cycle(const V310Bar &bars[],double &cycle_ema[])
{
   if(ArraySize(bars)<V3103_EMA_CYCLE_BARS+20) return false;
   ArrayResize(cycle_ema,V3103_EMA_CYCLE_BARS);
   for(int i=0;i<V3103_EMA_CYCLE_BARS;i++)
   {
      const int start=i+1;
      const int count=ArraySize(bars)-start;
      V310Bar history[];
      ArrayResize(history,count);
      for(int j=0;j<count;j++) history[j]=bars[start+j];
      cycle_ema[i]=V310EMAFromNewest(history,20);
      if(cycle_ema[i]<=0.0) return false;
   }
   return true;
}

void V310AppendPullbackBar(const V310Bar &bar)
{
   const int old=ArraySize(g_v310_pullback_bars);
   const int next=MathMin(old+1,20);
   if(old<20) ArrayResize(g_v310_pullback_bars,next);
   for(int i=next-1;i>0;i--) g_v310_pullback_bars[i]=g_v310_pullback_bars[i-1];
   g_v310_pullback_bars[0]=bar;
}

bool V3101HasPendingDirection(const ENUM_TRADE_DIRECTION direction)
{
   for(int slot=0;slot<V3109C32_MAX_SLOTS;slot++)
   {
      if(!g_trade_slots[slot].HasManagedPendingOrders()) continue;
      const TradeRuntimeState state=g_trade_slots[slot].State();
      if(state.state==STATE_PENDING_ORDER && state.direction==direction) return true;
   }
   return false;
}

bool V3101CancelPendingDirection(const ENUM_TRADE_DIRECTION direction,string &error)
{
   error="";
   bool all_ok=true;
   for(int slot=0;slot<V3109C32_MAX_SLOTS;slot++)
   {
      const TradeRuntimeState state=g_trade_slots[slot].State();
      if(state.state!=STATE_PENDING_ORDER || state.direction!=direction) continue;
      string slot_error="";
      if(!g_trade_slots[slot].CancelPending(slot_error))
      {
         all_ok=false;
         if(error!="") error+=" | ";
         error+="slot="+IntegerToString(slot)+": "+slot_error;
      }
   }
   return all_ok;
}

void V3109C32CancelInvalidBuyPendings(const V310Bar &closed_bar,
                                      const double atr,const double tick_size)
{
   for(int slot=0;slot<V3109C32_MAX_SLOTS;slot++)
   {
      const TradeRuntimeState state=g_trade_slots[slot].State();
      const double signal_low=g_v3109_c32_pending_contexts[slot].signal_low;
      if(state.state!=STATE_PENDING_ORDER || state.direction!=DIR_BUY || signal_low<=0.0)
         continue;
      const bool signal_low_broken=(closed_bar.low<=signal_low-tick_size+1e-12);
      const bool strong_bear=V310Bearish(closed_bar) &&
         V310BodyRangeRatio(closed_bar)>=0.60 && atr>0.0 &&
         V310BarBody(closed_bar)/atr>=0.80 && V310CloseLocation(closed_bar)<=1.0/3.0 &&
         signal_low_broken;
      if(!signal_low_broken && !strong_bear) continue;
      string cancel_error="";
      if(!g_trade_slots[slot].CancelPending(cancel_error))
         Print("[V3.10.9 BUY撤单异常] slot=",slot," | ",cancel_error);
      else
         RecordObservationEvent("pending_canceled",state.signal_id,"order","closed",
            strong_bear ? "V3102_CANCEL_STRONG_BEAR_STRUCTURE" :
                          "V310_CANCEL_SIGNAL_LOW_BROKEN","slot="+IntegerToString(slot));
   }
}

void V3109C32CancelInvalidSellPendings(const V310Bar &closed_bar,
                                       const double atr,const double tick_size)
{
   for(int slot=0;slot<V3109C32_MAX_SLOTS;slot++)
   {
      const TradeRuntimeState state=g_trade_slots[slot].State();
      const double signal_high=g_v3109_c32_pending_contexts[slot].signal_high;
      if(state.state!=STATE_PENDING_ORDER || state.direction!=DIR_SELL || signal_high<=0.0)
         continue;
      const bool signal_high_broken=(closed_bar.high>=signal_high+tick_size-1e-12);
      const bool strong_bull=V310Bullish(closed_bar) &&
         V310BodyRangeRatio(closed_bar)>=0.60 && atr>0.0 &&
         V310BarBody(closed_bar)/atr>=0.80 && V310CloseLocation(closed_bar)>=2.0/3.0 &&
         signal_high_broken;
      if(!signal_high_broken && !strong_bull) continue;
      string cancel_error="";
      if(!g_trade_slots[slot].CancelPending(cancel_error))
         Print("[V3.10.9 SELL撤单异常] slot=",slot," | ",cancel_error);
      else
         RecordObservationEvent("pending_canceled",state.signal_id,"order","closed",
            strong_bull ? "V3102_CANCEL_STRONG_BULL_STRUCTURE" :
                          "V3102_CANCEL_SIGNAL_HIGH_BROKEN","slot="+IntegerToString(slot));
   }
}

// Do not re-arm a new closed-bar signal until the terminal confirms that this
// EA no longer has a managed pending order or position. This is intentionally
// based on live trade state, not a local cancellation request.
bool V3103OwnOrderStateIsClear()
{
   return !g_trades.HasManagedPendingOrders() &&
      !V3109C32HasManagedExposure();
}

void V3102ResetBuyCycleState(const string reason)
{
   V310InitPullbackState(g_v310_pullback);
   g_v310_impulse_confirmed=false;
   g_v310_candidate_ready=false;
   g_v310_candidate_route="";
   g_v3109_c3_buy_counter=false;
   g_v3109_c3_buy_attempt=0;
   g_v310_candidate_ai_reviewed=false;
   ResetAIDecision(g_v310_candidate_ai_decision);
   ZeroMemory(g_v310_candidate_plan);
   ZeroMemory(g_v310_candidate_signal);
   g_v310_candidate_signal_time=0;
   g_v310_impulse_low=0.0;
   g_v310_impulse_high=0.0;
   g_v310_candidate_atr=0.0;
   g_v310_candidate_resistance=0.0;
   ArrayResize(g_v310_pullback_bars,0);
   V3102ClearSideDiagnostics(g_v310_buy_diag);
   g_v310_buy_diag.reset_reason=reason;
}

void V3102ResetSellCycleState(const string reason)
{
   V3101InitBearPullbackState(g_v310_sell_pullback);
   g_v310_sell_impulse_confirmed=false;
   g_v310_sell_candidate_ready=false;
   g_v310_sell_candidate_route="";
   g_v3109_c3_sell_counter=false;
   g_v3109_c3_sell_attempt=0;
   g_v310_sell_candidate_ai_reviewed=false;
   ResetAIDecision(g_v310_sell_candidate_ai_decision);
   ZeroMemory(g_v310_sell_candidate_plan);
   ZeroMemory(g_v310_sell_candidate_signal);
   ZeroMemory(g_v310_sell_candidate_location);
   g_v310_sell_candidate_signal_time=0;
   g_v310_sell_impulse_high=0.0;
   g_v310_sell_impulse_low=0.0;
   g_v310_sell_candidate_atr=0.0;
   g_v310_sell_candidate_support=0.0;
   ArrayResize(g_v310_sell_pullback_bars,0);
   V3102ClearSideDiagnostics(g_v310_sell_diag);
   g_v310_sell_diag.reset_reason=reason;
}

void V310UpdateM15Context()
{
   V310Bar bars[];
   if(!V310LoadClosedBars(PERIOD_M15,V310_M15_LOOKBACK,bars)) return;
   V310Bar primary_bars[];
   if(!V310LoadClosedBars(PERIOD_M15,V3109C21_PRIMARY_LOOKBACK,primary_bars)) return;
   V310Bar macro_bars[];
   if(!V310LoadClosedBars(PERIOD_M15,V310_C3_MACRO_LOOKBACK,macro_bars)) return;
   if(!V310DeriveM15Metrics(bars,g_v310_m15_metrics,g_v310_impulse_low,g_v310_impulse_high,g_v310_nearest_resistance)) return;
   ArrayCopy(g_v310_m15_target_bars,bars);
   if(!V3101DeriveM15BearMetrics(bars,g_v310_sell_m15_metrics,g_v310_sell_impulse_high,g_v310_sell_impulse_low,g_v310_sell_nearest_support)) return;
   ArrayCopy(g_v310_sell_m15_target_bars,bars);
   V3109C21PrimaryPivots primary_pivots;
   V3109C21DeriveConfiguredPrimaryPivots(primary_bars,primary_pivots);

   const double current_spread=MathMax(0.0,SymbolInfoDouble(_Symbol,SYMBOL_ASK)-SymbolInfoDouble(_Symbol,SYMBOL_BID));
   const double trade_tick_size=SymbolInfoDouble(_Symbol,SYMBOL_TRADE_TICK_SIZE);
   if(!g_v3109_c33_m15_replay_ready)
   {
      string replay_error="";
      if(!V3109C33WarmStartM15Trend(current_spread,trade_tick_size,replay_error))
         Print("V3109_C33_M15_REPLAY_FAILED|",replay_error);
   }

   // Candidate C2.1: primary structure owns direction; local pullback phase is
   // diagnostic and cannot authorize an opposite-side M5 scan.
   const V3109M15TrendState previous_state=g_v3109_m15_memory.state;
   const V3109C21PrimaryDirection previous_primary=g_v3109_m15_memory.primary_memory.primary;
   const V3109C21LocalPhase previous_phase=g_v3109_m15_memory.primary_memory.local_phase;
   const int previous_macro_bias=g_v3109_c3_m15_macro_bias;
   V3109AdvanceM15Trend(g_v310_m15_metrics,g_v310_sell_m15_metrics,primary_pivots,bars[0].time,
                        current_spread,trade_tick_size,g_v3109_m15_memory,g_v3109_m15_trend);
   V3109ApplyTrendToContexts(g_v3109_m15_trend,g_v310_m15_context,g_v310_sell_m15_context);
   g_v3109_c3_m15_macro_bias=V3109C3M15MacroBias(macro_bars);
   V3109C33ApplyEarlyTrendGate(bars,trade_tick_size);
   const V3109C21PrimaryDirection previous_quota_primary=(previous_macro_bias>0 ? PRIMARY_BULL :
      (previous_macro_bias<0 ? PRIMARY_BEAR : PRIMARY_RANGE));
   const V3109C21PrimaryDirection current_quota_primary=(g_v3109_c3_m15_macro_bias>0 ? PRIMARY_BULL :
      (g_v3109_c3_m15_macro_bias<0 ? PRIMARY_BEAR : PRIMARY_RANGE));
   V3109C3ResetQuotaOnPrimaryChange(previous_quota_primary,current_quota_primary,
                                    bars[0].time,g_v3109_c3_quota);

   // C3.3-TF1 is a pure trend module.  Opposite-primary tactical entries and
   // exhaustion counters remain disabled even when the historical 3:1 quota
   // would have allowed one.
   g_v3109_c3_buy_tactical_counter=false;
   g_v3109_c3_sell_tactical_counter=false;
   const bool counter_quota_available=false;
   if(g_v3109_c3_m15_macro_bias<0)
      g_v310_m15_context.allow_m5_scan=(g_v310_m15_context.allow_m5_scan &&
                                        g_v3109_c3_buy_tactical_counter && counter_quota_available);
   if(g_v3109_c3_m15_macro_bias>0)
      g_v310_sell_m15_context.allow_m5_scan=(g_v310_sell_m15_context.allow_m5_scan &&
                                             g_v3109_c3_sell_tactical_counter && counter_quota_available);
   if(previous_phase!=LOCAL_EXHAUSTED && g_v3109_m15_trend.local_phase==LOCAL_EXHAUSTED)
      V3109C3BeginExhaustion(g_v3109_c3_quota,bars[0].time);
   if(InpV310LongOnly)
      g_v310_sell_m15_context.allow_m5_scan=false;

   if(InpDebugMode)
      Print("V3109_C3_M15_STATE|time=",TimeToString(bars[0].time,TIME_DATE|TIME_MINUTES),
         "|previous_state=",V3109M15StateLabel(previous_state),
         "|state=",V3109M15StateLabel(g_v3109_m15_trend.state),
         "|previous_primary=",V3109C21PrimaryLabel(previous_primary),
         "|primary=",V3109C21PrimaryLabel(g_v3109_m15_trend.primary),
         "|previous_phase=",V3109C21LocalPhaseLabel(previous_phase),
         "|phase=",V3109C21LocalPhaseLabel(g_v3109_m15_trend.local_phase),
         "|state_started_at=",TimeToString(g_v3109_m15_memory.state_started_at,TIME_DATE|TIME_MINUTES),
         "|transition_started_at=",TimeToString(g_v3109_m15_memory.primary_memory.transition_started_at,TIME_DATE|TIME_MINUTES),
         "|bull_protected_low=",DoubleToString(g_v3109_m15_memory.primary_memory.protected_low,_Digits),
         "|bear_protected_high=",DoubleToString(g_v3109_m15_memory.primary_memory.protected_high,_Digits),
         "|dominant_high=",DoubleToString(g_v3109_m15_memory.primary_memory.dominant_high,_Digits),
         "|dominant_low=",DoubleToString(g_v3109_m15_memory.primary_memory.dominant_low,_Digits),
         "|candidate_low=",DoubleToString(g_v3109_m15_memory.primary_memory.candidate_low,_Digits),
         "|candidate_high=",DoubleToString(g_v3109_m15_memory.primary_memory.candidate_high,_Digits),
         "|primary_latest_high=",DoubleToString(primary_pivots.latest_high,_Digits),
         "|primary_latest_low=",DoubleToString(primary_pivots.latest_low,_Digits),
         "|break_count=",IntegerToString(g_v3109_m15_memory.primary_memory.break_close_count),
         "|promotion_buffer=",DoubleToString(g_v3109_m15_trend.promotion_buffer,_Digits),
         "|break_buffer=",DoubleToString(g_v3109_m15_trend.break_buffer,_Digits),
         "|bull_latest_high=",DoubleToString(g_v310_m15_metrics.latest_confirmed_high,_Digits),
         "|bull_latest_low=",DoubleToString(g_v310_m15_metrics.latest_confirmed_low,_Digits),
         "|bear_latest_high=",DoubleToString(g_v310_sell_m15_metrics.latest_confirmed_high,_Digits),
         "|bear_latest_low=",DoubleToString(g_v310_sell_m15_metrics.latest_confirmed_low,_Digits),
         "|bull_break=",(g_v310_m15_metrics.protected_swing_low_broken_m15?"true":"false"),
         "|bear_break=",(g_v310_sell_m15_metrics.protected_swing_high_broken_m15?"true":"false"),
         "|close=",DoubleToString(g_v310_m15_metrics.closed_bar_close,_Digits),
         "|ema=",DoubleToString(g_v310_m15_metrics.ema20,_Digits),
         "|atr=",DoubleToString(g_v310_m15_metrics.atr14,_Digits),
         "|allow_buy=",(g_v310_m15_context.allow_m5_scan?"true":"false"),
         "|allow_sell=",(g_v310_sell_m15_context.allow_m5_scan?"true":"false"),
         "|aligned_fills=",IntegerToString(g_v3109_c3_quota.aligned_fills),
         "|counter_fills=",IntegerToString(g_v3109_c3_quota.counter_fills),
         "|counter_quota=",IntegerToString(V3109C3CounterQuota(g_v3109_c3_quota.aligned_fills)),
         "|macro_bias=",IntegerToString(g_v3109_c3_m15_macro_bias),
         "|early_reason=",g_v3109_c33_early_reason,
         "|primary_reason=",g_v3109_m15_trend.primary_reason,
         "|buy_reason=",g_v310_m15_context.reason_code,
         "|sell_reason=",g_v310_sell_m15_context.reason_code);

   // Candidate B: a temporary M15 pause (LATE/RANGE) must not wipe the M5
   // pullback lifecycle. Reset only when the direction is genuinely opposite
   // or the protected structure broke (TRANSITION). This lets the H2/L2 second
   // attempt survive short M15 churn and finally develop into a trade.
   const V3109M15TrendState m15_state=g_v3109_m15_trend.state;
   const bool buy_counter_context=false;
   const bool sell_counter_context=false;
   const bool buy_opposite=(m15_state==M15_BEAR || m15_state==M15_EARLY_BEAR || m15_state==M15_LATE_BEAR);
   const bool buy_broken=(m15_state==M15_TRANSITION);
   if(buy_opposite || buy_broken)
   {
      if(!buy_counter_context || !g_v3109_c3_buy_counter)
         V3102ResetBuyCycleState("M15_DIRECTION_INVALID");
      const bool preserve_counter_pending=(buy_counter_context && g_v3109_c3_pending_counter &&
                                           g_v3109_c3_pending_direction==DIR_BUY);
      if(V3101HasPendingDirection(DIR_BUY) && !preserve_counter_pending)
      {
         string error="";
         if(!V3101CancelPendingDirection(DIR_BUY,error)) Print("[V3.10.9 BUY M15撤单异常] ",error);
      }
   }
   else if(!g_v310_m15_context.allow_m5_scan)
   {
      // LATE_BULL / RANGE: block new entries and drop stale pending orders,
      // but keep the pullback lifecycle so it resumes when the trend re-arms.
      g_v310_candidate_ready=false;
      if(V3101HasPendingDirection(DIR_BUY))
      {
         string error="";
         if(!V3101CancelPendingDirection(DIR_BUY,error)) Print("[V3.10.9 BUY M15暂停撤单] ",error);
      }
   }

   const bool sell_opposite=(m15_state==M15_BULL || m15_state==M15_EARLY_BULL || m15_state==M15_LATE_BULL);
   const bool sell_broken=(m15_state==M15_TRANSITION);
   if(!InpV310LongOnly && (sell_opposite || sell_broken))
   {
      if(!sell_counter_context || !g_v3109_c3_sell_counter)
         V3102ResetSellCycleState("M15_DIRECTION_INVALID");
      const bool preserve_counter_pending=(sell_counter_context && g_v3109_c3_pending_counter &&
                                           g_v3109_c3_pending_direction==DIR_SELL);
      if(V3101HasPendingDirection(DIR_SELL) && !preserve_counter_pending)
      {
         string error="";
         if(!V3101CancelPendingDirection(DIR_SELL,error)) Print("[V3.10.9 SELL M15撤单异常] ",error);
      }
   }
   else if(!InpV310LongOnly && !g_v310_sell_m15_context.allow_m5_scan)
   {
      g_v310_sell_candidate_ready=false;
      if(V3101HasPendingDirection(DIR_SELL))
      {
         string error="";
         if(!V3101CancelPendingDirection(DIR_SELL,error)) Print("[V3.10.9 SELL M15暂停撤单] ",error);
      }
   }
}

void V3101UpdateM15BearContext()
{
   // Unified M15 state is computed once in V310UpdateM15Context(). This entry
   // point is retained only as a LongOnly safety stub.
   if(InpV310LongOnly)
   {
      g_v310_sell_candidate_ready=false;
      g_v310_sell_m15_context.allow_m5_scan=false;
      return;
   }
}

// Route B is intentionally evaluated before the legacy impulse/pullback early returns.
// It shares the downstream plan and execution lifecycle, but its setup evidence is only
// M15 context + closed M5 EMA pullback/recovery price action.
bool V3103TryIndependentBullEMARecovery(const V310Bar &bars[],const double atr,const double tick_size)
{
   // C3 bootstrap only: a real EMA pullback may create a tracked M5 cycle when
   // the stricter impulse detector has not already created one.  Once active,
   // all later bars are handled by the ordinary Attempt 1/2 state machine.
   if(g_v310_candidate_ready)
      return false;

   V310Bar cycle_closed[]; ArrayResize(cycle_closed,V3103_EMA_CYCLE_BARS);
   for(int i=0;i<V3103_EMA_CYCLE_BARS;i++) cycle_closed[i]=bars[i+1];
   double cycle_ema[];
   const bool ema_ready=V3103M5EMA20Cycle(bars,cycle_ema);
   const bool ema_rising=ema_ready && V3103BullEMAOverallDirection(cycle_ema);
   const bool real_pullback=ema_ready && V3103HasCycleBullEMAPullback(cycle_closed,cycle_ema,InpV310LocationToleranceATR*atr);
   const bool ema_touch=ema_ready && V3103HasCycleEMATouch(cycle_closed,cycle_ema,InpV310LocationToleranceATR*atr);
   const bool recovery_candle=(bars[0].close>bars[0].open && V310BarRange(bars[0])>0.0 && V310BarBody(bars[0])+1e-12>=tick_size);
   const bool prior_break=(bars[0].close+1e-12>=bars[1].high+tick_size);
   const bool close_location=(V310CloseLocation(bars[0])+1e-12>=0.75);
   const bool recovery_quality=V3109C31BullRecoveryQuality(bars[0],bars[1],atr,tick_size);
   const bool ema_recovery=ema_ready && recovery_quality && V3103BullEMARecovery(g_v310_m15_context.allow_m5_scan,
      g_v310_m15_context.hard_exhaustion,g_v310_m15_metrics.protected_swing_low_intact,
      ema_rising,real_pullback,cycle_closed,cycle_ema,bars[0],bars[1],
      InpV310LocationToleranceATR*atr,tick_size);
   if(InpDebugMode)
      Print("V3103_M5_ROUTE|dir=BUY|time=",TimeToString(bars[0].time,TIME_DATE|TIME_MINUTES),
         "|legacy_pullback=false|independent_pullback=",(real_pullback?"true":"false"),
         "|ema_dir=",(ema_rising?"true":"false"),"|ema_touch=",(ema_touch?"true":"false"),
         "|candle=",(recovery_candle?"true":"false"),"|quality=",(recovery_quality?"true":"false"),
         "|break=",(prior_break?"true":"false"),"|close75=",(close_location?"true":"false"),
         "|ema=",(ema_recovery?"true":"false"),"|route=",IntegerToString(ema_recovery ? (int)V3103_ROUTE_EMA_RECOVERY : (int)V3103_ROUTE_NONE));
   if(!ema_recovery)
      return false;

   const datetime target_setup_anchor=V3103BullEMAPullbackStart(cycle_closed,cycle_ema,InpV310LocationToleranceATR*atr);
   if(g_v310_pullback.active && !g_v310_pullback.locked)
      return false;
   if(g_v310_pullback.active && g_v310_pullback.locked &&
      target_setup_anchor==g_v310_pullback.pullback_start_time)
      return false;
   string signal_key="";
   string slot_reason="";
   if(target_setup_anchor<=0 ||
      !V3109C32PrepareCandidateCycle(DIR_BUY,target_setup_anchor,1,bars[0].time,
                                     signal_key,slot_reason) ||
      !V3109C3CanEmitAttempt(true,false,1,signal_key,
         g_v3109_c32_micro_cycles[0].consumed_setup_key))
      return false;

   V310InitPullbackState(g_v310_pullback);
   g_v310_pullback.active=true;
   g_v310_pullback.pullback_id="EMA-PB-"+IntegerToString((int)target_setup_anchor);
   g_v310_pullback.pullback_start_time=target_setup_anchor;
   g_v310_pullback.pullback_start_high=MathMax(bars[0].high,bars[1].high);
   g_v310_pullback.attempt_count=1;
   g_v310_pullback.h_state="H1";
   g_v310_pullback.attempt_active=true;
   g_v310_pullback.down_leg_active=false;
   g_v310_pullback.current_attempt_high=bars[0].high;
   ArrayResize(g_v310_pullback_bars,0);
   V310AppendPullbackBar(bars[0]);

   V310SignalResult signal; ZeroMemory(signal); signal.valid=true; signal.signal_type="EMA_RECOVERY_BUY";
   V310SignalResult location; ZeroMemory(location); location.valid=true; location.location_ema20=true; location.m2b=true;
   const double ask=SymbolInfoDouble(_Symbol,SYMBOL_ASK);
   const double min_distance=MathMax((double)SymbolInfoInteger(_Symbol,SYMBOL_TRADE_STOPS_LEVEL),(double)SymbolInfoInteger(_Symbol,SYMBOL_TRADE_FREEZE_LEVEL))*_Point;
   const double resistance=V3103NearestValidBuyResistance(bars,g_v310_m15_target_bars,bars[0].high+tick_size,tick_size,target_setup_anchor,true);
   if(InpDebugMode)
      Print("V3103_TARGETSPACE|dir=BUY|route=EMA_RECOVERY|signal=",TimeToString(bars[0].time,TIME_DATE|TIME_MINUTES),
         "|anchor=",TimeToString(target_setup_anchor,TIME_DATE|TIME_MINUTES),"|exclude_internal=true|target=",DoubleToString(resistance,_Digits));
   g_v310_buy_diag.signal_valid=true; g_v310_buy_diag.location_valid=true; g_v310_buy_diag.plan_evaluated=true;
   V3107StructureStopSelection stop_selection;
   V3107SelectStructureStop(bars,false,stop_selection);
   V310BuildTradePlan(bars[0],stop_selection.anchor,tick_size,min_distance,ask,resistance,g_v310_candidate_plan);
   if(g_v310_candidate_plan.valid &&
      V3109C31HasImmediateBullObstacle(bars[0],bars[1],bars[2],
         g_v310_candidate_plan.entry,g_v310_candidate_plan.planned_r))
   {
      g_v310_candidate_plan.valid=false;
      g_v310_candidate_plan.reason_code="PLAN_REJECT_IMMEDIATE_M5_RESISTANCE_LT_1R";
   }
   if(InpDebugMode)
      Print("V3103_PLAN_GATE|dir=BUY|route=EMA_RECOVERY|entry=",DoubleToString(g_v310_candidate_plan.entry,_Digits),
         "|sl=",DoubleToString(g_v310_candidate_plan.final_stop,_Digits),"|r=",DoubleToString(g_v310_candidate_plan.planned_r,_Digits),
         "|stop_anchor=",DoubleToString(stop_selection.anchor,_Digits),"|stop_prev=",DoubleToString(stop_selection.previous_extreme,_Digits),
         "|stop_structure=",DoubleToString(stop_selection.structure_extreme,_Digits),"|stop_source=",stop_selection.source,
         "|ask=",DoubleToString(ask,_Digits),"|broker_min=",DoubleToString(min_distance,_Digits),
         "|target=",DoubleToString(resistance,_Digits),"|reason=",g_v310_candidate_plan.reason_code);
   g_v310_buy_diag.plan_valid=g_v310_candidate_plan.valid; g_v310_buy_diag.plan_reason=g_v310_candidate_plan.reason_code;
   if(!g_v310_candidate_plan.valid)
      return true;
   g_v310_candidate_signal=signal;
   g_v310_candidate_signal.location_ema20=true; g_v310_candidate_signal.m2b=true;
   g_v310_candidate_signal_time=bars[0].time; g_v310_candidate_bar=bars[0];
   g_v310_candidate_atr=atr; g_v310_candidate_resistance=resistance;
   g_v310_candidate_ai_reviewed=false; ResetAIDecision(g_v310_candidate_ai_decision);
   g_v310_candidate_route=signal.signal_type; g_v310_emitted_setup_key=signal_key;
   g_v3109_c3_buy_counter=g_v3109_c3_buy_tactical_counter;
   g_v3109_c3_buy_attempt=1; g_v310_candidate_ready=true;
   Print("[V3.10.7 BUY Candidate] Route=",g_v310_candidate_route," | Time=",TimeToString(bars[0].time,TIME_DATE|TIME_MINUTES)," | Entry=",DoubleToString(g_v310_candidate_plan.entry,_Digits)," | FinalStop=",DoubleToString(g_v310_candidate_plan.final_stop,_Digits));
   return true;
}

bool V3109C33TryIndependentBullCompressionBreak(const V310Bar &bars[],
                                                const double atr,const double tick_size)
{
   if(g_v310_candidate_ready || (g_v310_pullback.active && !g_v310_pullback.locked))
      return false;

   V310Bar recent_closed[];
   ArrayResize(recent_closed,3);
   for(int i=0;i<3;i++) recent_closed[i]=bars[i+1];
   double recent_ema[];
   const bool ema_ready=V3103RecentM5EMA20(bars,recent_ema);
   const bool ema_rising=ema_ready && V310EMAFromNewest(bars,20)>recent_ema[0];
   V310RangeLikeResult compression;
   V310EvaluateRangeLike(recent_closed,3,compression);
   const bool route_ready=ema_ready && V3103BullCompressionBreak(
      g_v310_m15_context.allow_m5_scan,g_v310_m15_context.hard_exhaustion,
      g_v310_m15_metrics.protected_swing_low_intact,ema_rising,
      compression.is_range_like,recent_closed,recent_ema,bars[0],
      InpV310LocationToleranceATR*atr,tick_size);
   if(!route_ready) return false;

   const datetime target_setup_anchor=V3103RecentEMAInteractionStart(
      recent_closed,recent_ema,InpV310LocationToleranceATR*atr);
   if(g_v310_pullback.active && g_v310_pullback.locked &&
      target_setup_anchor==g_v310_pullback.pullback_start_time)
      return false;

   string signal_key="";
   string slot_reason="";
   if(target_setup_anchor<=0 ||
      !V3109C32PrepareCandidateCycle(DIR_BUY,target_setup_anchor,1,bars[0].time,
                                     signal_key,slot_reason) ||
      !V3109C3CanEmitAttempt(true,false,1,signal_key,
         g_v3109_c32_micro_cycles[0].consumed_setup_key))
      return false;

   V310InitPullbackState(g_v310_pullback);
   g_v310_pullback.active=true;
   g_v310_pullback.pullback_id="COMP-PB-"+IntegerToString((int)target_setup_anchor);
   g_v310_pullback.pullback_start_time=target_setup_anchor;
   g_v310_pullback.pullback_start_high=MathMax(bars[0].high,bars[1].high);
   g_v310_pullback.attempt_count=1;
   g_v310_pullback.h_state="H1";
   g_v310_pullback.attempt_active=true;
   g_v310_pullback.down_leg_active=false;
   g_v310_pullback.current_attempt_high=bars[0].high;
   ArrayResize(g_v310_pullback_bars,0);
   V310AppendPullbackBar(bars[0]);

   V310SignalResult signal;
   ZeroMemory(signal);
   signal.valid=true;
   signal.signal_type="EMA_COMPRESSION_BREAK_BUY";
   signal.location_ema20=true;
   signal.m2b=true;
   const double ask=SymbolInfoDouble(_Symbol,SYMBOL_ASK);
   const double min_distance=MathMax((double)SymbolInfoInteger(_Symbol,SYMBOL_TRADE_STOPS_LEVEL),
      (double)SymbolInfoInteger(_Symbol,SYMBOL_TRADE_FREEZE_LEVEL))*_Point;
   const double resistance=V3103NearestValidBuyResistance(bars,g_v310_m15_target_bars,
      bars[0].high+tick_size,tick_size,target_setup_anchor,true);
   g_v310_buy_diag.signal_valid=true;
   g_v310_buy_diag.location_valid=true;
   g_v310_buy_diag.plan_evaluated=true;
   V3107StructureStopSelection stop_selection;
   V3107SelectStructureStop(bars,false,stop_selection);
   V310BuildTradePlan(bars[0],stop_selection.anchor,tick_size,min_distance,ask,
                      resistance,g_v310_candidate_plan);
   g_v310_buy_diag.plan_valid=g_v310_candidate_plan.valid;
   g_v310_buy_diag.plan_reason=g_v310_candidate_plan.reason_code;
   if(!g_v310_candidate_plan.valid) return true;

   g_v310_candidate_signal=signal;
   g_v310_candidate_signal_time=bars[0].time;
   g_v310_candidate_bar=bars[0];
   g_v310_candidate_atr=atr;
   g_v310_candidate_resistance=resistance;
   g_v310_candidate_ai_reviewed=false;
   ResetAIDecision(g_v310_candidate_ai_decision);
   g_v310_candidate_route=signal.signal_type;
   g_v310_emitted_setup_key=signal_key;
   g_v3109_c3_buy_counter=false;
   g_v3109_c3_buy_attempt=1;
   g_v310_candidate_ready=true;
   Print("V3109_C33_FREQUENCY_CANDIDATE|dir=BUY|route=EMA_COMPRESSION_BREAK_BUY|time=",
      TimeToString(bars[0].time,TIME_DATE|TIME_MINUTES),
      "|entry=",DoubleToString(g_v310_candidate_plan.entry,_Digits),
      "|sl=",DoubleToString(g_v310_candidate_plan.final_stop,_Digits),
      "|early=",(g_v3109_c33_early_buy?"true":"false"));
   return true;
}

bool V3109C3TryCounterBuy(const V310Bar &bars[],const double atr,const double tick_size)
{
   if(!V3109C3CanCounterFill(g_v3109_m15_trend.primary,g_v3109_m15_trend.local_phase,
      g_v3109_c3_quota.aligned_fills,g_v3109_c3_quota.counter_fills,
      g_v3109_c3_quota.counter_filled_in_exhaustion))
      return false;

   V3109C3CounterSignal counter;
   if(!V3109C3CounterBuyStructure(bars,g_v3109_m15_trend.primary,g_v3109_m15_trend.local_phase,
      atr,g_v310_m15_metrics.atr14,g_v3109_m15_memory.primary_memory.dominant_low,
      tick_size,counter))
      return false;

   string signal_key="";
   string slot_reason="";
   if(!V3109C32PrepareCandidateCycle(DIR_BUY,
      g_v3109_c3_quota.exhaustion_started_at,1,bars[0].time,signal_key,slot_reason))
      return false;

   V310SignalResult signal; ZeroMemory(signal);
   signal.valid=true; signal.strong_bull=true; signal.signal_type="COUNTERTREND_BUY";
   const double ask=SymbolInfoDouble(_Symbol,SYMBOL_ASK);
   const double min_distance=MathMax((double)SymbolInfoInteger(_Symbol,SYMBOL_TRADE_STOPS_LEVEL),
      (double)SymbolInfoInteger(_Symbol,SYMBOL_TRADE_FREEZE_LEVEL))*_Point;
   const double resistance=V3103NearestValidBuyResistance(bars,g_v310_m15_target_bars,
      bars[0].high+tick_size,tick_size,0,false);
   V3107StructureStopSelection stop_selection;
   if(!V3107SelectStructureStop(bars,false,stop_selection)) return false;
   V310BuildTradePlan(bars[0],stop_selection.anchor,tick_size,min_distance,ask,resistance,
                      g_v310_candidate_plan);
   g_v310_buy_diag.signal_valid=true;
   g_v310_buy_diag.location_valid=true;
   g_v310_buy_diag.plan_evaluated=true;
   g_v310_buy_diag.plan_valid=g_v310_candidate_plan.valid;
   g_v310_buy_diag.plan_reason=g_v310_candidate_plan.reason_code;
   if(!g_v310_candidate_plan.valid) return false;

   g_v310_candidate_signal=signal;
   g_v310_candidate_signal_time=bars[0].time;
   g_v310_candidate_bar=bars[0];
   g_v310_candidate_atr=atr;
   g_v310_candidate_resistance=resistance;
   g_v310_candidate_ai_reviewed=false; ResetAIDecision(g_v310_candidate_ai_decision);
   g_v310_candidate_route=signal.signal_type;
   g_v310_emitted_setup_key=signal_key;
   g_v3109_c3_buy_counter=true;
   g_v3109_c3_buy_attempt=0;
   g_v310_candidate_ready=true;
   Print("V3109_C3_COUNTER_CANDIDATE|dir=BUY|time=",TimeToString(bars[0].time,TIME_DATE|TIME_MINUTES),
      "|aligned=",IntegerToString(g_v3109_c3_quota.aligned_fills),
      "|counter=",IntegerToString(g_v3109_c3_quota.counter_fills),
      "|entry=",DoubleToString(g_v310_candidate_plan.entry,_Digits),
      "|sl=",DoubleToString(g_v310_candidate_plan.final_stop,_Digits));
   return true;
}

bool V3109C3TryCounterSell(const V310Bar &bars[],const double atr,const double tick_size)
{
   if(InpV310LongOnly || !V3109C3CanCounterFill(g_v3109_m15_trend.primary,
      g_v3109_m15_trend.local_phase,g_v3109_c3_quota.aligned_fills,
      g_v3109_c3_quota.counter_fills,g_v3109_c3_quota.counter_filled_in_exhaustion))
      return false;

   V3109C3CounterSignal counter;
   if(!V3109C3CounterSellStructure(bars,g_v3109_m15_trend.primary,g_v3109_m15_trend.local_phase,
      atr,g_v310_m15_metrics.atr14,g_v3109_m15_memory.primary_memory.dominant_high,
      tick_size,counter))
      return false;

   string signal_key="";
   string slot_reason="";
   if(!V3109C32PrepareCandidateCycle(DIR_SELL,
      g_v3109_c3_quota.exhaustion_started_at,1,bars[0].time,signal_key,slot_reason))
      return false;

   V310BearSignalResult signal; ZeroMemory(signal);
   signal.valid=true; signal.strong_bear=true; signal.signal_type="COUNTERTREND_SELL";
   V310SignalResult location; ZeroMemory(location); location.valid=true;
   const double bid=SymbolInfoDouble(_Symbol,SYMBOL_BID);
   const double min_distance=MathMax((double)SymbolInfoInteger(_Symbol,SYMBOL_TRADE_STOPS_LEVEL),
      (double)SymbolInfoInteger(_Symbol,SYMBOL_TRADE_FREEZE_LEVEL))*_Point;
   const double support=V3103NearestValidSellSupport(bars,g_v310_sell_m15_target_bars,
      bars[0].low-tick_size,tick_size,0,false);
   V3107StructureStopSelection stop_selection;
   if(!V3107SelectStructureStop(bars,true,stop_selection)) return false;
   V3101BuildSellTradePlan(bars[0],stop_selection.anchor,tick_size,min_distance,bid,support,
                           g_v310_sell_candidate_plan);
   g_v310_sell_diag.signal_valid=true;
   g_v310_sell_diag.location_valid=true;
   g_v310_sell_diag.plan_evaluated=true;
   g_v310_sell_diag.plan_valid=g_v310_sell_candidate_plan.valid;
   g_v310_sell_diag.plan_reason=g_v310_sell_candidate_plan.reason_code;
   if(!g_v310_sell_candidate_plan.valid) return false;

   g_v310_sell_candidate_signal=signal;
   g_v310_sell_candidate_location=location;
   g_v310_sell_candidate_signal_time=bars[0].time;
   g_v310_sell_candidate_bar=bars[0];
   g_v310_sell_candidate_atr=atr;
   g_v310_sell_candidate_support=support;
   g_v310_sell_candidate_ai_reviewed=false; ResetAIDecision(g_v310_sell_candidate_ai_decision);
   g_v310_sell_candidate_route=signal.signal_type;
   g_v310_sell_emitted_setup_key=signal_key;
   g_v3109_c3_sell_counter=true;
   g_v3109_c3_sell_attempt=0;
   g_v310_sell_candidate_ready=true;
   Print("V3109_C3_COUNTER_CANDIDATE|dir=SELL|time=",TimeToString(bars[0].time,TIME_DATE|TIME_MINUTES),
      "|aligned=",IntegerToString(g_v3109_c3_quota.aligned_fills),
      "|counter=",IntegerToString(g_v3109_c3_quota.counter_fills),
      "|entry=",DoubleToString(g_v310_sell_candidate_plan.entry,_Digits),
      "|sl=",DoubleToString(g_v310_sell_candidate_plan.final_stop,_Digits));
   return true;
}

void V310UpdateM5State()
{
   if(!g_v310_m15_context.allow_m5_scan) return;
   V3102ClearSideDiagnostics(g_v310_buy_diag);
   V310Bar bars[]; if(!V310LoadClosedBars(PERIOD_M5,60,bars)) return;
   double tick_size=SymbolInfoDouble(_Symbol,SYMBOL_TRADE_TICK_SIZE); if(tick_size<=0.0) tick_size=_Point;
   const double atr=V310ATRFromNewest(bars,14);
   g_v3109_c3_buy_counter=false;
   V3109C32CancelInvalidBuyPendings(bars[0],atr,tick_size);
   if(V3103TryIndependentBullEMARecovery(bars,atr,tick_size))
      return;
   if(V3109C33TryIndependentBullCompressionBreak(bars,atr,tick_size))
      return;
   if(g_v310_pullback.active && V310ConfirmBullImpulse(bars[1],bars[0],g_v310_pullback.pullback_start_high,atr,tick_size))
   {
      V310AdvancePullbackState(g_v310_pullback,"NEW_IMPULSE",bars[0].time);
      g_v310_impulse_confirmed=true; g_v310_impulse_high=MathMax(bars[1].high,bars[0].high);
      ArrayResize(g_v310_pullback_bars,0); g_v310_candidate_ready=false; return;
   }
   if(!g_v310_pullback.active && !g_v310_impulse_confirmed)
   {
      double prior_high=bars[2].high; for(int i=3;i<12;i++) prior_high=MathMax(prior_high,bars[i].high);
      if(bars[1].close>=prior_high+MathMax(tick_size,InpV310ImpulseBufferATR*atr) &&
         V310BodyRangeRatio(bars[1])>=0.60 && V310CloseLocation(bars[1])>=0.75 && bars[0].close>prior_high)
      { g_v310_impulse_confirmed=true; g_v310_impulse_high=MathMax(bars[1].high,bars[0].high); }
   }
   if(!g_v310_pullback.active && g_v310_impulse_confirmed && V310IsPullbackStart(bars[1],bars[0],tick_size))
   {
      V310AdvancePullbackState(g_v310_pullback,"PULLBACK_START",bars[0].time,MathMax(g_v310_impulse_high,bars[0].high));
      ArrayResize(g_v310_pullback_bars,0); V310AppendPullbackBar(bars[0]); return;
   }
   if(!g_v310_pullback.active || g_v310_pullback.locked) return;
   V310AppendPullbackBar(bars[0]);
   bool range_like=false;
   if(ArraySize(g_v310_pullback_bars)>=InpV310M5RangeMinBars)
   {
      V310RangeLikeResult range_result; V310EvaluateRangeLike(g_v310_pullback_bars,InpV310M5RangeMinBars,range_result);
      range_like=range_result.is_range_like;
   }
   V310Bar previous_three[]; ArrayResize(previous_three,3);
   for(int i=0;i<3;i++) previous_three[i]=bars[i+1];
   V310SignalResult signal; V310ClassifyBullSignal(bars[0],previous_three,signal);
   g_v310_buy_diag.signal_valid=signal.valid;
   V310LocationLevels levels;
   const double impulse_range=MathMax(0.0,g_v310_impulse_high-g_v310_impulse_low);
   levels.fib236=g_v310_impulse_high-0.236*impulse_range;
   levels.fib382=g_v310_impulse_high-0.382*impulse_range;
   levels.ema20=V310EMAFromNewest(bars,20);
   levels.breakout_retest=g_v310_impulse_high;
   V310SignalResult location; V310EvaluateLocationBar(bars[0],atr,levels,location,InpV310LocationToleranceATR);
   g_v310_buy_diag.location_valid=location.valid;
   V310AdvanceFromClosedBar(g_v310_pullback,bars[1],bars[0],tick_size,signal.valid);
   if(g_v310_pullback.locked && V3101HasPendingDirection(DIR_BUY))
   {
      string cancel_error="";
      if(!V3101CancelPendingDirection(DIR_BUY,cancel_error)) Print("[V3.10.7 BUY H3/Range撤单异常] ",cancel_error);
      g_v310_candidate_ready=false;
      return;
   }
   V310Bar recent_closed[]; ArrayResize(recent_closed,3);
   for(int i=0;i<3;i++) recent_closed[i]=bars[i+1];
   double recent_ema[];
   const bool ema_ready=V3103RecentM5EMA20(bars,recent_ema);
   const bool ema_rising=ema_ready && V310EMAFromNewest(bars,20)>recent_ema[0];
   V310Bar cycle_closed[]; ArrayResize(cycle_closed,V3103_EMA_CYCLE_BARS);
   for(int i=0;i<V3103_EMA_CYCLE_BARS;i++) cycle_closed[i]=bars[i+1];
   double cycle_ema[];
   const bool ema_recovery_ready=V3103M5EMA20Cycle(bars,cycle_ema);
   const bool ema_recovery_rising=ema_recovery_ready && V3103BullEMAOverallDirection(cycle_ema);
   const bool h2_candidate=(g_v310_pullback.h2_candidate_time==bars[0].time);
   const bool original_h2=!range_like && g_v310_pullback.h_state=="H2" &&
      g_v310_pullback.h2_signal_time==bars[0].time && location.valid;
   const bool recovery_break=!range_like && location.valid &&
      V3103BullRecoveryBreak(h2_candidate,bars[0],bars[1],tick_size);
   const bool real_pullback=ema_recovery_ready && V3103HasCycleBullEMAPullback(cycle_closed,cycle_ema,InpV310LocationToleranceATR*atr);
   const bool route_b_touch=ema_recovery_ready && V3103HasCycleEMATouch(cycle_closed,cycle_ema,InpV310LocationToleranceATR*atr);
   const bool route_b_candle=(bars[0].close>bars[0].open && V310BarRange(bars[0])>0.0 && V310BarBody(bars[0])+1e-12>=tick_size);
   const bool route_b_break=(bars[0].close+1e-12>=bars[1].high+tick_size);
   const bool route_b_close75=(V310CloseLocation(bars[0])+1e-12>=0.75);
   const bool recovery_quality=V3109C31BullRecoveryQuality(bars[0],bars[1],atr,tick_size);
   const bool ema_recovery=ema_recovery_ready && recovery_quality && V3103BullEMARecovery(g_v310_m15_context.allow_m5_scan,
      g_v310_m15_context.hard_exhaustion,g_v310_m15_metrics.protected_swing_low_intact,
      ema_recovery_rising,real_pullback,cycle_closed,cycle_ema,bars[0],bars[1],
      InpV310LocationToleranceATR*atr,tick_size);
   const bool compression_break=ema_ready && V3103BullCompressionBreak(g_v310_m15_context.allow_m5_scan,
      g_v310_m15_context.hard_exhaustion,g_v310_m15_metrics.protected_swing_low_intact,
      ema_rising,range_like,recent_closed,recent_ema,bars[0],InpV310LocationToleranceATR*atr,tick_size);
   const ENUM_V3103_M5_ROUTE route=V3103SelectM5Route(original_h2,recovery_break,ema_recovery,compression_break);
   const bool route_b=(route==V3103_ROUTE_EMA_RECOVERY || route==V3103_ROUTE_EMA_COMPRESSION_BREAK);
   datetime target_setup_anchor=0;
   if(route==V3103_ROUTE_EMA_RECOVERY)
      target_setup_anchor=V3103BullEMAPullbackStart(cycle_closed,cycle_ema,InpV310LocationToleranceATR*atr);
   else if(route==V3103_ROUTE_EMA_COMPRESSION_BREAK)
      target_setup_anchor=V3103RecentEMAInteractionStart(recent_closed,recent_ema,InpV310LocationToleranceATR*atr);
   const bool exclude_route_b_internal=(route_b && target_setup_anchor>0);
   if(InpDebugMode)
      Print("V3103_M5_ROUTE|dir=BUY|time=",TimeToString(bars[0].time,TIME_DATE|TIME_MINUTES),
         "|primary=",V3109C21PrimaryLabel(g_v3109_m15_trend.primary),
         "|phase=",V3109C21LocalPhaseLabel(g_v3109_m15_trend.local_phase),
         "|range=",(range_like?"true":"false"),"|h2_candidate=",(h2_candidate?"true":"false"),
         "|h_state=",g_v310_pullback.h_state,"|location=",(location.valid?"true":"false"),
         "|routeB_pullback=",(real_pullback?"true":"false"),
         "|routeB_dir=",(ema_recovery_rising?"true":"false"),"|routeB_touch=",(route_b_touch?"true":"false"),
         "|routeB_candle=",(route_b_candle?"true":"false"),"|routeB_quality=",(recovery_quality?"true":"false"),
         "|routeB_break=",(route_b_break?"true":"false"),"|routeB_close75=",(route_b_close75?"true":"false"),
         "|original=",(original_h2?"true":"false"),"|recovery=",(recovery_break?"true":"false"),
         "|ema=",(ema_recovery?"true":"false"),"|compression=",(compression_break?"true":"false"),
         "|route=",IntegerToString((int)route));
   if(route==V3103_ROUTE_NONE)
   {
      if(range_like)
      { V310AdvancePullbackState(g_v310_pullback,"RANGE_LIKE",bars[0].time); g_v310_candidate_ready=false; }
      return;
   }
   if(g_v310_pullback.locked) return;
   const int attempt_no=g_v310_pullback.attempt_count;
   string signal_key="";
   string slot_reason="";
   if(!V3109C32PrepareCandidateCycle(DIR_BUY,g_v310_pullback.pullback_start_time,
                                     attempt_no,bars[0].time,signal_key,slot_reason) ||
      !V3109C3CanEmitAttempt(g_v310_pullback.active,g_v310_pullback.locked,
                            attempt_no,signal_key,
                            g_v3109_c32_micro_cycles[0].consumed_setup_key)) return;
   if(route==V3103_ROUTE_H2_RECOVERY_BREAK)
   { signal.valid=true; signal.signal_type="BULL_RECOVERY_BREAK"; }
   else if(route==V3103_ROUTE_H2_ORIGINAL)
   {
      if(signal.pin) signal.signal_type="BULL_PIN_BAR";
      else if(signal.strong_bull) signal.signal_type="STRONG_BULL_BAR";
      else signal.signal_type="BULL_ENGULF_THREE_BEAR";
   }
   else
   {
      signal.valid=true;
      signal.signal_type=(route==V3103_ROUTE_EMA_RECOVERY ? "EMA_RECOVERY_BUY" : "EMA_COMPRESSION_BREAK_BUY");
      location.valid=true; location.location_ema20=true; location.m2b=true;
   }
   const double ask=SymbolInfoDouble(_Symbol,SYMBOL_ASK);
   const double min_distance=MathMax((double)SymbolInfoInteger(_Symbol,SYMBOL_TRADE_STOPS_LEVEL),(double)SymbolInfoInteger(_Symbol,SYMBOL_TRADE_FREEZE_LEVEL))*_Point;
   const double legacy_resistance=V3103NearestValidBuyResistance(bars,bars[0].high+tick_size,g_v310_nearest_resistance);
   const double resistance=V3103NearestValidBuyResistance(bars,g_v310_m15_target_bars,bars[0].high+tick_size,tick_size,target_setup_anchor,exclude_route_b_internal);
   if(!route_b && MathAbs(legacy_resistance-resistance)>tick_size*0.5)
   {
      g_v310_routea_target_diff_buy++;
      Print("V3103_TARGETSPACE_ROUTEA_DIFF|dir=BUY|signal=",TimeToString(bars[0].time,TIME_DATE|TIME_MINUTES),
         "|legacy=",DoubleToString(legacy_resistance,_Digits),"|new=",DoubleToString(resistance,_Digits));
   }
   if(InpDebugMode)
      Print("V3103_TARGETSPACE|dir=BUY|route=",IntegerToString((int)route),"|signal=",TimeToString(bars[0].time,TIME_DATE|TIME_MINUTES),
         "|anchor=",TimeToString(target_setup_anchor,TIME_DATE|TIME_MINUTES),"|exclude_internal=",(exclude_route_b_internal?"true":"false"),
         "|legacy=",DoubleToString(legacy_resistance,_Digits),"|target=",DoubleToString(resistance,_Digits));
   g_v310_buy_diag.plan_evaluated=true;
   V3107StructureStopSelection stop_selection;
   V3107SelectStructureStop(bars,false,stop_selection);
   V310BuildTradePlan(bars[0],stop_selection.anchor,tick_size,min_distance,ask,resistance,g_v310_candidate_plan);
   if(g_v310_candidate_plan.valid && route==V3103_ROUTE_EMA_RECOVERY &&
      V3109C31HasImmediateBullObstacle(bars[0],bars[1],bars[2],
         g_v310_candidate_plan.entry,g_v310_candidate_plan.planned_r))
   {
      g_v310_candidate_plan.valid=false;
      g_v310_candidate_plan.reason_code="PLAN_REJECT_IMMEDIATE_M5_RESISTANCE_LT_1R";
   }
   if(g_v310_candidate_plan.valid &&
      !V3109C31BullStopReliable(attempt_no,stop_selection.source,
         g_v310_candidate_plan.entry,g_v310_candidate_plan.final_stop,g_v310_m15_metrics.atr14))
   {
      g_v310_candidate_plan.valid=false;
      g_v310_candidate_plan.reason_code="PLAN_REJECT_A2_TIGHT_PREVIOUS_BAR_STOP";
   }
   if(InpDebugMode)
      Print("V3103_PLAN_GATE|dir=BUY|route=",IntegerToString((int)route),"|entry=",DoubleToString(g_v310_candidate_plan.entry,_Digits),
         "|sl=",DoubleToString(g_v310_candidate_plan.final_stop,_Digits),"|r=",DoubleToString(g_v310_candidate_plan.planned_r,_Digits),
         "|stop_anchor=",DoubleToString(stop_selection.anchor,_Digits),"|stop_prev=",DoubleToString(stop_selection.previous_extreme,_Digits),
         "|stop_structure=",DoubleToString(stop_selection.structure_extreme,_Digits),"|stop_source=",stop_selection.source,
         "|ask=",DoubleToString(ask,_Digits),"|broker_min=",DoubleToString(min_distance,_Digits),
         "|target=",DoubleToString(resistance,_Digits),"|reason=",g_v310_candidate_plan.reason_code);
   g_v310_buy_diag.plan_valid=g_v310_candidate_plan.valid;
   g_v310_buy_diag.plan_reason=g_v310_candidate_plan.reason_code;
   if(!g_v310_candidate_plan.valid) return;
   g_v310_candidate_signal=signal;
   g_v310_candidate_signal.location_fib236=location.location_fib236;
   g_v310_candidate_signal.location_fib382=location.location_fib382;
   g_v310_candidate_signal.location_ema20=location.location_ema20;
   g_v310_candidate_signal.location_breakout_retest=location.location_breakout_retest;
   g_v310_candidate_signal.m2b=location.m2b;
   g_v310_candidate_signal_time=bars[0].time; g_v310_candidate_bar=bars[0];
   g_v310_candidate_atr=atr; g_v310_candidate_resistance=resistance;
   g_v310_candidate_ai_reviewed=false; ResetAIDecision(g_v310_candidate_ai_decision);
   g_v310_candidate_route=signal.signal_type;
   g_v310_emitted_setup_key=signal_key;
   g_v3109_c3_buy_counter=g_v3109_c3_buy_tactical_counter;
   g_v3109_c3_buy_attempt=attempt_no;
   g_v310_candidate_ready=true;
   Print("[V3.10.7 BUY Candidate] Route=",g_v310_candidate_route," | Time=",TimeToString(bars[0].time,TIME_DATE|TIME_MINUTES)," | Entry=",DoubleToString(g_v310_candidate_plan.entry,_Digits)," | FinalStop=",DoubleToString(g_v310_candidate_plan.final_stop,_Digits));
}

void V3101AppendSellPullbackBar(const V310Bar &bar)
{
   const int old=ArraySize(g_v310_sell_pullback_bars);
   const int next=MathMin(old+1,20);
   if(old<20) ArrayResize(g_v310_sell_pullback_bars,next);
   for(int i=next-1;i>0;i--) g_v310_sell_pullback_bars[i]=g_v310_sell_pullback_bars[i-1];
   g_v310_sell_pullback_bars[0]=bar;
}

bool V3103TryIndependentBearEMARecovery(const V310Bar &bars[],const double atr,const double tick_size)
{
   if(g_v310_sell_candidate_ready)
      return false;

   V310Bar cycle_closed[]; ArrayResize(cycle_closed,V3103_EMA_CYCLE_BARS);
   for(int i=0;i<V3103_EMA_CYCLE_BARS;i++) cycle_closed[i]=bars[i+1];
   double cycle_ema[];
   const bool ema_ready=V3103M5EMA20Cycle(bars,cycle_ema);
   const bool ema_falling=ema_ready && V3103BearEMAOverallDirection(cycle_ema);
   const bool real_rally=ema_ready && V3103HasCycleBearEMARally(cycle_closed,cycle_ema,InpV310LocationToleranceATR*atr);
   const bool route_b_touch=ema_ready && V3103HasCycleEMATouch(cycle_closed,cycle_ema,InpV310LocationToleranceATR*atr);
   const bool route_b_candle=(bars[0].close<bars[0].open && V310BarRange(bars[0])>0.0 && V310BarBody(bars[0])+1e-12>=tick_size);
   const bool route_b_break=(bars[0].close-1e-12<=bars[1].low-tick_size);
   const bool route_b_close75=((bars[0].high-bars[0].close)/V310BarRange(bars[0])+1e-12>=0.75);
   const bool ema_touch=route_b_touch;
   const bool recovery_candle=(bars[0].close<bars[0].open && V310BarRange(bars[0])>0.0 && V310BarBody(bars[0])+1e-12>=tick_size);
   const bool prior_break=(bars[0].close-1e-12<=bars[1].low-tick_size);
   const bool close_location=((bars[0].high-bars[0].close)/V310BarRange(bars[0])+1e-12>=0.75);
   const bool ema_recovery=ema_ready && V3103BearEMARecovery(g_v310_sell_m15_context.allow_m5_scan,
      g_v310_sell_m15_context.hard_exhaustion,g_v310_sell_m15_metrics.protected_swing_high_intact,
      ema_falling,real_rally,cycle_closed,cycle_ema,bars[0],bars[1],
      InpV310LocationToleranceATR*atr,tick_size);
   if(InpDebugMode)
      Print("V3103_M5_ROUTE|dir=SELL|time=",TimeToString(bars[0].time,TIME_DATE|TIME_MINUTES),
         "|legacy_pullback=false|independent_rally=",(real_rally?"true":"false"),
         "|ema_dir=",(ema_falling?"true":"false"),"|ema_touch=",(ema_touch?"true":"false"),
         "|candle=",(recovery_candle?"true":"false"),"|break=",(prior_break?"true":"false"),"|close75=",(close_location?"true":"false"),
         "|ema=",(ema_recovery?"true":"false"),"|route=",IntegerToString(ema_recovery ? (int)V3103_ROUTE_EMA_RECOVERY : (int)V3103_ROUTE_NONE));
   if(!ema_recovery)
      return false;

   const datetime target_setup_anchor=V3103BearEMARallyStart(cycle_closed,cycle_ema,InpV310LocationToleranceATR*atr);
   if(g_v310_sell_pullback.active && !g_v310_sell_pullback.locked)
      return false;
   if(g_v310_sell_pullback.active && g_v310_sell_pullback.locked &&
      target_setup_anchor==g_v310_sell_pullback.pullback_start_time)
      return false;
   string signal_key="";
   string slot_reason="";
   if(target_setup_anchor<=0 ||
      !V3109C32PrepareCandidateCycle(DIR_SELL,target_setup_anchor,1,bars[0].time,
                                     signal_key,slot_reason) ||
      !V3109C3CanEmitAttempt(true,false,1,signal_key,
         g_v3109_c32_micro_cycles[1].consumed_setup_key))
      return false;

   V3101InitBearPullbackState(g_v310_sell_pullback);
   g_v310_sell_pullback.active=true;
   g_v310_sell_pullback.pullback_id="EMA-PB-S-"+IntegerToString((int)target_setup_anchor);
   g_v310_sell_pullback.pullback_start_time=target_setup_anchor;
   g_v310_sell_pullback.pullback_start_low=MathMin(bars[0].low,bars[1].low);
   g_v310_sell_pullback.attempt_count=1;
   g_v310_sell_pullback.h_state="H1";
   g_v310_sell_pullback.attempt_active=true;
   g_v310_sell_pullback.up_leg_active=false;
   g_v310_sell_pullback.current_attempt_low=bars[0].low;
   ArrayResize(g_v310_sell_pullback_bars,0);
   V3101AppendSellPullbackBar(bars[0]);

   V310BearSignalResult signal; ZeroMemory(signal); signal.valid=true; signal.signal_type="EMA_RECOVERY_SELL";
   V310SignalResult location; ZeroMemory(location); location.valid=true; location.location_ema20=true; location.m2b=true;
   const double bid=SymbolInfoDouble(_Symbol,SYMBOL_BID);
   const double min_distance=MathMax((double)SymbolInfoInteger(_Symbol,SYMBOL_TRADE_STOPS_LEVEL),(double)SymbolInfoInteger(_Symbol,SYMBOL_TRADE_FREEZE_LEVEL))*_Point;
   const double support=V3103NearestValidSellSupport(bars,g_v310_sell_m15_target_bars,bars[0].low-tick_size,tick_size,target_setup_anchor,true);
   if(InpDebugMode)
      Print("V3103_TARGETSPACE|dir=SELL|route=EMA_RECOVERY|signal=",TimeToString(bars[0].time,TIME_DATE|TIME_MINUTES),
         "|anchor=",TimeToString(target_setup_anchor,TIME_DATE|TIME_MINUTES),"|exclude_internal=true|target=",DoubleToString(support,_Digits));
   g_v310_sell_diag.signal_valid=true; g_v310_sell_diag.location_valid=true; g_v310_sell_diag.plan_evaluated=true;
   V3107StructureStopSelection stop_selection;
   V3107SelectStructureStop(bars,true,stop_selection);
   V3101BuildSellTradePlan(bars[0],stop_selection.anchor,tick_size,min_distance,bid,support,g_v310_sell_candidate_plan);
   if(InpDebugMode)
      Print("V3103_PLAN_GATE|dir=SELL|route=EMA_RECOVERY|entry=",DoubleToString(g_v310_sell_candidate_plan.entry,_Digits),
         "|sl=",DoubleToString(g_v310_sell_candidate_plan.final_stop,_Digits),"|r=",DoubleToString(g_v310_sell_candidate_plan.planned_r,_Digits),
         "|stop_anchor=",DoubleToString(stop_selection.anchor,_Digits),"|stop_prev=",DoubleToString(stop_selection.previous_extreme,_Digits),
         "|stop_structure=",DoubleToString(stop_selection.structure_extreme,_Digits),"|stop_source=",stop_selection.source,
         "|bid=",DoubleToString(bid,_Digits),"|broker_min=",DoubleToString(min_distance,_Digits),
         "|target=",DoubleToString(support,_Digits),"|reason=",g_v310_sell_candidate_plan.reason_code);
   g_v310_sell_diag.plan_valid=g_v310_sell_candidate_plan.valid; g_v310_sell_diag.plan_reason=g_v310_sell_candidate_plan.reason_code;
   if(!g_v310_sell_candidate_plan.valid)
      return true;
   g_v310_sell_candidate_signal=signal; g_v310_sell_candidate_location=location;
   g_v310_sell_candidate_signal_time=bars[0].time; g_v310_sell_candidate_bar=bars[0];
   g_v310_sell_candidate_atr=atr; g_v310_sell_candidate_support=support;
   g_v310_sell_candidate_ai_reviewed=false; ResetAIDecision(g_v310_sell_candidate_ai_decision);
   g_v310_sell_candidate_route=signal.signal_type; g_v310_sell_emitted_setup_key=signal_key;
   g_v3109_c3_sell_counter=g_v3109_c3_sell_tactical_counter;
   g_v3109_c3_sell_attempt=1; g_v310_sell_candidate_ready=true;
   Print("[V3.10.7 SELL Candidate] Route=",g_v310_sell_candidate_route," | Time=",TimeToString(bars[0].time,TIME_DATE|TIME_MINUTES)," | Entry=",DoubleToString(g_v310_sell_candidate_plan.entry,_Digits)," | FinalStop=",DoubleToString(g_v310_sell_candidate_plan.final_stop,_Digits));
   return true;
}

bool V3109C33TryIndependentBearCompressionBreak(const V310Bar &bars[],
                                                const double atr,const double tick_size)
{
   if(g_v310_sell_candidate_ready ||
      (g_v310_sell_pullback.active && !g_v310_sell_pullback.locked))
      return false;

   V310Bar recent_closed[];
   ArrayResize(recent_closed,3);
   for(int i=0;i<3;i++) recent_closed[i]=bars[i+1];
   double recent_ema[];
   const bool ema_ready=V3103RecentM5EMA20(bars,recent_ema);
   const bool ema_falling=ema_ready && V310EMAFromNewest(bars,20)<recent_ema[0];
   V310RangeLikeResult compression;
   V310EvaluateRangeLike(recent_closed,3,compression);
   const bool route_ready=ema_ready && V3103BearCompressionBreak(
      g_v310_sell_m15_context.allow_m5_scan,g_v310_sell_m15_context.hard_exhaustion,
      g_v310_sell_m15_metrics.protected_swing_high_intact,ema_falling,
      compression.is_range_like,recent_closed,recent_ema,bars[0],
      InpV310LocationToleranceATR*atr,tick_size);
   if(!route_ready) return false;

   const datetime target_setup_anchor=V3103RecentEMAInteractionStart(
      recent_closed,recent_ema,InpV310LocationToleranceATR*atr);
   if(g_v310_sell_pullback.active && g_v310_sell_pullback.locked &&
      target_setup_anchor==g_v310_sell_pullback.pullback_start_time)
      return false;

   string signal_key="";
   string slot_reason="";
   if(target_setup_anchor<=0 ||
      !V3109C32PrepareCandidateCycle(DIR_SELL,target_setup_anchor,1,bars[0].time,
                                     signal_key,slot_reason) ||
      !V3109C3CanEmitAttempt(true,false,1,signal_key,
         g_v3109_c32_micro_cycles[1].consumed_setup_key))
      return false;

   V3101InitBearPullbackState(g_v310_sell_pullback);
   g_v310_sell_pullback.active=true;
   g_v310_sell_pullback.pullback_id="COMP-PB-S-"+IntegerToString((int)target_setup_anchor);
   g_v310_sell_pullback.pullback_start_time=target_setup_anchor;
   g_v310_sell_pullback.pullback_start_low=MathMin(bars[0].low,bars[1].low);
   g_v310_sell_pullback.attempt_count=1;
   g_v310_sell_pullback.h_state="H1";
   g_v310_sell_pullback.attempt_active=true;
   g_v310_sell_pullback.up_leg_active=false;
   g_v310_sell_pullback.current_attempt_low=bars[0].low;
   ArrayResize(g_v310_sell_pullback_bars,0);
   V3101AppendSellPullbackBar(bars[0]);

   V310BearSignalResult signal;
   ZeroMemory(signal);
   signal.valid=true;
   signal.signal_type="EMA_COMPRESSION_BREAK_SELL";
   V310SignalResult location;
   ZeroMemory(location);
   location.valid=true;
   location.location_ema20=true;
   location.m2b=true;
   const double bid=SymbolInfoDouble(_Symbol,SYMBOL_BID);
   const double min_distance=MathMax((double)SymbolInfoInteger(_Symbol,SYMBOL_TRADE_STOPS_LEVEL),
      (double)SymbolInfoInteger(_Symbol,SYMBOL_TRADE_FREEZE_LEVEL))*_Point;
   const double support=V3103NearestValidSellSupport(bars,g_v310_sell_m15_target_bars,
      bars[0].low-tick_size,tick_size,target_setup_anchor,true);
   g_v310_sell_diag.signal_valid=true;
   g_v310_sell_diag.location_valid=true;
   g_v310_sell_diag.plan_evaluated=true;
   V3107StructureStopSelection stop_selection;
   V3107SelectStructureStop(bars,true,stop_selection);
   V3101BuildSellTradePlan(bars[0],stop_selection.anchor,tick_size,min_distance,bid,
                           support,g_v310_sell_candidate_plan);
   g_v310_sell_diag.plan_valid=g_v310_sell_candidate_plan.valid;
   g_v310_sell_diag.plan_reason=g_v310_sell_candidate_plan.reason_code;
   if(!g_v310_sell_candidate_plan.valid) return true;

   g_v310_sell_candidate_signal=signal;
   g_v310_sell_candidate_location=location;
   g_v310_sell_candidate_signal_time=bars[0].time;
   g_v310_sell_candidate_bar=bars[0];
   g_v310_sell_candidate_atr=atr;
   g_v310_sell_candidate_support=support;
   g_v310_sell_candidate_ai_reviewed=false;
   ResetAIDecision(g_v310_sell_candidate_ai_decision);
   g_v310_sell_candidate_route=signal.signal_type;
   g_v310_sell_emitted_setup_key=signal_key;
   g_v3109_c3_sell_counter=false;
   g_v3109_c3_sell_attempt=1;
   g_v310_sell_candidate_ready=true;
   Print("V3109_C33_FREQUENCY_CANDIDATE|dir=SELL|route=EMA_COMPRESSION_BREAK_SELL|time=",
      TimeToString(bars[0].time,TIME_DATE|TIME_MINUTES),
      "|entry=",DoubleToString(g_v310_sell_candidate_plan.entry,_Digits),
      "|sl=",DoubleToString(g_v310_sell_candidate_plan.final_stop,_Digits),
      "|early=",(g_v3109_c33_early_sell?"true":"false"));
   return true;
}

void V3101UpdateM5SellState()
{
   if(InpV310LongOnly || !g_v310_sell_m15_context.allow_m5_scan) return;
   V3102ClearSideDiagnostics(g_v310_sell_diag);
   V310Bar bars[]; if(!V310LoadClosedBars(PERIOD_M5,60,bars)) return;
   double tick_size=SymbolInfoDouble(_Symbol,SYMBOL_TRADE_TICK_SIZE); if(tick_size<=0.0) tick_size=_Point;
   const double atr=V310ATRFromNewest(bars,14);
   g_v3109_c3_sell_counter=false;

   V3109C32CancelInvalidSellPendings(bars[0],atr,tick_size);

   if(V3103TryIndependentBearEMARecovery(bars,atr,tick_size))
      return;
   if(V3109C33TryIndependentBearCompressionBreak(bars,atr,tick_size))
      return;

   if(g_v310_sell_pullback.active && V3101ConfirmBearImpulse(bars[1],bars[0],g_v310_sell_pullback.pullback_start_low,atr,tick_size))
   {
      V3101AdvanceBearPullbackState(g_v310_sell_pullback,"NEW_IMPULSE",bars[0].time);
      g_v310_sell_impulse_confirmed=true;
      g_v310_sell_impulse_low=MathMin(bars[1].low,bars[0].low);
      ArrayResize(g_v310_sell_pullback_bars,0); g_v310_sell_candidate_ready=false; return;
   }

   if(!g_v310_sell_pullback.active && !g_v310_sell_impulse_confirmed)
   {
      double prior_low=bars[2].low; for(int i=3;i<12;i++) prior_low=MathMin(prior_low,bars[i].low);
      if(bars[1].close<=prior_low-MathMax(tick_size,InpV310ImpulseBufferATR*atr) &&
         V310BodyRangeRatio(bars[1])>=0.60 && V310CloseLocation(bars[1])<=0.25 && bars[0].close<prior_low)
      { g_v310_sell_impulse_confirmed=true; g_v310_sell_impulse_low=MathMin(bars[1].low,bars[0].low); }
   }

   if(!g_v310_sell_pullback.active && g_v310_sell_impulse_confirmed && V3101IsRallyStart(bars[1],bars[0],tick_size))
   {
      V3101AdvanceBearPullbackState(g_v310_sell_pullback,"PULLBACK_START",bars[0].time,MathMin(g_v310_sell_impulse_low,bars[0].low));
      ArrayResize(g_v310_sell_pullback_bars,0); V3101AppendSellPullbackBar(bars[0]); return;
   }
   if(!g_v310_sell_pullback.active || g_v310_sell_pullback.locked) return;

   V3101AppendSellPullbackBar(bars[0]);
   bool range_like=false;
   if(ArraySize(g_v310_sell_pullback_bars)>=InpV310M5RangeMinBars)
   {
      V310RangeLikeResult range_result; V310EvaluateRangeLike(g_v310_sell_pullback_bars,InpV310M5RangeMinBars,range_result);
      range_like=range_result.is_range_like;
   }

   V310Bar previous_three[]; ArrayResize(previous_three,3);
   for(int i=0;i<3;i++) previous_three[i]=bars[i+1];
   V310BearSignalResult signal; V3101ClassifyBearSignal(bars[0],previous_three,signal);
   g_v310_sell_diag.signal_valid=signal.valid;
   V310LocationLevels levels;
   const double impulse_range=MathMax(0.0,g_v310_sell_impulse_high-g_v310_sell_impulse_low);
   levels.fib236=g_v310_sell_impulse_low+0.236*impulse_range;
   levels.fib382=g_v310_sell_impulse_low+0.382*impulse_range;
   levels.ema20=V310EMAFromNewest(bars,20);
   levels.breakout_retest=g_v310_sell_impulse_low;
   V310SignalResult location; V310EvaluateLocationBar(bars[0],atr,levels,location,InpV310LocationToleranceATR);
   g_v310_sell_diag.location_valid=location.valid;
   V3101AdvanceBearFromClosedBar(g_v310_sell_pullback,bars[1],bars[0],tick_size,signal.valid);

   if(g_v310_sell_pullback.locked && V3101HasPendingDirection(DIR_SELL))
   {
      string cancel_error="";
      if(!V3101CancelPendingDirection(DIR_SELL,cancel_error)) Print("[V3.10.7 SELL H3/Range撤单异常] ",cancel_error);
      g_v310_sell_candidate_ready=false;
      return;
   }
   V310Bar recent_closed[]; ArrayResize(recent_closed,3);
   for(int i=0;i<3;i++) recent_closed[i]=bars[i+1];
   double recent_ema[];
   const bool ema_ready=V3103RecentM5EMA20(bars,recent_ema);
   const bool ema_falling=ema_ready && V310EMAFromNewest(bars,20)<recent_ema[0];
   V310Bar cycle_closed[]; ArrayResize(cycle_closed,V3103_EMA_CYCLE_BARS);
   for(int i=0;i<V3103_EMA_CYCLE_BARS;i++) cycle_closed[i]=bars[i+1];
   double cycle_ema[];
   const bool ema_recovery_ready=V3103M5EMA20Cycle(bars,cycle_ema);
   const bool ema_recovery_falling=ema_recovery_ready && V3103BearEMAOverallDirection(cycle_ema);
   const bool l2_candidate=(g_v310_sell_pullback.h2_candidate_time==bars[0].time);
   const bool original_l2=!range_like && g_v310_sell_pullback.h_state=="H2" &&
      g_v310_sell_pullback.h2_signal_time==bars[0].time && location.valid;
   const bool recovery_break=!range_like && location.valid &&
      V3103BearRecoveryBreak(l2_candidate,bars[0],bars[1],tick_size);
   const bool real_rally=ema_recovery_ready && V3103HasCycleBearEMARally(cycle_closed,cycle_ema,InpV310LocationToleranceATR*atr);
   const bool route_b_touch=ema_recovery_ready && V3103HasCycleEMATouch(cycle_closed,cycle_ema,InpV310LocationToleranceATR*atr);
   const bool route_b_candle=(bars[0].close<bars[0].open && V310BarRange(bars[0])>0.0 && V310BarBody(bars[0])+1e-12>=tick_size);
   const bool route_b_break=(bars[0].close-1e-12<=bars[1].low-tick_size);
   const bool route_b_close75=((bars[0].high-bars[0].close)/V310BarRange(bars[0])+1e-12>=0.75);
   const bool ema_recovery=ema_recovery_ready && V3103BearEMARecovery(g_v310_sell_m15_context.allow_m5_scan,
      g_v310_sell_m15_context.hard_exhaustion,g_v310_sell_m15_metrics.protected_swing_high_intact,
      ema_recovery_falling,real_rally,cycle_closed,cycle_ema,bars[0],bars[1],
      InpV310LocationToleranceATR*atr,tick_size);
   const bool compression_break=ema_ready && V3103BearCompressionBreak(g_v310_sell_m15_context.allow_m5_scan,
      g_v310_sell_m15_context.hard_exhaustion,g_v310_sell_m15_metrics.protected_swing_high_intact,
      ema_falling,range_like,recent_closed,recent_ema,bars[0],InpV310LocationToleranceATR*atr,tick_size);
   const ENUM_V3103_M5_ROUTE route=V3103SelectM5Route(original_l2,recovery_break,ema_recovery,compression_break);
   const bool route_b=(route==V3103_ROUTE_EMA_RECOVERY || route==V3103_ROUTE_EMA_COMPRESSION_BREAK);
   datetime target_setup_anchor=0;
   if(route==V3103_ROUTE_EMA_RECOVERY)
      target_setup_anchor=V3103BearEMARallyStart(cycle_closed,cycle_ema,InpV310LocationToleranceATR*atr);
   else if(route==V3103_ROUTE_EMA_COMPRESSION_BREAK)
      target_setup_anchor=V3103RecentEMAInteractionStart(recent_closed,recent_ema,InpV310LocationToleranceATR*atr);
   const bool exclude_route_b_internal=(route_b && target_setup_anchor>0);
   if(InpDebugMode)
      Print("V3103_M5_ROUTE|dir=SELL|time=",TimeToString(bars[0].time,TIME_DATE|TIME_MINUTES),
         "|primary=",V3109C21PrimaryLabel(g_v3109_m15_trend.primary),
         "|phase=",V3109C21LocalPhaseLabel(g_v3109_m15_trend.local_phase),
         "|range=",(range_like?"true":"false"),"|l2_candidate=",(l2_candidate?"true":"false"),
         "|h_state=",g_v310_sell_pullback.h_state,"|location=",(location.valid?"true":"false"),
         "|routeB_rally=",(real_rally?"true":"false"),
         "|routeB_dir=",(ema_recovery_falling?"true":"false"),"|routeB_touch=",(route_b_touch?"true":"false"),
         "|routeB_candle=",(route_b_candle?"true":"false"),"|routeB_break=",(route_b_break?"true":"false"),"|routeB_close75=",(route_b_close75?"true":"false"),
         "|original=",(original_l2?"true":"false"),"|recovery=",(recovery_break?"true":"false"),
         "|ema=",(ema_recovery?"true":"false"),"|compression=",(compression_break?"true":"false"),
         "|route=",IntegerToString((int)route));
   if(route==V3103_ROUTE_NONE)
   {
      if(range_like)
      { V3101AdvanceBearPullbackState(g_v310_sell_pullback,"RANGE_LIKE",bars[0].time); g_v310_sell_candidate_ready=false; }
      return;
   }
   if(g_v310_sell_pullback.locked) return;
   const int attempt_no=g_v310_sell_pullback.attempt_count;
   string signal_key="";
   string slot_reason="";
   if(!V3109C32PrepareCandidateCycle(DIR_SELL,g_v310_sell_pullback.pullback_start_time,
                                     attempt_no,bars[0].time,signal_key,slot_reason) ||
      !V3109C3CanEmitAttempt(g_v310_sell_pullback.active,g_v310_sell_pullback.locked,
                            attempt_no,signal_key,
                            g_v3109_c32_micro_cycles[1].consumed_setup_key)) return;
   if(route==V3103_ROUTE_H2_RECOVERY_BREAK)
   { signal.valid=true; signal.signal_type="BEAR_RECOVERY_BREAK"; }
   else if(route==V3103_ROUTE_H2_ORIGINAL)
   {
      if(signal.pin) signal.signal_type="BEAR_PIN_BAR";
      else if(signal.strong_bear) signal.signal_type="STRONG_BEAR_BAR";
      else signal.signal_type="BEAR_ENGULF_THREE_BULL";
   }
   else
   {
      signal.valid=true;
      signal.signal_type=(route==V3103_ROUTE_EMA_RECOVERY ? "EMA_RECOVERY_SELL" : "EMA_COMPRESSION_BREAK_SELL");
      location.valid=true; location.location_ema20=true; location.m2b=true;
   }

   const double bid=SymbolInfoDouble(_Symbol,SYMBOL_BID);
   const double min_distance=MathMax((double)SymbolInfoInteger(_Symbol,SYMBOL_TRADE_STOPS_LEVEL),(double)SymbolInfoInteger(_Symbol,SYMBOL_TRADE_FREEZE_LEVEL))*_Point;
   const double legacy_support=V3103NearestValidSellSupport(bars,bars[0].low-tick_size,g_v310_sell_nearest_support);
   const double support=V3103NearestValidSellSupport(bars,g_v310_sell_m15_target_bars,bars[0].low-tick_size,tick_size,target_setup_anchor,exclude_route_b_internal);
   if(!route_b && MathAbs(legacy_support-support)>tick_size*0.5)
   {
      g_v310_routea_target_diff_sell++;
      Print("V3103_TARGETSPACE_ROUTEA_DIFF|dir=SELL|signal=",TimeToString(bars[0].time,TIME_DATE|TIME_MINUTES),
         "|legacy=",DoubleToString(legacy_support,_Digits),"|new=",DoubleToString(support,_Digits));
   }
   if(InpDebugMode)
      Print("V3103_TARGETSPACE|dir=SELL|route=",IntegerToString((int)route),"|signal=",TimeToString(bars[0].time,TIME_DATE|TIME_MINUTES),
         "|anchor=",TimeToString(target_setup_anchor,TIME_DATE|TIME_MINUTES),"|exclude_internal=",(exclude_route_b_internal?"true":"false"),
         "|legacy=",DoubleToString(legacy_support,_Digits),"|target=",DoubleToString(support,_Digits));
   g_v310_sell_diag.plan_evaluated=true;
   V3107StructureStopSelection stop_selection;
   V3107SelectStructureStop(bars,true,stop_selection);
   V3101BuildSellTradePlan(bars[0],stop_selection.anchor,tick_size,min_distance,bid,support,g_v310_sell_candidate_plan);
   if(InpDebugMode)
      Print("V3103_PLAN_GATE|dir=SELL|route=",IntegerToString((int)route),"|entry=",DoubleToString(g_v310_sell_candidate_plan.entry,_Digits),
         "|sl=",DoubleToString(g_v310_sell_candidate_plan.final_stop,_Digits),"|r=",DoubleToString(g_v310_sell_candidate_plan.planned_r,_Digits),
         "|stop_anchor=",DoubleToString(stop_selection.anchor,_Digits),"|stop_prev=",DoubleToString(stop_selection.previous_extreme,_Digits),
         "|stop_structure=",DoubleToString(stop_selection.structure_extreme,_Digits),"|stop_source=",stop_selection.source,
         "|bid=",DoubleToString(bid,_Digits),"|broker_min=",DoubleToString(min_distance,_Digits),
         "|target=",DoubleToString(support,_Digits),"|reason=",g_v310_sell_candidate_plan.reason_code);
   g_v310_sell_diag.plan_valid=g_v310_sell_candidate_plan.valid;
   g_v310_sell_diag.plan_reason=g_v310_sell_candidate_plan.reason_code;
   if(!g_v310_sell_candidate_plan.valid) return;

   g_v310_sell_candidate_signal=signal;
   g_v310_sell_candidate_location=location;
   g_v310_sell_candidate_signal_time=bars[0].time; g_v310_sell_candidate_bar=bars[0];
   g_v310_sell_candidate_atr=atr; g_v310_sell_candidate_support=support;
   g_v310_sell_candidate_ai_reviewed=false; ResetAIDecision(g_v310_sell_candidate_ai_decision);
   g_v310_sell_candidate_route=signal.signal_type;
   g_v310_sell_emitted_setup_key=signal_key;
   g_v3109_c3_sell_counter=g_v3109_c3_sell_tactical_counter;
   g_v3109_c3_sell_attempt=attempt_no;
   g_v310_sell_candidate_ready=true;
   Print("[V3.10.7 SELL Candidate] Route=",g_v310_sell_candidate_route," | Time=",TimeToString(bars[0].time,TIME_DATE|TIME_MINUTES),
         " | Entry=",DoubleToString(g_v310_sell_candidate_plan.entry,_Digits),
         " | FinalStop=",DoubleToString(g_v310_sell_candidate_plan.final_stop,_Digits));
}

string V310CandidatePayload(const CandidateSignal &candidate)
{
   const bool is_sell=(candidate.direction==DIR_SELL);
   const bool spike_path=(is_sell ? g_v310_sell_m15_context.spike_path : g_v310_m15_context.spike_path);
   const bool ema_path=(is_sell ? g_v310_sell_m15_context.ema_path : g_v310_m15_context.ema_path);
   const bool hard_exhaustion=(is_sell ? g_v310_sell_m15_context.hard_exhaustion : g_v310_m15_context.hard_exhaustion);
   const string pullback_id=(is_sell ? g_v310_sell_pullback.pullback_id : g_v310_pullback.pullback_id);
   const string h_state=(is_sell ? g_v310_sell_pullback.h_state : g_v310_pullback.h_state);
   const int attempt_count=(is_sell ? g_v3109_c3_sell_attempt : g_v3109_c3_buy_attempt);
   const bool countertrend=(is_sell ? g_v3109_c3_sell_counter : g_v3109_c3_buy_counter);
   const string signal_type=(is_sell ? g_v310_sell_candidate_signal.signal_type : g_v310_candidate_signal.signal_type);
   const bool location_ema20=(is_sell ? g_v310_sell_candidate_location.location_ema20 : g_v310_candidate_signal.location_ema20);
   const bool m2b=(is_sell ? g_v310_sell_candidate_location.m2b : g_v310_candidate_signal.m2b);
   const double entry=(is_sell ? g_v310_sell_candidate_plan.entry : g_v310_candidate_plan.entry);
   const double final_stop=(is_sell ? g_v310_sell_candidate_plan.final_stop : g_v310_candidate_plan.final_stop);
   const double planned_r=(is_sell ? g_v310_sell_candidate_plan.planned_r : g_v310_candidate_plan.planned_r);
   string json="{";
    json+="\"strategy_version\":\""+V310_EA_VERSION+"\",\"rule_version\":\""+PARALLEL_AUDIT_RULE_VERSION+"\",";
   json+="\"param_version\":\""+V310_PARAMETER_VERSION+"\",\"signal_id\":\""+JsonEscape(candidate.signal_id)+"\",";
   json+="\"direction\":\""+DirectionLabel(candidate.direction)+"\",\"m15_spike_path\":"+JsonBool(spike_path)+",";
   json+="\"m15_ema_path\":"+JsonBool(ema_path)+",\"m15_hard_exhaustion\":"+JsonBool(hard_exhaustion)+",";
   json+="\"pullback_id\":\""+JsonEscape(pullback_id)+"\",\"h_state\":\""+JsonEscape(h_state)+"\",";
   json+="\"attempt_count\":"+IntegerToString(attempt_count)+",\"signal_type\":\""+JsonEscape(signal_type)+"\",";
   json+="\"trade_class\":\""+(countertrend ? "COUNTERTREND" : "PRIMARY_ALIGNED")+"\",";
   json+="\"primary\":\""+V3109C21PrimaryLabel(g_v3109_m15_trend.primary)+"\",";
   json+="\"m15_macro_bias\":"+IntegerToString(g_v3109_c3_m15_macro_bias)+",";
   json+="\"aligned_fills\":"+IntegerToString(g_v3109_c3_quota.aligned_fills)+",";
   json+="\"counter_fills\":"+IntegerToString(g_v3109_c3_quota.counter_fills)+",";
   json+="\"location_ema20\":"+JsonBool(location_ema20)+",\"m2b\":"+JsonBool(m2b)+",";
   json+="\"planned_entry\":"+JsonNumber(entry)+",\"final_stop\":"+JsonNumber(final_stop)+",";
   json+="\"planned_r\":"+JsonNumber(planned_r)+"}";
   return json;
}

void V310BuildLegacyExecutionCandidate(CandidateSignal &candidate)
{
   ResetCandidate(candidate);
   candidate.signal_id=(g_v3109_c3_buy_counter ? "V310_C3_COUNTER_BUY_" : "V310_C3_ALIGNED_BUY_A"+
      IntegerToString(g_v3109_c3_buy_attempt)+"_")+IntegerToString((int)g_v310_candidate_signal_time);
   candidate.signal_time=g_v310_candidate_signal_time;
   candidate.direction=DIR_BUY; candidate.signal_route=SIGNAL_ROUTE_STRATEGY01_H2;
   candidate.major_structure_valid=(g_v3109_c3_buy_counter || g_v310_m15_context.allow_m5_scan);
   candidate.h_attempt=g_v3109_c3_buy_attempt; candidate.h_near_ema=g_v310_candidate_signal.m2b;
   candidate.ma_confluence=g_v310_candidate_signal.location_ema20;
   candidate.sr_confluence=g_v310_candidate_signal.location_breakout_retest;
   candidate.pin_bar=g_v310_candidate_signal.pin;
   candidate.engulfing=g_v310_candidate_signal.three_bear_engulf;
   candidate.strong_reversal_bar=g_v310_candidate_signal.strong_bull;
   candidate.signal_open=g_v310_candidate_bar.open; candidate.signal_high=g_v310_candidate_bar.high;
   candidate.signal_low=g_v310_candidate_bar.low; candidate.signal_close=g_v310_candidate_bar.close;
   candidate.atr14=g_v310_candidate_atr;
   candidate.planned_entry=g_v310_candidate_plan.entry;
   candidate.planned_sl=g_v310_candidate_plan.final_stop;
   candidate.legacy_sl=g_v310_candidate_plan.final_stop;
   candidate.signal_sl=g_v310_candidate_plan.final_stop;
   candidate.exit_unit=g_v310_candidate_plan.planned_r;
   candidate.stop_mode=STOP_MODE_LEGACY_ONLY;
   candidate.tp1=g_v310_candidate_resistance;
   candidate.rr_to_tp1=(g_v310_candidate_resistance-candidate.planned_entry)/g_v310_candidate_plan.planned_r;
}

void V310HandleReadyCandidate(const bool new_entries_allowed)
{
   if(!g_v310_candidate_ready || !new_entries_allowed) return;
   const datetime expiry=g_v310_candidate_signal_time+InpPendingExpiryBars*PeriodSeconds(PERIOD_M5);
   if(TimeTradeServer()>=expiry)
   {
      if(InpDebugMode) Print("V3109_C3_CANDIDATE_BLOCK|dir=BUY|reason=EXPIRED");
      g_v310_candidate_ready=false; return;
   }
   CandidateSignal candidate; V310BuildLegacyExecutionCandidate(candidate);
   string slot_reason="";
   const double provisional_risk=V3109C32RiskBudgetUsd(
      AccountInfoDouble(ACCOUNT_EQUITY),InpRiskPercent);
   int slot=V3109C32SelectLiveSlot(DIR_BUY,candidate.signal_time,
                                    provisional_risk,slot_reason);
   if(slot<0)
   {
      if(InpDebugMode) Print("V3109_C32_ENTRY_BLOCK|dir=BUY|reason=",slot_reason);
      g_v310_candidate_ready=false;
      return;
   }
   g_v3109_c32_candidate_slot=slot;
   const string payload=V310CandidatePayload(candidate);
   if(!g_v310_candidate_ai_reviewed)
   {
      RecordObservationEvent("candidate",candidate.signal_id,"candidate","pass","V3.10.7 H2 deterministic candidate",payload);
      g_v310_candidate_ai_decision=g_ai.Review(candidate,payload);
      g_v310_candidate_ai_reviewed=true;
      if(!IsApproved(g_v310_candidate_ai_decision,InpAIConfidenceThreshold,InpAIFailOpenGrace))
      { RecordObservationEvent("ai_reject",candidate.signal_id,"ai","reject",g_v310_candidate_ai_decision.reason,payload); g_v310_candidate_ready=false; g_v3109_c32_candidate_slot=-1; return; }
   }
   const datetime pre_order_now=TimeTradeServer();
   const WeekendGuardStatus pre_order_weekend_guard=EvaluateWeekendGuard(_Symbol,pre_order_now,
      InpWeekendGuardEnabled,InpWeekendNoNewTradeMinutes,InpWeekendForceCloseMinutes,
      InpWeekendFallbackFridayCloseHour,InpWeekendFallbackFridayCloseMinute);
   const RolloverGuardStatus pre_order_rollover_guard=EvaluateRolloverGuard(pre_order_now,
      InpRolloverGuardEnabled,InpRolloverStartHour,InpRolloverStartMinute,
      InpRolloverEndHour,InpRolloverEndMinute);
   if(!CanOpenNewTradeNow(pre_order_weekend_guard) ||
      !CanOpenNewTradeDuringRollover(pre_order_rollover_guard) ||
      g_risk.IsEntryLocked())
   {
      RecordObservationEvent("risk_block",candidate.signal_id,"pre_order_recheck","reject",
         "AI审核后交易条件已变化","V3.10.7下单前复核拒绝");
      g_v310_candidate_ready=false;
      g_v3109_c32_candidate_slot=-1;
      return;
   }
   slot=V3109C32SelectLiveSlot(DIR_BUY,candidate.signal_time,
                               provisional_risk,slot_reason);
   if(slot<0)
   {
      RecordObservationEvent("risk_block",candidate.signal_id,"slot_admission","reject",
                             slot_reason,"C3.2三槽准入复核拒绝");
      g_v310_candidate_ready=false;
      g_v3109_c32_candidate_slot=-1;
      return;
   }
   g_v3109_c32_candidate_slot=slot;
   MqlTick tick; string error=""; if(!SymbolInfoTick(_Symbol,tick)) return;
   if(!g_risk.CheckSpread(tick.bid,tick.ask,candidate.atr14,InpMaxSpreadATRRatio,InpAbsoluteMaxSpreadPoints,_Point,error))
   { if(InpDebugMode) Print("V3109_C3_CANDIDATE_BLOCK|dir=BUY|reason=SPREAD|detail=",error); g_v310_candidate_ready=false; return; }
   double tick_size=SymbolInfoDouble(_Symbol,SYMBOL_TRADE_TICK_SIZE); if(tick_size<=0.0) tick_size=_Point;
   double tick_value=SymbolInfoDouble(_Symbol,SYMBOL_TRADE_TICK_VALUE_LOSS); if(tick_value<=0.0) tick_value=SymbolInfoDouble(_Symbol,SYMBOL_TRADE_TICK_VALUE);
   const double volume=CalculateRiskVolume(AccountInfoDouble(ACCOUNT_EQUITY),InpRiskPercent,
      candidate.planned_entry,candidate.planned_sl,tick_size,tick_value,
      SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_MIN),SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_MAX),SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_STEP),error);
   if(volume<=0.0) { if(InpDebugMode) Print("V3109_C3_CANDIDATE_BLOCK|dir=BUY|reason=VOLUME|detail=",error); g_v310_candidate_ready=false; g_v3109_c32_candidate_slot=-1; return; }
   const double candidate_risk=CalculatePlannedRiskMoney(DIR_BUY,
      candidate.planned_entry,candidate.planned_sl,volume);
   if(candidate_risk<=0.0 || !MathIsValidNumber(candidate_risk))
   {
      if(InpDebugMode) Print("V3109_C3_CANDIDATE_BLOCK|dir=BUY|reason=RISK_MEASURE");
      g_v310_candidate_ready=false;
      g_v3109_c32_candidate_slot=-1;
      return;
   }
   slot=V3109C32SelectLiveSlot(DIR_BUY,candidate.signal_time,
                               candidate_risk,slot_reason);
   if(slot<0)
   {
      if(InpDebugMode) Print("V3109_C32_ENTRY_BLOCK|dir=BUY|reason=",slot_reason,
                             "|candidate_risk_usd=",DoubleToString(candidate_risk,2));
      g_v310_candidate_ready=false;
      g_v3109_c32_candidate_slot=-1;
      return;
   }
   g_v3109_c32_candidate_slot=slot;
   if(g_trade_slots[slot].PlacePending(candidate,volume,expiry,error))
   {
      g_v310_pending_signal_low=candidate.signal_low;
      g_v3109_c3_pending_counter=g_v3109_c3_buy_counter;
      g_v3109_c3_pending_direction=DIR_BUY;
      g_v3109_c3_pending_attempt=g_v3109_c3_buy_attempt;
      g_v3109_c3_pending_setup_key=g_v310_emitted_setup_key;
      const datetime pullback_start=(g_v3109_c3_buy_counter ?
         g_v3109_c3_quota.exhaustion_started_at : g_v310_pullback.pullback_start_time);
      V3109C32BindPendingContext(slot,DIR_BUY,g_v3109_c3_buy_counter,
         g_v3109_c3_buy_attempt,pullback_start,candidate.signal_time,
         candidate.signal_low,candidate.signal_high,g_v310_emitted_setup_key,
         candidate_risk,V3109C32AggregateInitialRiskUsd());
      RecordObservationEvent("pending_created",candidate.signal_id,"order","pass","V3109 C3 protected Buy Stop",payload);
      g_v310_candidate_ready=false;
      g_v3109_c32_candidate_slot=-1;
      return;
   }
   const bool reached_crossed=(StringFind(error,"达到或越过")>=0);
   const bool invalid_distance=(StringFind(error,"距离")>=0 || StringFind(error,"Stops")>=0 || StringFind(error,"Freeze")>=0);
   if(invalid_distance)
   {
      // Candidate C: ARMED waiting. The entry is valid but temporarily too close
      // to market. Keep the signal and retry on subsequent M5 bars until expiry.
      RecordObservationEvent("armed_waiting",candidate.signal_id,"order","retry","V310_ARMED_WAIT_DISTANCE",error);
   }
   else if(reached_crossed)
   {
      RecordObservationEvent("armed_crossed",candidate.signal_id,"order","reject","V310_ARMED_CROSSED_NO_CHASE",error);
      g_v310_candidate_ready=false;
      g_v3109_c32_candidate_slot=-1;
   }
   else RecordObservationEvent("execution_error",candidate.signal_id,"order","retry",error,"temporary error; original prices retained");
   if(InpDebugMode) Print("V3109_C3_ORDER_NOT_PLACED|dir=BUY|class=",
      (g_v3109_c3_buy_counter ? "COUNTERTREND" : "PRIMARY_ALIGNED"),
      "|attempt=",IntegerToString(g_v3109_c3_buy_attempt),"|detail=",error);
}

void V3101BuildSellExecutionCandidate(CandidateSignal &candidate)
{
   ResetCandidate(candidate);
   candidate.signal_id=(g_v3109_c3_sell_counter ? "V310_C3_COUNTER_SELL_" : "V310_C3_ALIGNED_SELL_A"+
      IntegerToString(g_v3109_c3_sell_attempt)+"_")+IntegerToString((int)g_v310_sell_candidate_signal_time);
   candidate.signal_time=g_v310_sell_candidate_signal_time;
   candidate.direction=DIR_SELL; candidate.signal_route=SIGNAL_ROUTE_STRATEGY01_H2;
   candidate.major_structure_valid=(g_v3109_c3_sell_counter || g_v310_sell_m15_context.allow_m5_scan);
   candidate.h_attempt=g_v3109_c3_sell_attempt; candidate.h_near_ema=g_v310_sell_candidate_location.m2b;
   candidate.ma_confluence=g_v310_sell_candidate_location.location_ema20;
   candidate.sr_confluence=g_v310_sell_candidate_location.location_breakout_retest;
   candidate.pin_bar=g_v310_sell_candidate_signal.pin;
   candidate.engulfing=g_v310_sell_candidate_signal.three_bull_engulf;
   candidate.strong_reversal_bar=g_v310_sell_candidate_signal.strong_bear;
   candidate.signal_open=g_v310_sell_candidate_bar.open; candidate.signal_high=g_v310_sell_candidate_bar.high;
   candidate.signal_low=g_v310_sell_candidate_bar.low; candidate.signal_close=g_v310_sell_candidate_bar.close;
   candidate.atr14=g_v310_sell_candidate_atr;
   candidate.planned_entry=g_v310_sell_candidate_plan.entry;
   candidate.planned_sl=g_v310_sell_candidate_plan.final_stop;
   candidate.legacy_sl=g_v310_sell_candidate_plan.final_stop;
   candidate.signal_sl=g_v310_sell_candidate_plan.final_stop;
   candidate.exit_unit=g_v310_sell_candidate_plan.planned_r;
   candidate.stop_mode=STOP_MODE_LEGACY_ONLY;
   candidate.tp1=g_v310_sell_candidate_support;
   candidate.rr_to_tp1=(candidate.planned_entry-g_v310_sell_candidate_support)/g_v310_sell_candidate_plan.planned_r;
}

void V3101HandleReadySellCandidate(const bool new_entries_allowed)
{
   if(InpV310LongOnly || !g_v310_sell_candidate_ready || !new_entries_allowed) return;
   const datetime expiry=g_v310_sell_candidate_signal_time+InpPendingExpiryBars*PeriodSeconds(PERIOD_M5);
   if(TimeTradeServer()>=expiry)
   {
      if(InpDebugMode) Print("V3109_C3_CANDIDATE_BLOCK|dir=SELL|reason=EXPIRED");
      g_v310_sell_candidate_ready=false; return;
   }
   CandidateSignal candidate; V3101BuildSellExecutionCandidate(candidate);
   string slot_reason="";
   const double provisional_risk=V3109C32RiskBudgetUsd(
      AccountInfoDouble(ACCOUNT_EQUITY),InpRiskPercent);
   int slot=V3109C32SelectLiveSlot(DIR_SELL,candidate.signal_time,
                                    provisional_risk,slot_reason);
   if(slot<0)
   {
      if(InpDebugMode) Print("V3109_C32_ENTRY_BLOCK|dir=SELL|reason=",slot_reason);
      g_v310_sell_candidate_ready=false;
      return;
   }
   g_v3109_c32_candidate_slot=slot;
   const string payload=V310CandidatePayload(candidate);
   if(!g_v310_sell_candidate_ai_reviewed)
   {
      RecordObservationEvent("candidate",candidate.signal_id,"candidate","pass","V3.10.7 SELL H2 deterministic candidate",payload);
      g_v310_sell_candidate_ai_decision=g_ai.Review(candidate,payload);
      g_v310_sell_candidate_ai_reviewed=true;
      if(!IsApproved(g_v310_sell_candidate_ai_decision,InpAIConfidenceThreshold,InpAIFailOpenGrace))
      { RecordObservationEvent("ai_reject",candidate.signal_id,"ai","reject",g_v310_sell_candidate_ai_decision.reason,payload); g_v310_sell_candidate_ready=false; g_v3109_c32_candidate_slot=-1; return; }
   }
   const datetime pre_order_now=TimeTradeServer();
   const WeekendGuardStatus pre_order_weekend_guard=EvaluateWeekendGuard(_Symbol,pre_order_now,
      InpWeekendGuardEnabled,InpWeekendNoNewTradeMinutes,InpWeekendForceCloseMinutes,
      InpWeekendFallbackFridayCloseHour,InpWeekendFallbackFridayCloseMinute);
   const RolloverGuardStatus pre_order_rollover_guard=EvaluateRolloverGuard(pre_order_now,
      InpRolloverGuardEnabled,InpRolloverStartHour,InpRolloverStartMinute,
      InpRolloverEndHour,InpRolloverEndMinute);
   if(!CanOpenNewTradeNow(pre_order_weekend_guard) ||
      !CanOpenNewTradeDuringRollover(pre_order_rollover_guard) ||
      g_risk.IsEntryLocked())
   {
      RecordObservationEvent("risk_block",candidate.signal_id,"pre_order_recheck","reject",
         "AI审核后交易条件已变化","V3.10.7 SELL下单前复核拒绝");
      g_v310_sell_candidate_ready=false;
      g_v3109_c32_candidate_slot=-1;
      return;
   }
   slot=V3109C32SelectLiveSlot(DIR_SELL,candidate.signal_time,
                               provisional_risk,slot_reason);
   if(slot<0)
   {
      RecordObservationEvent("risk_block",candidate.signal_id,"slot_admission","reject",
                             slot_reason,"C3.2三槽准入复核拒绝");
      g_v310_sell_candidate_ready=false;
      g_v3109_c32_candidate_slot=-1;
      return;
   }
   g_v3109_c32_candidate_slot=slot;
   MqlTick tick; string error=""; if(!SymbolInfoTick(_Symbol,tick)) return;
   if(!g_risk.CheckSpread(tick.bid,tick.ask,candidate.atr14,InpMaxSpreadATRRatio,InpAbsoluteMaxSpreadPoints,_Point,error))
   { if(InpDebugMode) Print("V3109_C3_CANDIDATE_BLOCK|dir=SELL|reason=SPREAD|detail=",error); g_v310_sell_candidate_ready=false; return; }
   double tick_size=SymbolInfoDouble(_Symbol,SYMBOL_TRADE_TICK_SIZE); if(tick_size<=0.0) tick_size=_Point;
   double tick_value=SymbolInfoDouble(_Symbol,SYMBOL_TRADE_TICK_VALUE_LOSS); if(tick_value<=0.0) tick_value=SymbolInfoDouble(_Symbol,SYMBOL_TRADE_TICK_VALUE);
   const double volume=CalculateRiskVolume(AccountInfoDouble(ACCOUNT_EQUITY),InpRiskPercent,
      candidate.planned_entry,candidate.planned_sl,tick_size,tick_value,
      SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_MIN),SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_MAX),SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_STEP),error);
   if(volume<=0.0) { if(InpDebugMode) Print("V3109_C3_CANDIDATE_BLOCK|dir=SELL|reason=VOLUME|detail=",error); g_v310_sell_candidate_ready=false; g_v3109_c32_candidate_slot=-1; return; }
   const double candidate_risk=CalculatePlannedRiskMoney(DIR_SELL,
      candidate.planned_entry,candidate.planned_sl,volume);
   if(candidate_risk<=0.0 || !MathIsValidNumber(candidate_risk))
   {
      if(InpDebugMode) Print("V3109_C3_CANDIDATE_BLOCK|dir=SELL|reason=RISK_MEASURE");
      g_v310_sell_candidate_ready=false;
      g_v3109_c32_candidate_slot=-1;
      return;
   }
   slot=V3109C32SelectLiveSlot(DIR_SELL,candidate.signal_time,
                               candidate_risk,slot_reason);
   if(slot<0)
   {
      if(InpDebugMode) Print("V3109_C32_ENTRY_BLOCK|dir=SELL|reason=",slot_reason,
                             "|candidate_risk_usd=",DoubleToString(candidate_risk,2));
      g_v310_sell_candidate_ready=false;
      g_v3109_c32_candidate_slot=-1;
      return;
   }
   g_v3109_c32_candidate_slot=slot;
   if(g_trade_slots[slot].PlacePending(candidate,volume,expiry,error))
   {
      g_v310_pending_signal_high=candidate.signal_high;
      g_v3109_c3_pending_counter=g_v3109_c3_sell_counter;
      g_v3109_c3_pending_direction=DIR_SELL;
      g_v3109_c3_pending_attempt=g_v3109_c3_sell_attempt;
      g_v3109_c3_pending_setup_key=g_v310_sell_emitted_setup_key;
      const datetime pullback_start=(g_v3109_c3_sell_counter ?
         g_v3109_c3_quota.exhaustion_started_at : g_v310_sell_pullback.pullback_start_time);
      V3109C32BindPendingContext(slot,DIR_SELL,g_v3109_c3_sell_counter,
         g_v3109_c3_sell_attempt,pullback_start,candidate.signal_time,
         candidate.signal_low,candidate.signal_high,g_v310_sell_emitted_setup_key,
         candidate_risk,V3109C32AggregateInitialRiskUsd());
      RecordObservationEvent("pending_created",candidate.signal_id,"order","pass","V3109 C3 protected Sell Stop",payload);
      g_v310_sell_candidate_ready=false;
      g_v3109_c32_candidate_slot=-1;
      return;
   }
   const bool reached_crossed=(StringFind(error,"达到或越过")>=0);
   const bool invalid_distance=(StringFind(error,"距离")>=0 || StringFind(error,"Stops")>=0 || StringFind(error,"Freeze")>=0);
   if(invalid_distance)
   {
      RecordObservationEvent("armed_waiting",candidate.signal_id,"order","retry","V310_ARMED_WAIT_DISTANCE",error);
   }
   else if(reached_crossed)
   {
      RecordObservationEvent("armed_crossed",candidate.signal_id,"order","reject","V310_ARMED_CROSSED_NO_CHASE",error);
      g_v310_sell_candidate_ready=false;
      g_v3109_c32_candidate_slot=-1;
   }
   else RecordObservationEvent("execution_error",candidate.signal_id,"order","retry",error,"temporary error; original prices retained");
   if(InpDebugMode) Print("V3109_C3_ORDER_NOT_PLACED|dir=SELL|class=",
      (g_v3109_c3_sell_counter ? "COUNTERTREND" : "PRIMARY_ALIGNED"),
      "|attempt=",IntegerToString(g_v3109_c3_sell_attempt),"|detail=",error);
}

// ===== Phase B: V3.10 盯盘快照输出 =====
// 只读输出：把 V3.10 运行态映射成与 V3.9.20 相同的观测表结构，
// 供 Python 复盘服务消费。不参与任何交易判断，也不修改任何策略状态。
void EmitV310MarketSnapshot()
{
   if(!g_observation.IsEnabled()) return;

   // 1) 交易状态：优先反映正在持仓/挂单的槽位，否则反映候选状态
   TradeRuntimeState runtime_state=g_trade_slots[0].State();
   bool managed_exposure=false;
   for(int slot=0;slot<V3109C32_MAX_SLOTS;slot++)
   {
      const TradeRuntimeState slot_state=g_trade_slots[slot].State();
      if(slot_state.state==STATE_PENDING_ORDER || slot_state.state==STATE_POSITION_OPEN ||
         slot_state.state==STATE_TP1_REACHED || slot_state.state==STATE_RUNNER ||
         slot_state.state==STATE_LOCKED)
      {
         runtime_state=slot_state;
         managed_exposure=true;
         break;
      }
   }
   const bool buy_candidate=g_v310_candidate_ready;
   const bool sell_candidate=(!InpV310LongOnly && g_v310_sell_candidate_ready);
   const bool candidate_seen=(buy_candidate || sell_candidate);
   if(!managed_exposure)
   {
      runtime_state.state=(candidate_seen ? STATE_CANDIDATE_FOUND : STATE_IDLE);
      runtime_state.signal_id=(buy_candidate ? g_v310_emitted_setup_key :
                              (sell_candidate ? g_v310_sell_emitted_setup_key : ""));
   }

   // 2) 扫描语义：把 V3.10 的定方向/入场状态翻译成观测表的 outcome/stage/reason
   ScanResult scan;
   ResetScanResult(scan,PERIOD_M5,g_v310_last_m5_closed);
   if(managed_exposure)
   {
      scan.outcome=SCAN_SKIP;
      scan.stage=STAGE_EXPOSURE;
      scan.reason="已有本EA持仓或挂单，继续持仓管理";
   }
   else if(candidate_seen)
   {
      scan.outcome=SCAN_PASS;
      scan.stage=STAGE_CANDIDATE;
      scan.reason="V3.10.7 候选已形成 | route="+
                  (buy_candidate ? g_v310_candidate_route : g_v310_sell_candidate_route);
   }
   else if(!g_v310_m15_context.allow_m5_scan)
   {
      scan.outcome=SCAN_REJECT;
      scan.stage=STAGE_TREND;
      scan.reason="M15定方向未放行 | "+g_v310_m15_context.reason_code;
   }
   else
   {
      scan.outcome=SCAN_REJECT;
      scan.stage=STAGE_PRICE_ACTION;
      scan.reason="M5未形成候选 | H="+g_v310_pullback.h_state+
                  " | "+g_v310_pullback.reason_code;
   }

   // 每根 M5 输出一行扫描诊断，格式与 V3.9.20 完全一致，便于日志对照。
   g_last_scan_result=scan;
   g_last_scan_text=FormatScanResult(scan);
   Print(g_last_scan_text);

   if(!g_observation.WriteMarketSnapshot(TimeTradeServer(),scan,g_weekend_status_text,
                                         g_rollover_status_text,runtime_state))
      Print("[AI盯盘数据异常] 市场快照写入失败 | Error=",GetLastError());
}

void V310ProcessTick(const bool new_entries_allowed)
{
   const datetime m15_closed=iTime(_Symbol,PERIOD_M15,V310_CLOSED_SHIFT);
   const datetime m5_closed=iTime(_Symbol,PERIOD_M5,V310_CLOSED_SHIFT);
   if(m15_closed<=0 || m5_closed<=0) return;
   const bool new_m15=(m15_closed!=g_v310_last_m15_closed);
   const bool new_m5=(m5_closed!=g_v310_last_m5_closed);
   if(!new_m15 && !new_m5) return;
   if(new_m15)
   {
      g_v310_last_m15_closed=m15_closed;
      V310UpdateM15Context();
      if(!InpV310LongOnly) V3101UpdateM15BearContext();
   }
   if(new_m5)
   {
      g_v310_last_m5_closed=m5_closed;
      V310UpdateM5State();
      if(!InpV310LongOnly) V3101UpdateM5SellState();
      MarketSnapshot audit_bars[]; string audit_error="";
      if(g_indicators.Load(200,audit_bars,audit_error))
      {
         MqlTick audit_tick;
         if(SymbolInfoTick(_Symbol,audit_tick) &&
            !WriteParallelIndependentInput(audit_bars,audit_tick.bid,audit_tick.ask,!new_entries_allowed,audit_error))
            Print("[Parallel AI审计异常] 独立输入写入失败 | ",audit_error);
      }
      else if(InpDebugMode) Print("[Parallel AI审计异常] M5输入加载失败 | ",audit_error);
      const bool buy_candidate_seen=g_v310_candidate_ready;
      const bool sell_candidate_seen=(!InpV310LongOnly && g_v310_sell_candidate_ready);
      const bool candidate_seen=(buy_candidate_seen || sell_candidate_seen);
      const ENUM_TRADE_DIRECTION candidate_direction=(buy_candidate_seen ? DIR_BUY : (sell_candidate_seen ? DIR_SELL : DIR_NONE));
      V310HandleReadyCandidate(new_entries_allowed);
      V3101HandleReadySellCandidate(new_entries_allowed);
      const string trace_execution=(V3109C32HasManagedPendingOrders() ? "PENDING_CREATED" :
         (candidate_seen ? "NOT_SENT" : "NOT_RUN"));
      if(!WriteParallelV310Trace(candidate_seen,candidate_direction,trace_execution,audit_error) && InpDebugMode)
         Print("[Parallel AI审计异常] V3.10.7 Trace写入失败 | ",audit_error);
      // Phase B: 每根 M5 收盘输出一行盯盘快照（只读，不影响交易逻辑）
      EmitV310MarketSnapshot();
   }
   if(InpDebugMode)
      Print("[V3.10.7闭合K] M15=",TimeToString(m15_closed,TIME_DATE|TIME_MINUTES),
            " | M5=",TimeToString(m5_closed,TIME_DATE|TIME_MINUTES),
            " | Rule=",PARALLEL_AUDIT_RULE_VERSION,
            " | Params=",V310_PARAMETER_VERSION);
}

void V3109C32ManageSlotPosition(const int slot,const double bid,const double ask)
{
   if(slot<0 || slot>=V3109C32_MAX_SLOTS) return;
   string event_text="";
   string protection_event="";
   if(g_trade_slots[slot].RetryPendingProtection(protection_event))
   {
      const TradeRuntimeState protected_state=g_trade_slots[slot].State();
      string protection_type="server_tp_set";
      if(StringFind(protection_event,"[保本]")==0) protection_type="break_even_set";
      else if(StringFind(protection_event,"[锁盈]")==0) protection_type="profit_lock_set";
      else if(StringFind(protection_event,"[RUNNER]")==0) protection_type="position_update";
      RecordObservationEvent(protection_type,protected_state.signal_id,"position","pass",
                             protection_event,"持仓保护修改已确认",protected_state.order_ticket,
                             protected_state.position_id,0,protected_state.direction,
                             protected_state.remaining_volume,protected_state.stop_loss,0.0);
      Print("V3109_C32_SLOT|slot=",slot,"|",protection_event);
   }
   else if(protection_event!="")
      Print("V3109_C32_SLOT|slot=",slot,"|",protection_event);

   const TradeRuntimeState before_exit=g_trade_slots[slot].State();
   if(before_exit.exit_mode==EXIT_MODE_STAGED)
   {
      const ENUM_EXIT_STAGE intended_stage=NextStagedExitStage(before_exit,bid,ask);
      if(intended_stage!=EXIT_STAGE_NONE)
      {
         if(g_trade_slots[slot].IsExitStageRetryBlocked(intended_stage)) return;
         RecordObservationEvent(intended_stage==EXIT_STAGE_TP1 ? "tp1_reached" : "tp2_reached",
                                before_exit.signal_id,"position","reached",
                                intended_stage==EXIT_STAGE_TP1 ? "已达到1R" : "已达到2R",
                                "基于实际成交价和初始止损计算",before_exit.order_ticket,
                                before_exit.position_id,0,before_exit.direction,
                                before_exit.remaining_volume,0.0,0.0);
         const bool exit_ok=g_trade_slots[slot].ManageStagedExit(bid,ask,event_text);
         const TradeRuntimeState after_exit=g_trade_slots[slot].State();
         if(exit_ok)
         {
            const string event_type=(intended_stage==EXIT_STAGE_TP1 ?
                                     "tp1_partial_close" : "tp2_partial_close");
            RecordObservationEvent(event_type,before_exit.signal_id,"position","pass",
                                   event_text,"实际持仓手数变化已确认",before_exit.order_ticket,
                                   before_exit.position_id,0,before_exit.direction,
                                   after_exit.remaining_volume,0.0,0.0);
            NotifyEvent(event_type+"_S"+IntegerToString(slot)+"_"+
                        IntegerToString((int)TimeCurrent()),event_text);
            string immediate_protection="";
            if(g_trade_slots[slot].RetryPendingProtection(immediate_protection))
            {
               const TradeRuntimeState locked_state=g_trade_slots[slot].State();
               const string protection_type=(intended_stage==EXIT_STAGE_TP1 ?
                                               "break_even_set" : "profit_lock_set");
               RecordObservationEvent(protection_type,before_exit.signal_id,"position","pass",
                                      immediate_protection,"阶段成交后的止损保护",before_exit.order_ticket,
                                      before_exit.position_id,0,before_exit.direction,
                                      locked_state.remaining_volume,locked_state.stop_loss,0.0);
               Print("V3109_C32_SLOT|slot=",slot,"|",immediate_protection);
            }
            else if(immediate_protection!="")
               Print("V3109_C32_SLOT|slot=",slot,"|",immediate_protection);
         }
         else
         {
            Print("V3109_C32_SLOT|slot=",slot,"|",event_text);
            RecordObservationEvent("exit_execution_error",before_exit.signal_id,"position","error",
                                   event_text,"阶段状态未提前提交",before_exit.order_ticket,
                                   before_exit.position_id,0,before_exit.direction,
                                   before_exit.remaining_volume,0.0,0.0);
         }
         return;
      }
      if(before_exit.execution_check_pending)
      {
         g_trade_slots[slot].ResolveUncertainExecution(event_text);
         if(event_text!="") Print("V3109_C32_SLOT|slot=",slot,"|",event_text);
         return;
      }
   }
   else if(before_exit.exit_mode==EXIT_MODE_LEGACY)
   {
      const bool legacy_ok=g_trade_slots[slot].ManageLegacyTP1(bid,ask,event_text);
      if(event_text!="")
      {
         const TradeRuntimeState legacy_state=g_trade_slots[slot].State();
         RecordObservationEvent(legacy_ok ? "position_update" : "exit_execution_error",
                                before_exit.signal_id,"position",legacy_ok ? "tp1" : "error",
                                event_text,legacy_ok ? "Legacy TP1实际持仓变化已确认" :
                                                      "Legacy TP1状态未提前提交",
                                before_exit.order_ticket,before_exit.position_id,0,
                                before_exit.direction,legacy_state.remaining_volume,0.0,0.0);
         if(legacy_ok)
            NotifyEvent("LEGACY_TP1_S"+IntegerToString(slot)+"_"+
                        IntegerToString((int)TimeCurrent()),event_text);
         else
            Print("V3109_C32_SLOT|slot=",slot,"|",event_text);
      }
   }
}

void OnTick()
{
   const datetime server_now=TimeTradeServer();
   MaybeProcessDailyReviewCloseTriggers(server_now,false,"OnTick");
   WeekendGuardStatus weekend_guard=EvaluateWeekendGuard(_Symbol,server_now,
      InpWeekendGuardEnabled,InpWeekendNoNewTradeMinutes,InpWeekendForceCloseMinutes,
      InpWeekendFallbackFridayCloseHour,InpWeekendFallbackFridayCloseMinute);
   g_weekend_status_text=FormatWeekendGuardStatus(weekend_guard);
   RolloverGuardStatus rollover_guard=EvaluateRolloverGuard(server_now,
      InpRolloverGuardEnabled,InpRolloverStartHour,InpRolloverStartMinute,
      InpRolloverEndHour,InpRolloverEndMinute);
   g_rollover_status_text=FormatRolloverGuardStatus(rollover_guard);

   // “跨周遗留敞口”属于启动/换周恢复检查，不应该在每个Tick持续扫描。
   // 持续扫描会让刚刚建立的本周挂单再次进入历史遗留判断链，造成误删。
   const datetime current_week_start=StartOfCurrentTradingWeek(server_now);

   // 换周后清空上周残留的重试节流状态。
   if(g_weekend_force_flat_retry_week_start>0 &&
      current_week_start>0 &&
      g_weekend_force_flat_retry_week_start!=current_week_start)
   {
      g_weekend_force_flat_retry_after=0;
      g_weekend_force_flat_retry_week_start=0;
      g_prior_week_cleanup_pending=false;
   }

   const bool prior_week_check_due=(InpWeekendGuardEnabled && current_week_start>0 &&
                                    g_prior_week_cleanup_checked_week_start!=current_week_start);
   bool prior_week_exposure=false;
   if(prior_week_check_due)
   {
      // 已确认存在上周遗留敞口但市场尚未开放时，不在每个Tick重复扫描和发送平仓请求。
      if(g_prior_week_cleanup_pending &&
         g_weekend_force_flat_retry_after>0 &&
         server_now<g_weekend_force_flat_retry_after)
      {
         UpdateChartStatus();
         return;
      }

      prior_week_exposure=V3109C32HasPriorWeekManagedExposure(server_now);
      g_prior_week_cleanup_pending=prior_week_exposure;
      if(!prior_week_exposure)
      {
         // 本周首次检查已经确认没有上周遗留；立即锁定本周，避免新挂单被再次扫描。
         g_prior_week_cleanup_checked_week_start=current_week_start;
         g_prior_week_cleanup_pending=false;
         g_weekend_force_flat_retry_after=0;
         g_weekend_force_flat_retry_week_start=0;
         Print("[周末风控] 本周跨周遗留检查完成：未发现上周敞口 | WeekStart=",
               TimeToString(current_week_start,TIME_DATE|TIME_SECONDS));
      }
   }

   const bool weekend_force_flat_required=(InpWeekendGuardEnabled &&
                                           (weekend_guard.force_flat || prior_week_exposure));
   if(weekend_force_flat_required)
   {
      // 普通周末强平失败也执行限频，避免市场关闭时每个Tick反复请求交易服务器。
      if(g_weekend_force_flat_retry_after>0 &&
         server_now<g_weekend_force_flat_retry_after)
      {
         UpdateChartStatus();
         return;
      }

      const bool had_pending=V3109C32HasManagedPendingOrders();
      const bool had_positions=V3109C32HasManagedPositions();
      string pending_error="";
      string close_error="";
      const bool pending_ok=(!had_pending || V3109C32CancelAllManagedPending(pending_error));
      const bool close_ok=(!had_positions || V3109C32CloseAllManagedPositions(close_error));
      if(!pending_ok)
      {
         Print("[周末风控异常] ",pending_error);
         RecordObservationEvent("execution_error","","weekend_guard","error",pending_error,"");
      }
      if(!close_ok)
      {
         Print("[周末风控异常] ",close_error);
         RecordObservationEvent("execution_error","","weekend_guard","error",close_error,"");
      }

      if(!pending_ok || !close_ok)
      {
         string combined_error=pending_error;
         if(combined_error!="" && close_error!="") combined_error+=" | ";
         combined_error+=close_error;
         const bool market_closed=(StringFind(combined_error,"Retcode=10018")>=0 ||
                                   StringFind(combined_error,"market closed")>=0 ||
                                   StringFind(combined_error,"Market closed")>=0);
         const int retry_seconds=(market_closed ?
                                  WEEKEND_FORCE_FLAT_MARKET_CLOSED_RETRY_SECONDS :
                                  WEEKEND_FORCE_FLAT_ERROR_RETRY_SECONDS);
         g_weekend_force_flat_retry_after=server_now+retry_seconds;
         g_weekend_force_flat_retry_week_start=current_week_start;
         if(market_closed)
            Print("[周末风控重试] 市场关闭，暂停重复强平请求 | ",retry_seconds,
                  "秒后重试 | NextRetry=",
                  TimeToString(g_weekend_force_flat_retry_after,TIME_DATE|TIME_SECONDS));
         else
            Print("[周末风控重试] 强平请求未完成 | ",retry_seconds,
                  "秒后重试 | NextRetry=",
                  TimeToString(g_weekend_force_flat_retry_after,TIME_DATE|TIME_SECONDS));
      }
      else
      {
         g_weekend_force_flat_retry_after=0;
         g_weekend_force_flat_retry_week_start=0;
      }

      if((had_pending || had_positions) && pending_ok && close_ok)
      {
         const string flat_reason=(prior_week_exposure ? "检测到跨周遗留敞口" : weekend_guard.reason);
         RecordObservationEvent("risk_block","","weekend_guard","force_flat",flat_reason,
                                "已清除本EA挂单和持仓");
         NotifyEvent("WEEKEND_FLAT_"+IntegerToString(ServerDateKey(server_now)),
                     "[周末风控] 已清除本EA挂单和持仓 | "+flat_reason);
      }

      // 只有跨周检查实际完成且清理成功，才标记本周已检查。
      if(prior_week_exposure && pending_ok && close_ok && current_week_start>0)
      {
         g_prior_week_cleanup_checked_week_start=current_week_start;
         g_prior_week_cleanup_pending=false;
      }
      if(g_waiting_pending.active)
      {
         const string message="[待挂信号] 周末强平/跨周清理触发，等待信号作废 | ID="+
            g_waiting_pending.candidate.signal_id+" | 未创建真实订单";
         ClearWaitingPendingSignal("pending_wait_cleared","blocked","周末强平或跨周清理",message);
      }

      UpdateChartStatus();
      return;
   }

   if(InpWeekendGuardEnabled && weekend_guard.no_new_entries && g_waiting_pending.active)
   {
      const string message="[待挂信号] 周末风控进入禁开窗口，等待信号作废 | ID="+
         g_waiting_pending.candidate.signal_id+" | 未创建真实订单";
      ClearWaitingPendingSignal("pending_wait_cleared","blocked",weekend_guard.reason,message);
   }

   if(InpWeekendGuardEnabled && weekend_guard.no_new_entries &&
      V3109C32HasManagedPendingOrders())
   {
      string pending_error="";
      if(!V3109C32CancelAllManagedPending(pending_error))
      {
         Print("[周末风控异常] ",pending_error);
         RecordObservationEvent("execution_error","","weekend_guard","error",pending_error,"");
      }
      else
      {
         RecordObservationEvent("risk_block","","weekend_guard","pending_canceled",
                                weekend_guard.reason,"已进入禁开仓窗口");
         NotifyEvent("WEEKEND_CANCEL_"+IntegerToString(ServerDateKey(server_now)),
                     "[周末风控] 已进入禁开仓窗口，挂单已删除");
      }
   }

   if(InpRolloverGuardEnabled && rollover_guard.block_new_entries && g_waiting_pending.active)
   {
      const string message="[待挂信号] 换日风控进入禁开窗口，等待信号作废 | ID="+
         g_waiting_pending.candidate.signal_id+" | 未创建真实订单";
      ClearWaitingPendingSignal("pending_wait_cleared","blocked",rollover_guard.reason,message);
   }

   if(InpRolloverGuardEnabled && rollover_guard.block_new_entries &&
      V3109C32HasManagedPendingOrders())
   {
      string pending_error="";
      if(!V3109C32CancelAllManagedPending(pending_error))
      {
         Print("[换日风控异常] ",pending_error);
         RecordObservationEvent("execution_error","","rollover_guard","error",pending_error,"");
      }
      else
      {
         RecordObservationEvent("risk_block","","rollover_guard","pending_canceled",
                                rollover_guard.reason,"已进入禁止新开仓窗口");
         NotifyEvent("ROLLOVER_CANCEL_"+IntegerToString(ServerDateKey(server_now)),
                     "[换日风控] 已进入禁止新开仓窗口，挂单已删除");
      }
   }

   const double bid=SymbolInfoDouble(_Symbol,SYMBOL_BID);
   const double ask=SymbolInfoDouble(_Symbol,SYMBOL_ASK);
   for(int slot=0;slot<V3109C32_MAX_SLOTS;slot++)
      V3109C32ManageSlotPosition(slot,bid,ask);
   V3109C32ManageAllSlots(server_now);

   string own_realized_error="";
   if(!EnsureOwnRealizedToday(_Symbol,InpMagicNumber,server_now,own_realized_error))
   {
      const int error_date=ServerDateKey(server_now);
      if(g_independent_risk_history_error_date!=error_date)
      {
         g_independent_risk_history_error_date=error_date;
         Print("[本EA风控异常] ",own_realized_error,
               " | 暂用最近一次可用的本EA已实现盈亏数据");
      }
   }

   if(!g_independent_risk_mode_logged)
   {
      g_independent_risk_mode_logged=true;
      if(UseOwnFloatingForRiskLock())
         Print("[独立风控] 对冲账户：统计本EA已实现盈亏和本EA浮动盈亏 | Symbol=",
               _Symbol," | Magic=",InpMagicNumber);
      else
         Print("[独立风控] 净额账户仅统计本EA已实现盈亏，避免其他EA共享持仓影响 | Mode=",
               IndependentRiskAccountModeLabel()," | Symbol=",_Symbol,
               " | Magic=",InpMagicNumber);
   }

   const double own_floating=CalculateOwnFloatingPnL(_Symbol,InpMagicNumber);
   const double own_strategy_net=g_own_realized_today+own_floating;
   const bool locked=g_risk.UpdateDailyLock(server_now,
      AccountInfoDouble(ACCOUNT_EQUITY),own_strategy_net);
   if(locked)
   {
      const string lock_event_key="LOCK_"+IntegerToString(ServerDateKey(server_now));
      const string lock_message="[本EA风控锁定] 当日禁止新开仓，已有仓位继续管理"+
         " | 本EA损益变化="+DoubleToString(g_risk.StrategyChange(),2)+
         " | 日亏损限额="+DoubleToString(g_risk.DailyLossLimit(),2)+
         " | 已实现="+DoubleToString(g_own_realized_today,2)+
         " | 浮动="+DoubleToString(own_floating,2)+
         " | Mode="+IndependentRiskAccountModeLabel();
      if(NotifyEvent(lock_event_key,lock_message))
      {
         // 与专家日志保持一致：同一天只记录一次风险锁定事件，避免CSV刷屏。
         RecordObservationEvent("risk_block","","risk_lock","locked",
                                "本EA当日亏损达到限制",lock_message);
      }
   }
   const bool new_bar=IsNewBar();
   if(g_waiting_pending.active)
   {
      const bool wait_handled=ProcessWaitingPendingSignal(server_now,weekend_guard,rollover_guard);
      if(wait_handled)
      {
         UpdateChartStatus();
         return;
      }
   }
   if(!new_bar) return;
   string calendar_error="";
   if(!g_calendar.RefreshIfDue(server_now,calendar_error))
   {
      Print("[AI盯盘数据异常] ",calendar_error);
      RecordObservationEvent("execution_error","","calendar","error",calendar_error,"");
   }

   V310ProcessTick(!locked && !weekend_guard.no_new_entries && !rollover_guard.block_new_entries);
   UpdateChartStatus();
   return;

#ifdef V310_ENABLE_LEGACY_STRATEGY // intentionally undefined in V3.10.7

   ScanResult scan;
   ResetScanResult(scan,g_signal_timeframe,iTime(_Symbol,g_signal_timeframe,1));
   MarketSnapshot bars[];
   string error="";
   if(!g_indicators.Load(200,bars,error))
   {
      SetScanResult(scan,SCAN_REJECT,STAGE_ENVIRONMENT,error);
      EmitScanResult(scan);
      return;
   }
   scan.bar_time=bars[ArraySize(bars)-1].time;
   string parallel_audit_error="";
   if(!WriteParallelIndependentInput(bars,bid,ask,locked,parallel_audit_error))
      Print("[Parallel AI审计异常] 独立输入写入失败 | ",parallel_audit_error);

   if(!CanOpenNewTradeNow(weekend_guard))
   {
      SetScanResult(scan,SCAN_SKIP,STAGE_RISK_LOCK,weekend_guard.reason+
                    " | 禁开时间="+TimeToString(weekend_guard.no_new_entry_time,TIME_DATE|TIME_MINUTES));
      EmitScanResult(scan);
      return;
   }
   StructureResult structure;
   if(!g_structure.Analyze(bars,InpPivotLeft,InpPivotRight,structure,error))
   {
      SetScanResult(scan,SCAN_REJECT,STAGE_STRUCTURE,error);
      EmitScanResult(scan);
      return;
   }
   const ENUM_TRADE_DIRECTION direction=(structure.buy_major_valid ? DIR_BUY :
                                         (structure.sell_major_valid ? DIR_SELL : DIR_NONE));
   const TradeRuntimeState active_state=g_trades.State();
   if(active_state.state==STATE_PENDING_ORDER)
   {
      FibResult pending_fib;
      string context_error="";
      string spread_error="";
      const bool direction_ok=(direction==active_state.direction);
      const bool context_analyzed=g_fibonacci.AnalyzeContext(active_state.direction,structure,bars,
         InpMinImpulseATR,InpFibMin,InpFibMax,InpFibInvalidBufferATR,InpSRToleranceATR,
         InpEMANearDistanceUSD,pending_fib,context_error);
      const bool context_ok=(context_analyzed && pending_fib.context_valid &&
                             !pending_fib.structure_invalidated);
      const bool fib_valid=pending_fib.valid;
      const bool spread_ok=g_risk.CheckSpread(bid,ask,bars[ArraySize(bars)-1].atr14,
         InpMaxSpreadATRRatio,InpAbsoluteMaxSpreadPoints,_Point,spread_error);
      const bool pending_ok=IsPendingRouteValid(active_state.signal_route,direction_ok,
                                                context_ok,fib_valid,spread_ok);
      string reason="已有挂单，继续管理";
      if(!pending_ok)
      {
         const string route_label=SignalRouteLabel(active_state.signal_route,
                                                   active_state.direction);
         string failed_components="";
         if(!direction_ok)
            failed_components="Direction";
         if(!context_ok)
         {
            if(failed_components!="") failed_components+=",";
            failed_components+="Context/Structure";
         }
         if(active_state.signal_route==SIGNAL_ROUTE_FIB_PA && !fib_valid)
         {
            if(failed_components!="") failed_components+=",";
            failed_components+="Fib";
         }
         if(!spread_ok)
         {
            if(failed_components!="") failed_components+=",";
            failed_components+="Spread";
         }
         if(active_state.signal_route!=SIGNAL_ROUTE_FIB_PA &&
            active_state.signal_route!=SIGNAL_ROUTE_EMA_H23 &&
            active_state.signal_route!=SIGNAL_ROUTE_BOTH)
         {
            if(failed_components!="") failed_components+=",";
            failed_components+="RouteMissing";
         }
         string cancel_error="";
         if(g_trades.CancelPending(cancel_error))
         {
            Print("[Pending] Route=",route_label," invalid: ",failed_components,
                  "; order canceled");
            reason="Pending route "+route_label+" invalid ("+failed_components+
                   ") and canceled";
         }
         else
         {
            Print("[Exception] Pending route=",route_label," invalid: ",
                  failed_components,"; cancel failed: ",cancel_error);
            reason="Pending route "+route_label+" invalid ("+failed_components+
                   "), but cancel failed: "+cancel_error;
         }
      }
      SetScanResult(scan,SCAN_SKIP,STAGE_EXPOSURE,reason);
      EmitScanResult(scan);
      return;
   }
   const bool structure_trail_active=CanTrailToStructureByState(active_state);
   if(structure_trail_active && active_state.state>=STATE_TP1_REACHED)
   {
      const double structure_price=(active_state.direction==DIR_BUY ? structure.pullback_low.price :
                                                                      structure.pullback_high.price);
      if(g_trades.TrailToStructure(structure_price,
           MathMax(InpStopBufferPoints*_Point,bars[ArraySize(bars)-1].atr14*InpStopBufferATR),event_text))
      {
         const TradeRuntimeState trailed_state=g_trades.State();
         if(trailed_state.exit_mode==EXIT_MODE_STAGED)
            RecordObservationEvent("position_update",trailed_state.signal_id,"position","trail",
                                   event_text,"Runner结构止损更新",trailed_state.order_ticket,
                                   trailed_state.position_id,0,trailed_state.direction,
                                   trailed_state.remaining_volume,trailed_state.stop_loss,0.0);
         NotifyEvent(active_state.signal_id+"_TRAIL_"+IntegerToString((int)scan.bar_time),event_text);
      }
   }
   if(V3109C32HasManagedExposure())
   {
      SetScanResult(scan,SCAN_SKIP,STAGE_EXPOSURE,"已有本EA持仓，继续持仓管理");
      EmitScanResult(scan);
      return;
   }
   if(!CanOpenNewTradeDuringRollover(rollover_guard))
   {
      SetScanResult(scan,SCAN_SKIP,STAGE_RISK_LOCK,rollover_guard.reason+
                    " | 窗口="+RolloverMinuteLabel(rollover_guard.start_minutes)+"-"+
                    RolloverMinuteLabel(rollover_guard.end_minutes)+"（服务器时间）");
      EmitScanResult(scan);
      return;
   }

   if(locked)
   {
      SetScanResult(scan,SCAN_SKIP,STAGE_RISK_LOCK,"本EA日亏损或连续亏损锁定，禁止新开仓");
      EmitScanResult(scan);
      return;
   }
   if(direction==DIR_NONE)
   {
      SetScanResult(scan,SCAN_REJECT,STAGE_TREND,"未形成有效HH/HL或LL/LH趋势方向");
      EmitScanResult(scan);
      return;
   }

   EMAImpulseResult impulse;
   string three_bar_reason="";
   FindThreeBarEMAImpulse(direction,bars,structure,
                          InpMinThreeBarMoveUSD,impulse,three_bar_reason);

   FibResult fib;
   const bool fib_context_analyzed=g_fibonacci.AnalyzeContext(direction,structure,bars,
                                  InpMinImpulseATR,InpFibMin,InpFibMax,
                                  InpFibInvalidBufferATR,InpSRToleranceATR,
                                  InpEMANearDistanceUSD,fib,error);
   if(fib.retracement>0.0) g_parallel_audit_fib_retracement=fib.retracement;
   if(!fib_context_analyzed)
   {
      SetScanResult(scan,SCAN_REJECT,STAGE_STRUCTURE,error);
      EmitScanResult(scan);
      return;
   }
   g_price_action.SetImpulse(direction,DoubleToString(fib.swing_low,_Digits)+"_"+
                                      DoubleToString(fib.swing_high,_Digits));
   const PAResult pa=g_price_action.Analyze(direction,bars,InpMinAttemptSeparationBars,
                                             InpEMANearDistanceUSD,InpPinBarWickBodyRatio,
                                             InpStrongBarBodyRatio);

   CandidateSignal candidate;
   ENUM_SCAN_STAGE reject_stage=STAGE_CANDIDATE;
   if(!g_signal_engine.Evaluate(_Symbol,g_signal_timeframe,direction,bars,structure,fib,impulse,
      three_bar_reason,pa,
      _Point,InpEntryBufferPoints,
      InpEntryBufferATR,InpStopBufferPoints,InpStopBufferATR,InpMaxSLATR,
      InpMinRRToTP1,InpMaxSignalBarUSD,InpEMASignalBarStopUSD,candidate,error,reject_stage))
   {
      SetScanResult(scan,SCAN_REJECT,reject_stage,error);
      EmitScanResult(scan);
      return;
   }
   double candidate_tick_size=SymbolInfoDouble(_Symbol,SYMBOL_TRADE_TICK_SIZE);
   if(candidate_tick_size<=0.0) candidate_tick_size=_Point;
   if(!AlignCandidateTradePrices(candidate,candidate_tick_size,InpMaxSLATR,
                                 InpMinRRToTP1,InpEMAMaxStopExpansionRatio,error))
   {
      SetScanResult(scan,SCAN_REJECT,STAGE_CANDIDATE,error);
      EmitScanResult(scan);
      return;
   }
   if(!g_risk.CheckSpread(bid,ask,candidate.atr14,InpMaxSpreadATRRatio,
                          InpAbsoluteMaxSpreadPoints,_Point,error))
   {
      const double spread_ratio=(candidate.atr14>0.0 ? (ask-bid)/candidate.atr14 : 0.0);
      SetScanResult(scan,SCAN_REJECT,STAGE_SPREAD,
                    FormatSpreadReject(spread_ratio,InpMaxSpreadATRRatio)+" | "+error);
      EmitScanResult(scan);
      return;
   }
   if(!g_processed.Add(candidate.signal_id))
   {
      SetScanResult(scan,SCAN_SKIP,STAGE_DUPLICATE,"SignalID已处理："+candidate.signal_id);
      EmitScanResult(scan);
      return;
   }

   SetScanResult(scan,SCAN_PASS,STAGE_AI,"本地候选已形成，提交AI审核");
   EmitScanResult(scan);
   const string payload=BuildCandidatePayload(_Symbol,g_signal_timeframe,candidate,bars);
   if(InpWriteCandidateCSV) g_csv.WriteCandidate(candidate,_Symbol,ask-bid,payload);
   RecordObservationEvent("candidate",candidate.signal_id,"candidate","pass",
                          "本地候选已形成",payload,0,0,0,candidate.direction,0.0,
                          candidate.planned_entry,0.0);
   NotifyEvent(candidate.signal_id+"_CANDIDATE","[候选信号] ID="+candidate.signal_id+
               " | TF="+SignalTimeframeLabel(g_signal_timeframe)+
               " | Route="+SignalRouteLabel(candidate.signal_route,candidate.direction)+
               " | ThreeBar="+(candidate.three_bar_impulse_valid?"true":"false")+
               " | Fib="+DoubleToString(candidate.fib_retracement,1)+
               " | Attempt="+AttemptLabel(candidate.direction,candidate.h_attempt)+
               " | EMA距离="+DoubleToString(candidate.ema_distance_usd,2)+
               " | RR="+DoubleToString(candidate.rr_to_tp1,2));
   const AIDecision decision=g_ai.Review(candidate,payload);
   g_csv.WriteDecision(candidate,decision);
   const bool ai_approved=IsApproved(decision,InpAIConfidenceThreshold,InpAIFailOpenGrace);
   g_parallel_audit_fib_retracement=candidate.fib_retracement;
   if(decision.is_error)
   {
      RecordObservationEvent("ai_error",candidate.signal_id,"ai","error",
                             decision.reason+"（降级放行）",
                             "allow="+(decision.allow_trade?"true":"false")+
                             " | confidence="+IntegerToString(decision.confidence)+
                             " | threshold="+IntegerToString(InpAIConfidenceThreshold)+
                             " | error="+decision.error_code,0,0,0,
                             candidate.direction,0.0,candidate.planned_entry,0.0);
      NotifyEvent(candidate.signal_id+"_AI","[AI结果·降级放行] ID="+candidate.signal_id+
                  " | allow="+(decision.allow_trade?"true":"false")+
                  " | confidence="+IntegerToString(decision.confidence)+" | "+decision.reason);
   }
   else
   {
      RecordObservationEvent(ai_approved ? "ai_allow" : "ai_reject",candidate.signal_id,
                             "ai",ai_approved ? "pass" : "reject",decision.reason,
                             "allow="+(decision.allow_trade?"true":"false")+
                             " | confidence="+IntegerToString(decision.confidence)+
                             " | threshold="+IntegerToString(InpAIConfidenceThreshold),0,0,0,
                             candidate.direction,0.0,candidate.planned_entry,0.0);
      NotifyEvent(candidate.signal_id+"_AI","[AI结果] ID="+candidate.signal_id+
                  " | allow="+(decision.allow_trade?"true":"false")+
                  " | confidence="+IntegerToString(decision.confidence)+" | "+decision.reason);
   }
   const double audit_risk=MathAbs(candidate.planned_entry-candidate.planned_sl);
   const double audit_tp2=(candidate.direction==DIR_BUY ?
      candidate.planned_entry+2.0*audit_risk : candidate.planned_entry-2.0*audit_risk);
   WriteParallelEATrace(scan,"OPEN",candidate.direction,
      SignalRouteLabel(candidate.signal_route,candidate.direction),candidate.planned_entry,
      candidate.planned_sl,candidate.tp1,audit_tp2,candidate.rr_to_tp1,
      (ai_approved ? "PASS" : "FAIL"),decision.confidence,decision.reason,
      (ai_approved ? "PENDING" : "BLOCKED_BY_EA_AI"),
      (ai_approved ? "waiting for execution checks" : "EA AI review rejected"),
      candidate.legacy_sl,candidate.signal_sl,candidate.exit_unit,
      (int)candidate.stop_mode,candidate.expansion_ratio);
   if(!ai_approved) return;

   WeekendGuardStatus pre_order_weekend_guard=EvaluateWeekendGuard(_Symbol,TimeTradeServer(),
      InpWeekendGuardEnabled,InpWeekendNoNewTradeMinutes,InpWeekendForceCloseMinutes,
      InpWeekendFallbackFridayCloseHour,InpWeekendFallbackFridayCloseMinute);
   if(!CanOpenNewTradeNow(pre_order_weekend_guard))
   {
      Print("[周末风控拒绝] AI审核期间已进入禁开仓窗口 | ",pre_order_weekend_guard.reason);
      RecordObservationEvent("risk_block",candidate.signal_id,"weekend_guard","reject",
                             pre_order_weekend_guard.reason,"AI审核期间进入禁开窗口");
      WriteParallelCandidateEATrace(scan,candidate,decision,"PASS","SESSION_CHANGED",
                                    pre_order_weekend_guard.reason);
      return;
   }
   RolloverGuardStatus pre_order_rollover_guard=EvaluateRolloverGuard(TimeTradeServer(),
      InpRolloverGuardEnabled,InpRolloverStartHour,InpRolloverStartMinute,
      InpRolloverEndHour,InpRolloverEndMinute);
   if(!CanOpenNewTradeDuringRollover(pre_order_rollover_guard))
   {
      Print("[换日风控拒绝] AI审核期间已进入禁止新开仓窗口 | ",
            pre_order_rollover_guard.reason);
      RecordObservationEvent("risk_block",candidate.signal_id,"rollover_guard","reject",
                             pre_order_rollover_guard.reason,"AI审核期间进入换日禁开窗口");
      WriteParallelCandidateEATrace(scan,candidate,decision,"PASS","SESSION_CHANGED",
                                    pre_order_rollover_guard.reason);
      return;
   }

   // AI返回后必须重新读取实时Tick，不再使用提交AI前缓存的bid/ask。
   MqlTick latest_tick;
   if(!SymbolInfoTick(_Symbol,latest_tick) || latest_tick.bid<=0.0 || latest_tick.ask<=0.0)
   {
      Print("[下单拒绝] AI返回后无法获取最新Bid/Ask");
      RecordObservationEvent("execution_error",candidate.signal_id,"pre_order","error",
                             "AI返回后无法获取最新Bid/Ask","未提交挂单");
      WriteParallelCandidateEATrace(scan,candidate,decision,"PASS","BROKER_RESTRICTION",
                                    "AI返回后无法获取最新Bid/Ask");
      return;
   }
   const double latest_bid=latest_tick.bid;
   const double latest_ask=latest_tick.ask;
   if(!g_risk.CheckSpread(latest_bid,latest_ask,candidate.atr14,InpMaxSpreadATRRatio,
                          InpAbsoluteMaxSpreadPoints,_Point,error))
   {
      Print("[下单拒绝] AI审核期间点差变化：",error);
      RecordObservationEvent("risk_block",candidate.signal_id,"spread","reject",error,
                             "AI返回后重新检查点差");
      WriteParallelCandidateEATrace(scan,candidate,decision,"PASS","SPREAD_CHANGED",error);
      return;
   }
   const double live_price=(direction==DIR_BUY ? latest_ask : latest_bid);
   if(!g_risk.CheckPostAIMove(direction,candidate.planned_entry,live_price,candidate.atr14,
                              InpMaxPostAIMoveATR,error))
   {
      Print("[下单拒绝] ",error);
      WriteParallelCandidateEATrace(scan,candidate,decision,"PASS","PRICE_MOVED",error);
      return;
   }

   const int stops_level=(int)SymbolInfoInteger(_Symbol,SYMBOL_TRADE_STOPS_LEVEL);
   const double broker_tick_size=SymbolInfoDouble(_Symbol,SYMBOL_TRADE_TICK_SIZE);
   const double price_epsilon=MathMax(_Point,(broker_tick_size>0.0 ? broker_tick_size : _Point))*0.1;
   double min_pending_distance=0.0;
   string distance_reason="";
   const ENUM_PENDING_DISTANCE_STATUS distance_status=EvaluatePendingDistance(
      direction,candidate.planned_entry,latest_bid,latest_ask,_Point,broker_tick_size,
      stops_level,min_pending_distance,distance_reason);
   if(distance_status==PENDING_DISTANCE_ENTRY_PASSED)
   {
      Print("[下单拒绝] ",distance_reason);
      WriteParallelCandidateEATrace(scan,candidate,decision,"PASS","ENTRY_PASSED",distance_reason);
      return;
   }
   if(distance_status==PENDING_DISTANCE_INVALID)
   {
      Print("[下单拒绝] ",distance_reason);
      WriteParallelCandidateEATrace(scan,candidate,decision,"PASS","BROKER_RESTRICTION",distance_reason);
      return;
   }
   const datetime expiry=TimeTradeServer()+InpPendingExpiryBars*PeriodSeconds(g_signal_timeframe);
   if(distance_status==PENDING_DISTANCE_TOO_CLOSE)
   {
      ArmWaitingPendingSignal(candidate,decision,expiry,latest_bid,latest_ask,
                              stops_level,min_pending_distance);
      WriteParallelCandidateEATrace(scan,candidate,decision,"PASS","BROKER_RESTRICTION",
                                    "挂单距离不足，进入等待");
      UpdateChartStatus();
      return;
   }
   if(broker_tick_size>0.0)
   {
      const double aligned_entry=MathRound(candidate.planned_entry/broker_tick_size)*broker_tick_size;
      if(MathAbs(aligned_entry-candidate.planned_entry)>price_epsilon)
      {
         const string align_reason="价格对齐后挂单价仍不符合最小价格步长";
         Print("[下单拒绝] ",align_reason," | Entry=",candidate.planned_entry,
               " TickSize=",broker_tick_size);
         WriteParallelCandidateEATrace(scan,candidate,decision,"PASS","BROKER_RESTRICTION",
                                       align_reason);
         return;
      }
   }
   const double tick_size=broker_tick_size;
   double tick_value=SymbolInfoDouble(_Symbol,SYMBOL_TRADE_TICK_VALUE_LOSS);
   if(tick_value<=0.0) tick_value=SymbolInfoDouble(_Symbol,SYMBOL_TRADE_TICK_VALUE);
   const double volume=CalculateRiskVolume(AccountInfoDouble(ACCOUNT_EQUITY),InpRiskPercent,
      candidate.planned_entry,candidate.planned_sl,tick_size,tick_value,
      SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_MIN),SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_MAX),
      SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_STEP),error);
   if(volume<=0.0)
   {
      Print("[风控拒绝] ",error);
      WriteParallelCandidateEATrace(scan,candidate,decision,"PASS","BROKER_RESTRICTION",error);
      return;
   }
   if(g_trades.PlacePending(candidate,volume,expiry,error))
   {
      const TradeRuntimeState pending_state=g_trades.State();
      RecordObservationEvent("pending_created",candidate.signal_id,"order","pass",
                             "挂单创建成功","expiry="+ObservationTimeText(expiry),
                             pending_state.order_ticket,pending_state.position_id,0,
                             candidate.direction,volume,candidate.planned_entry,0.0);
      if(InpLarkOpenNotifyEnabled)
      {
         LarkTradeContext lark_ctx;
         BuildLarkTradeContext(candidate,decision,pending_state.order_ticket,lark_ctx);
         string lark_context_error="";
         if(!SaveLarkTradeContext(lark_ctx,lark_context_error))
            Print("[Lark通知异常] 开仓原因快照保存失败 | SignalID=",candidate.signal_id,
                  " | Error=",lark_context_error,"；EA交易继续运行");
      }
      NotifyEvent(candidate.signal_id+"_PENDING","[挂单] 成功 | ID="+candidate.signal_id);
      WriteParallelEATrace(scan,"OPEN",candidate.direction,
         SignalRouteLabel(candidate.signal_route,candidate.direction),candidate.planned_entry,
      candidate.planned_sl,candidate.tp1,audit_tp2,candidate.rr_to_tp1,
         "PASS",decision.confidence,decision.reason,"PENDING_CREATED","挂单创建成功",
         candidate.legacy_sl,candidate.signal_sl,candidate.exit_unit,
         (int)candidate.stop_mode,candidate.expansion_ratio);
   }
   else
   {
      if(StringFind(error,"挂单距离不足")>=0)
      {
         MqlTick race_tick;
         if(SymbolInfoTick(_Symbol,race_tick) && race_tick.bid>0.0 && race_tick.ask>0.0)
         {
            const int race_stops=(int)SymbolInfoInteger(_Symbol,SYMBOL_TRADE_STOPS_LEVEL);
            const double race_min=MathMax(0.0,(double)race_stops)*_Point;
            ArmWaitingPendingSignal(candidate,decision,expiry,race_tick.bid,race_tick.ask,
                                    race_stops,race_min);
            UpdateChartStatus();
            return;
         }
      }
      RecordObservationEvent("execution_error",candidate.signal_id,"order","error",
                             error,"挂单创建失败",0,0,0,candidate.direction,volume,
                             candidate.planned_entry,0.0);
      NotifyEvent(candidate.signal_id+"_PENDING_FAIL","[挂单] 失败 | "+error);
      WriteParallelEATrace(scan,"OPEN",candidate.direction,
         SignalRouteLabel(candidate.signal_route,candidate.direction),candidate.planned_entry,
      candidate.planned_sl,candidate.tp1,audit_tp2,candidate.rr_to_tp1,
         "PASS",decision.confidence,decision.reason,"ORDER_FAILED",error,
         candidate.legacy_sl,candidate.signal_sl,candidate.exit_unit,
         (int)candidate.stop_mode,candidate.expansion_ratio);
   }
#endif // V310_ENABLE_LEGACY_STRATEGY
}

void OnTradeTransaction(const MqlTradeTransaction &trans,const MqlTradeRequest &request,
                        const MqlTradeResult &result)
{
   if(trans.type==TRADE_TRANSACTION_ORDER_DELETE && trans.order>0)
   {
      long order_magic=0;
      if(OrderSelect(trans.order)) order_magic=OrderGetInteger(ORDER_MAGIC);
      else if(HistoryOrderSelect(trans.order))
         order_magic=HistoryOrderGetInteger(trans.order,ORDER_MAGIC);
      int slot=V3109C32SlotIndexFromMagic(InpMagicNumber,order_magic);
      if(slot<0)
      {
         for(int candidate_slot=0;candidate_slot<V3109C32_MAX_SLOTS;candidate_slot++)
         {
            if(g_trade_slots[candidate_slot].State().order_ticket==trans.order)
            {
               slot=candidate_slot;
               break;
            }
         }
      }
      if(slot<0) return;
      const TradeRuntimeState before_state=g_trade_slots[slot].State();
      g_trade_slots[slot].HandleOrderRemoved(trans.order,trans.order_state);
      if(trans.order_state==ORDER_STATE_EXPIRED || trans.order_state==ORDER_STATE_CANCELED ||
         trans.order_state==ORDER_STATE_REJECTED)
         V3109C32ResetPendingContext(g_v3109_c32_pending_contexts[slot]);
      RecordObservationEvent("pending_closed",before_state.signal_id,"order",
                             "closed",EnumToString(trans.order_state),
                             "挂单删除、到期、取消或成交后移出活动订单",
                             trans.order,before_state.position_id,0,
                             before_state.direction,before_state.remaining_volume,
                             before_state.entry,0.0);
      return;
   }
   if(trans.type!=TRADE_TRANSACTION_DEAL_ADD || trans.deal==0) return;
   if(!HistoryDealSelect(trans.deal)) return;
   const long slot_magic=HistoryDealGetInteger(trans.deal,DEAL_MAGIC);
   const int slot=V3109C32SlotIndexFromMagic(InpMagicNumber,slot_magic);
   if(HistoryDealGetString(trans.deal,DEAL_SYMBOL)!=_Symbol || slot<0) return;

   const TradeRuntimeState before_state=g_trade_slots[slot].State();
   const long entry=HistoryDealGetInteger(trans.deal,DEAL_ENTRY);
   const ulong position_id=(ulong)HistoryDealGetInteger(trans.deal,DEAL_POSITION_ID);
   const double deal_volume=HistoryDealGetDouble(trans.deal,DEAL_VOLUME);
   const double deal_price=HistoryDealGetDouble(trans.deal,DEAL_PRICE);
   const datetime deal_time=(datetime)HistoryDealGetInteger(trans.deal,DEAL_TIME);
   const double deal_net=HistoryDealGetDouble(trans.deal,DEAL_PROFIT)+
                         HistoryDealGetDouble(trans.deal,DEAL_COMMISSION)+
                         HistoryDealGetDouble(trans.deal,DEAL_SWAP)+
                         HistoryDealGetDouble(trans.deal,DEAL_FEE);
   string realized_refresh_error="";
   if(!RefreshOwnRealizedToday(_Symbol,InpMagicNumber,TimeTradeServer(),
                               realized_refresh_error))
      Print("[本EA风控异常] 成交后刷新本EA当日盈亏失败 | ",realized_refresh_error);
   const string deal_comment=HistoryDealGetString(trans.deal,DEAL_COMMENT);
   const long deal_reason_raw=HistoryDealGetInteger(trans.deal,DEAL_REASON);
   string deal_reason=EnumToString((ENUM_DEAL_REASON)deal_reason_raw);
   // 修复：MT5 的 SL/TP 触发平仓，注释固定为 "[sl ...]"/"[tp ...]"。
   // 用它优先判断真实退出原因，避免个别情况 DEAL_REASON 被误报成 CLIENT（手动平仓）。
   if(StringFind(deal_comment,"[sl")==0 || StringFind(deal_comment,"[SL")==0)
      deal_reason="DEAL_REASON_SL";
   else if(StringFind(deal_comment,"[tp")==0 || StringFind(deal_comment,"[TP")==0)
      deal_reason="DEAL_REASON_TP";
   ENUM_TRADE_DIRECTION deal_direction=before_state.direction;
   if(deal_direction==DIR_NONE)
      deal_direction=(HistoryDealGetInteger(trans.deal,DEAL_TYPE)==DEAL_TYPE_BUY ? DIR_BUY : DIR_SELL);

   if(entry==DEAL_ENTRY_IN)
   {
      const V3109C32PendingContext pending_ctx=g_v3109_c32_pending_contexts[slot];
      const bool first_slot_entry=pending_ctx.active;
      const bool countertrend_fill=(first_slot_entry &&
                                    pending_ctx.direction==deal_direction &&
                                    pending_ctx.countertrend);
      if(first_slot_entry)
      {
         V3109C3RegisterFill(g_v3109_c3_quota,countertrend_fill);
         const int direction_index=(deal_direction==DIR_BUY ? 0 : 1);
         V3109C32StartMicroCycle(g_v3109_c32_micro_cycles[direction_index],
            (int)deal_direction,pending_ctx.trend_segment_id,
            pending_ctx.micro_pullback_start_time);
         V3109C32MarkCycleFilled(g_v3109_c32_micro_cycles[direction_index],
                                 pending_ctx.signal_bar_time,pending_ctx.setup_key);
         g_v3109_c32_last_fill_bar_time=pending_ctx.signal_bar_time;
      }
      const double actual_fill_risk=CalculatePlannedRiskMoney(deal_direction,deal_price,
         before_state.initial_sl,deal_volume);
      const double planned_risk=(pending_ctx.planned_risk_usd>0.0 ?
         pending_ctx.planned_risk_usd : actual_fill_risk);
      const double account_equity=AccountInfoDouble(ACCOUNT_EQUITY);
      const double risk_pct=(account_equity>0.0 ? 100.0*planned_risk/account_equity : 0.0);
      const double aggregate_risk=(pending_ctx.aggregate_risk_usd>0.0 ?
         pending_ctx.aggregate_risk_usd : V3109C32AggregateInitialRiskUsd());
      const double aggregate_risk_pct=(account_equity>0.0 ?
         100.0*aggregate_risk/account_equity : 0.0);
      string audit_setup_key=pending_ctx.setup_key;
      StringReplace(audit_setup_key,"|","~");
      Print("V3109_C33TF1_FILL|time=",TimeToString(deal_time,TIME_DATE|TIME_MINUTES),
         "|dir=",DirectionLabel(deal_direction),
         "|class=",(countertrend_fill ? "COUNTERTREND" : "PRIMARY_ALIGNED"),
         "|slot=",IntegerToString(slot),"|slot_magic=",IntegerToString(slot_magic),
         "|position=",IntegerToString((long)position_id),
         "|sl=",DoubleToString(before_state.initial_sl,_Digits),
         "|attempt=",IntegerToString(pending_ctx.attempt_no),
         "|risk_usd=",DoubleToString(planned_risk,8),
         "|risk_pct=",DoubleToString(risk_pct,8),
         "|aggregate_risk_usd=",DoubleToString(aggregate_risk,8),
         "|aggregate_risk_pct=",DoubleToString(aggregate_risk_pct,8),
         "|actual_fill_risk_usd=",DoubleToString(actual_fill_risk,8),
         "|setup_key=",audit_setup_key,
         "|first_slot_entry=",(first_slot_entry ? "true" : "false"),
         "|aligned=",IntegerToString(g_v3109_c3_quota.aligned_fills),
         "|counter=",IntegerToString(g_v3109_c3_quota.counter_fills),
         "|quota=",IntegerToString(V3109C3CounterQuota(g_v3109_c3_quota.aligned_fills)));
      g_v3109_c3_pending_counter=false;
      g_v3109_c3_pending_direction=DIR_NONE;
      g_v3109_c3_pending_attempt=0;
      g_v3109_c3_pending_setup_key="";
      g_trade_slots[slot].MarkFilled();
      const TradeRuntimeState filled_state=g_trade_slots[slot].State();
      V3109C32ResetPendingContext(g_v3109_c32_pending_contexts[slot]);
      string lifecycle_error="";
      if(!WriteTradeLifecycleEvent(filled_state,position_id,trans.deal,"OPEN","OPEN","",
                                   slot_magic,slot,
                                   lifecycle_error))
         Print("[交易生命周期异常] 开仓事实写入失败 | Position=",position_id,
               " | Deal=",trans.deal," | ",lifecycle_error);
      RecordObservationEvent("pending_filled",before_state.signal_id,"order","filled",
                             deal_reason,"挂单已成交并进入持仓管理",before_state.order_ticket,
                             position_id,trans.deal,deal_direction,deal_volume,deal_price,deal_net);
      SendOpenTradeCard(before_state,filled_state,position_id,trans.deal,deal_price,
                        deal_volume,deal_time,slot_magic,slot);
      NotifyEvent("FILL_"+IntegerToString((int)trans.deal),"[成交] 挂单已成交");
   }
   else if(entry==DEAL_ENTRY_OUT || entry==DEAL_ENTRY_OUT_BY)
   {
      const bool position_still_open=g_trade_slots[slot].HasManagedPositions();
      const string lifecycle_stage=LifecycleExitStage(before_state,!position_still_open);
      string lifecycle_error="";
      if(!WriteTradeLifecycleEvent(before_state,position_id,trans.deal,
                                   (position_still_open ? "EXIT" : "FINAL_EXIT"),
                                   lifecycle_stage,
                                   (position_still_open ? "" : deal_reason),
                                   slot_magic,slot,
                                   lifecycle_error))
         Print("[交易生命周期异常] 退出事实写入失败 | Position=",position_id,
               " | Deal=",trans.deal," | ",lifecycle_error);
      if(!position_still_open)
      {
         ClosedTradeFacts closed_facts;
         string aggregate_error="";
         double net=deal_net;
         if(AggregateClosedTradeFacts(_Symbol,slot_magic,position_id,
                                      closed_facts,aggregate_error))
            net=closed_facts.net_profit;
         else
            Print("[最终结算异常] Position=",position_id," | ",aggregate_error,
                  " | 风控暂使用最终Deal净值=",DoubleToString(deal_net,2));
         NotifyEvent("EXIT_"+IntegerToString((int)trans.deal),"[最终退出] 成交Deal="+
                     IntegerToString((int)trans.deal)+" | Position="+
                     IntegerToString((long)position_id)+" | Net="+
                     DoubleToString(net,2)+" "+AccountInfoString(ACCOUNT_CURRENCY));
         SendFinalTradeCard(before_state,position_id,trans.deal,deal_reason,
                            slot_magic,slot);
         const bool now_locked=g_risk.RegisterClosedTradeGroup(net);
         RecordServerSLEvent("FINAL_EXIT",before_state.signal_id,position_id,
                             deal_direction,deal_volume,before_state.initial_sl,
                             before_state.initial_sl,before_state.stop_loss,0.0,0.0,
                             false,0,deal_reason);
         Print("[服务器SL最终] Position=",IntegerToString((long)position_id),
               " | Source=FINAL_EXIT",
               " | LastKnownServerSL=",DoubleToString(before_state.stop_loss,_Digits));
         Print("V3109_C33TF1_FINAL_EXIT|time=",
               TimeToString(deal_time,TIME_DATE|TIME_MINUTES),
               "|slot=",IntegerToString(slot),
               "|slot_magic=",IntegerToString(slot_magic),
               "|position=",IntegerToString((long)position_id));
         g_trade_slots[slot].MarkClosed();
         RecordObservationEvent("position_closed",before_state.signal_id,"position","closed",
                                deal_reason,"本EA持仓已完全退出",before_state.order_ticket,
                                position_id,trans.deal,deal_direction,deal_volume,deal_price,net);
         if(now_locked)
         {
            RecordObservationEvent("risk_block",before_state.signal_id,"risk_lock","locked",
                                   "连续亏损达到3组","当日停止新开仓");
            NotifyEvent("LOSS_LOCK_"+IntegerToString(ServerDateKey(TimeTradeServer())),
                        "[风控锁定] 连续亏损达到3组，当日停止新开仓");
         }
      }
      else
      {
         RecordObservationEvent("position_update",before_state.signal_id,"position","partial_exit",
                                deal_reason,"持仓部分退出或TP1处理",before_state.order_ticket,
                                position_id,trans.deal,deal_direction,deal_volume,deal_price,deal_net);
      }
   }
}
#endif
// ===== END INLINE: XAUUSD_M5_AI_Pullback_V3_8_AI_MONITOR.mq5 =====
