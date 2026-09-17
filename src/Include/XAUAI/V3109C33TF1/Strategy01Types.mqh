#ifndef XAUAI_V310_STRATEGY01_TYPES_MQH
#define XAUAI_V310_STRATEGY01_TYPES_MQH

#define V310_EA_VERSION "3.10933"
#define V310_RULE_VERSION "EA_OPEN_V3_10_9_C33_TF1_PURE_TREND_FREQUENCY_R1"
#define V310_PARAM_VERSION "M15_EARLY_TREND_M5_FREQUENCY_3SLOT_C33TF1"
#define V310_DATA_ROOT "XAU_V3109_C33_TF1"
#define V310_EA_MAGIC 2026091601
#define V310_PARALLEL_AI_MAGIC 2026091601
#define V310_TESTER_MAGIC 2026091602

enum V310TrendPhase
{
   V310_PHASE_INVALID=0,
   V310_PHASE_EARLY=1,
   V310_PHASE_MATURE=2,
   V310_PHASE_EXHAUSTED=3,
   V310_PHASE_TRANSITION=4
};

struct V310Bar
{
   datetime time;
   double open;
   double high;
   double low;
   double close;
};

struct V310RangeLikeResult
{
   bool is_range_like;
   double overlap_ratio;
   double overlap_share;
   double net_efficiency;
   string reason_code;
};

struct V310M15Metrics
{
   bool bull_context;
   bool protected_swing_low_intact;
   V310TrendPhase trend_phase;
   bool valid_bull_breakout;
   int broken_level_count;
   bool breakout_follow_through;
   double move_atr;
   double slope_atr;
   double direction_efficiency;
   double breakout_body_atr;
   double breakout_body_range_ratio;
   double breakout_close_location;
   double break_distance_atr;
   bool bull_structure;
   bool bull_higher_low;
   bool bull_higher_high;
   double ema_slope_atr;
   double same_side_close_ratio;
   int opposite_close_count;
   int ema_cross_count;
   bool failed_breakout_m15;
   bool protected_swing_low_broken_m15;
   bool m15_range_like;
   bool climax_candidate_m15;
   bool no_follow_through_m15;
   datetime closed_bar_time;
   double closed_bar_close;
   double ema20;
   double atr14;
   double latest_confirmed_high;
   double previous_confirmed_high;
   double latest_confirmed_low;
   double previous_confirmed_low;
   datetime latest_confirmed_high_time;
   datetime previous_confirmed_high_time;
   datetime latest_confirmed_low_time;
   datetime previous_confirmed_low_time;
   double protected_level;
   double protected_break_buffer;
};

struct V310M15Result
{
   bool allow_m5_scan;
   bool continuation_context;
   bool common_gate;
   bool strong_bull_trend;
   bool spike_path;
   int spike_score;
   bool ema_path;
   int ema_score;
   bool hard_exhaustion;
   string reason_code;
};

struct V310M15BearMetrics
{
   bool bear_context;
   bool protected_swing_high_intact;
   V310TrendPhase trend_phase;
   bool valid_bear_breakdown;
   int broken_level_count;
   bool breakdown_follow_through;
   double move_atr;
   double slope_atr;
   double direction_efficiency;
   double breakdown_body_atr;
   double breakdown_body_range_ratio;
   double breakdown_close_location;
   double break_distance_atr;
   bool bear_structure;
   bool bear_lower_low;
   bool bear_lower_high;
   double ema_slope_atr;
   double same_side_close_ratio;
   int opposite_close_count;
   int ema_cross_count;
   bool failed_breakdown_m15;
   bool protected_swing_high_broken_m15;
   bool m15_range_like;
   bool climax_candidate_m15;
   bool no_follow_through_m15;
   datetime closed_bar_time;
   double closed_bar_close;
   double ema20;
   double atr14;
   double latest_confirmed_high;
   double previous_confirmed_high;
   double latest_confirmed_low;
   double previous_confirmed_low;
   datetime latest_confirmed_high_time;
   datetime previous_confirmed_high_time;
   datetime latest_confirmed_low_time;
   datetime previous_confirmed_low_time;
   double protected_level;
   double protected_break_buffer;
};

struct V310M15BearResult
{
   bool allow_m5_scan;
   bool continuation_context;
   bool common_gate;
   bool strong_bear_trend;
   bool spike_path;
   int spike_score;
   bool ema_path;
   int ema_score;
   bool hard_exhaustion;
   string reason_code;
};

struct V310PullbackState
{
   bool active;
   bool locked;
   string pullback_id;
   datetime pullback_start_time;
   double pullback_start_high;
   int attempt_count;
   string h_state;
   datetime h2_signal_time;
   datetime h2_candidate_time;
   double current_attempt_high;
   datetime impulse_confirmed_time;
   bool h3_reached;
   bool range_like;
   bool attempt_active;
   bool down_leg_active;
   bool awaiting_down_leg;
   string reason_code;
};

struct V310SignalResult
{
   bool valid;
   string signal_type;
   bool pin;
   bool strong_bull;
   bool three_bear_engulf;
   bool location_fib236;
   bool location_fib382;
   bool location_ema20;
   bool location_breakout_retest;
   bool m2b;
   string reason_code;
};

struct V310LocationLevels
{
   double fib236;
   double fib382;
   double ema20;
   double breakout_retest;
};

struct V310TradePlan
{
   bool valid;
   bool armed;
   double entry;
   double final_stop;
   double planned_r;
   double actual_fill;
   double actual_r;
   double initial_volume;
   double partial_volume;
   double remainder_volume;
   int expiry_bars;
   string reason_code;
};

void V310SetBar(V310Bar &bar,const datetime time,const double open_price,const double high_price,const double low_price,const double close_price)
{
   bar.time=time; bar.open=open_price; bar.high=high_price; bar.low=low_price; bar.close=close_price;
}

double V310BarRange(const V310Bar &bar) { return MathMax(0.0,bar.high-bar.low); }
double V310BarBody(const V310Bar &bar) { return MathAbs(bar.close-bar.open); }
bool V310Bullish(const V310Bar &bar) { return bar.close>bar.open; }
bool V310Bearish(const V310Bar &bar) { return bar.close<bar.open; }
double V310BodyRangeRatio(const V310Bar &bar) { const double range=V310BarRange(bar); return range>0.0?V310BarBody(bar)/range:0.0; }
double V310CloseLocation(const V310Bar &bar) { const double range=V310BarRange(bar); return range>0.0?(bar.close-bar.low)/range:0.0; }

int V310StepDigits(const double step)
{
   int digits=0;
   double scaled=step;
   while(digits<8 && MathAbs(scaled-MathRound(scaled))>1e-9) { scaled*=10.0; digits++; }
   return digits;
}

double V310AlignUp(const double price,const double tick_size)
{
   if(tick_size<=0.0) return 0.0;
   return NormalizeDouble(MathCeil(price/tick_size-1e-10)*tick_size,V310StepDigits(tick_size));
}

double V310AlignDown(const double price,const double tick_size)
{
   if(tick_size<=0.0) return 0.0;
   return NormalizeDouble(MathFloor(price/tick_size+1e-10)*tick_size,V310StepDigits(tick_size));
}

bool V310IsIntegerPrice(const double price,const double tick_size)
{
   return tick_size>0.0 && MathAbs(price-MathRound(price))<=tick_size*0.5+1e-10;
}

double V310VolumeDown(const double volume,const double step)
{
   if(step<=0.0) return 0.0;
   return NormalizeDouble(MathFloor(MathMax(0.0,volume)/step+1e-10)*step,V310StepDigits(step));
}

#endif
