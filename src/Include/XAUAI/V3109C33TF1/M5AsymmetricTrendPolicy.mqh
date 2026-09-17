#ifndef XAUAI_V3109_C3_M5_ASYMMETRIC_TREND_POLICY_MQH
#define XAUAI_V3109_C3_M5_ASYMMETRIC_TREND_POLICY_MQH

#include "Strategy01Types.mqh"
#include "M15PrimaryStructure.mqh"

struct V3109C3CounterSignal
{
   bool valid;
   double recent_extreme;
   double prior_extreme;
   double neckline;
   string reason;
};

struct V3109C3QuotaState
{
   V3109C21PrimaryDirection primary;
   datetime primary_started_at;
   int aligned_fills;
   int counter_fills;
   datetime exhaustion_started_at;
   bool counter_filled_in_exhaustion;
};

double V3109C3AverageClose(const V310Bar &bars[],const int start,const int count)
{
   if(start<0 || count<=0 || start+count>ArraySize(bars)) return 0.0;
   double sum=0.0;
   for(int i=start;i<start+count;i++) sum+=bars[i].close;
   return sum/count;
}

// A slow M15 background bias prevents a multi-day correction inside a larger
// trend from immediately authorizing the opposite M5 book.  Newest-first bars:
// compare the latest 20-bar mean with the 20-bar mean 460 bars earlier.  This
// measures roughly five trading days of M15 displacement and deliberately
// treats shorter opposite moves as countertrend until the broad bias changes.
int V3109C3M15MacroBias(const V310Bar &bars[])
{
   if(ArraySize(bars)<480) return 0;
   const double recent=V3109C3AverageClose(bars,0,20);
   const double prior=V3109C3AverageClose(bars,460,20);
   if(recent>prior) return 1;
   if(recent<prior) return -1;
   return 0;
}

string V3109C3AttemptKey(const int direction,const datetime pullback_start_time,
                         const int attempt_no)
{
   if((direction!=0 && direction!=1) || pullback_start_time<=0 ||
      attempt_no<1 || attempt_no>2)
      return "";
   return (direction==0 ? "BUY|" : "SELL|")+
      IntegerToString((int)pullback_start_time)+"|A"+IntegerToString(attempt_no);
}

bool V3109C3CanEmitAttempt(const bool pullback_active,const bool pullback_locked,
                           const int attempt_no,const string attempt_key,
                           const string emitted_attempt_key)
{
   return pullback_active && !pullback_locked && attempt_no>=1 && attempt_no<=2 &&
      StringLen(attempt_key)>0 && attempt_key!=emitted_attempt_key;
}

// A bullish EMA recovery is not enough when the signal candle is only a tiny
// bounce or is still extending the pullback materially lower.  The tolerance
// is volatility-relative so this remains causal across price regimes.
bool V3109C31BullRecoveryQuality(const V310Bar &current,const V310Bar &previous,
                                 const double atr,const double tick_size)
{
   if(atr<=0.0 || tick_size<=0.0 || current.close<=current.open ||
      current.close<=previous.close)
      return false;
   const double range=current.high-current.low;
   if(range<=0.0 || (current.close-current.open)/range<0.25)
      return false;
   const double allowed_new_low=MathMax(tick_size,0.20*atr);
   return current.low>=previous.low-allowed_new_low;
}

// A one-left/one-right high is already a known obstacle on the signal close.
// Do not claim one full R of clean space when that obstacle sits closer.
bool V3109C31HasImmediateBullObstacle(const V310Bar &current,
                                      const V310Bar &previous,
                                      const V310Bar &older,
                                      const double entry,const double planned_r)
{
   if(entry<=0.0 || planned_r<=0.0 || previous.high<=entry)
      return false;
   const bool local_pivot=(previous.high>current.high && previous.high>older.high);
   return local_pivot && previous.high-entry<planned_r;
}

// Attempt 2 should have developed a reliable structure.  A previous-bar
// fallback remains valid only when its distance is at least half M15 ATR.
bool V3109C31BullStopReliable(const int attempt_no,const string stop_source,
                              const double entry,const double final_stop,
                              const double m15_atr)
{
   if(entry<=final_stop || m15_atr<=0.0)
      return false;
   if(attempt_no!=2 || stop_source!="PREVIOUS_BAR")
      return true;
   return entry-final_stop>=0.50*m15_atr;
}

int V3109C3CounterQuota(const int aligned_fills)
{
   return MathMax(0,aligned_fills)/3;
}

bool V3109C3CanCounterFill(const V3109C21PrimaryDirection primary,
                           const V3109C21LocalPhase phase,
                           const int aligned_fills,const int counter_fills,
                           const bool counter_filled_in_exhaustion)
{
   const bool directional=(primary==PRIMARY_BULL || primary==PRIMARY_BEAR);
   return directional && phase==LOCAL_EXHAUSTED &&
      !counter_filled_in_exhaustion &&
      counter_fills<V3109C3CounterQuota(aligned_fills);
}

void V3109C3ResetQuotaState(V3109C3QuotaState &state,
                            const V3109C21PrimaryDirection primary,
                            const datetime started_at)
{
   ZeroMemory(state);
   state.primary=primary;
   state.primary_started_at=started_at;
}

void V3109C3ResetQuotaOnPrimaryChange(const V3109C21PrimaryDirection previous_primary,
                                      const V3109C21PrimaryDirection current_primary,
                                      const datetime started_at,
                                      V3109C3QuotaState &state)
{
   if(previous_primary!=current_primary)
      V3109C3ResetQuotaState(state,current_primary,started_at);
}

void V3109C3BeginExhaustion(V3109C3QuotaState &state,const datetime started_at)
{
   if(started_at<=0 || state.exhaustion_started_at==started_at) return;
   state.exhaustion_started_at=started_at;
   state.counter_filled_in_exhaustion=false;
}

void V3109C3RegisterFill(V3109C3QuotaState &state,const bool countertrend)
{
   if(countertrend)
   {
      state.counter_fills++;
      state.counter_filled_in_exhaustion=true;
   }
   else
      state.aligned_fills++;
}

bool V3109C3PivotHigh(const V310Bar &bars[],const int center)
{
   if(center<2 || center+2>=ArraySize(bars)) return false;
   for(int wing=1;wing<=2;wing++)
      if(bars[center].high<=bars[center-wing].high ||
         bars[center].high<=bars[center+wing].high)
         return false;
   return true;
}

bool V3109C3PivotLow(const V310Bar &bars[],const int center)
{
   if(center<2 || center+2>=ArraySize(bars)) return false;
   for(int wing=1;wing<=2;wing++)
      if(bars[center].low>=bars[center-wing].low ||
         bars[center].low>=bars[center+wing].low)
         return false;
   return true;
}

bool V3109C3TwoLatestPivotHighs(const V310Bar &bars[],int &latest_index,
                                int &prior_index)
{
   latest_index=-1; prior_index=-1;
   for(int i=2;i+2<ArraySize(bars);i++)
   {
      if(!V3109C3PivotHigh(bars,i)) continue;
      if(latest_index<0) latest_index=i;
      else { prior_index=i; return true; }
   }
   return false;
}

bool V3109C3TwoLatestPivotLows(const V310Bar &bars[],int &latest_index,
                               int &prior_index)
{
   latest_index=-1; prior_index=-1;
   for(int i=2;i+2<ArraySize(bars);i++)
   {
      if(!V3109C3PivotLow(bars,i)) continue;
      if(latest_index<0) latest_index=i;
      else { prior_index=i; return true; }
   }
   return false;
}

bool V3109C3CounterSellStructure(const V310Bar &bars[],
                                 const V3109C21PrimaryDirection primary,
                                 const V3109C21LocalPhase phase,
                                 const double m5_atr,const double m15_atr,
                                 const double dominant_high,const double tick_size,
                                 V3109C3CounterSignal &out)
{
   ZeroMemory(out);
   if(primary!=PRIMARY_BULL || phase!=LOCAL_EXHAUSTED)
   { out.reason="COUNTER_SELL_WRONG_CONTEXT"; return false; }
   if(ArraySize(bars)<7 || m5_atr<=0.0 || m15_atr<=0.0 ||
      dominant_high<=0.0 || tick_size<=0.0)
   { out.reason="COUNTER_SELL_INVALID_INPUT"; return false; }
   int latest=-1,prior=-1;
   if(!V3109C3TwoLatestPivotHighs(bars,latest,prior))
   { out.reason="COUNTER_SELL_NEEDS_TWO_TOPS"; return false; }
   out.recent_extreme=bars[latest].high;
   out.prior_extreme=bars[prior].high;
   if(MathAbs(out.recent_extreme-dominant_high)>0.75*m15_atr)
   { out.reason="COUNTER_SELL_FAR_FROM_M15_HIGH"; return false; }
   if(out.recent_extreme>out.prior_extreme+0.15*m5_atr)
   { out.reason="COUNTER_SELL_MEANINGFUL_NEW_HIGH"; return false; }
   const V310Bar current=bars[0];
   if(!V310Bearish(current) || V310BodyRangeRatio(current)<0.60 ||
      V310CloseLocation(current)>0.25)
   { out.reason="COUNTER_SELL_WEAK_REVERSAL_BAR"; return false; }
   out.neckline=bars[1].low;
   for(int i=2;i<latest;i++) out.neckline=MathMin(out.neckline,bars[i].low);
   const double break_buffer=MathMax(tick_size,0.10*m5_atr);
   if(current.close>=out.neckline-break_buffer)
   { out.reason="COUNTER_SELL_NECKLINE_HOLDS"; return false; }
   out.valid=true;
   out.reason="COUNTER_SELL_STRICT_STRUCTURE";
   return true;
}

bool V3109C3CounterBuyStructure(const V310Bar &bars[],
                                const V3109C21PrimaryDirection primary,
                                const V3109C21LocalPhase phase,
                                const double m5_atr,const double m15_atr,
                                const double dominant_low,const double tick_size,
                                V3109C3CounterSignal &out)
{
   ZeroMemory(out);
   if(primary!=PRIMARY_BEAR || phase!=LOCAL_EXHAUSTED)
   { out.reason="COUNTER_BUY_WRONG_CONTEXT"; return false; }
   if(ArraySize(bars)<7 || m5_atr<=0.0 || m15_atr<=0.0 ||
      dominant_low<=0.0 || tick_size<=0.0)
   { out.reason="COUNTER_BUY_INVALID_INPUT"; return false; }
   int latest=-1,prior=-1;
   if(!V3109C3TwoLatestPivotLows(bars,latest,prior))
   { out.reason="COUNTER_BUY_NEEDS_TWO_BOTTOMS"; return false; }
   out.recent_extreme=bars[latest].low;
   out.prior_extreme=bars[prior].low;
   if(MathAbs(out.recent_extreme-dominant_low)>0.75*m15_atr)
   { out.reason="COUNTER_BUY_FAR_FROM_M15_LOW"; return false; }
   if(out.recent_extreme<out.prior_extreme-0.15*m5_atr)
   { out.reason="COUNTER_BUY_MEANINGFUL_NEW_LOW"; return false; }
   const V310Bar current=bars[0];
   if(!V310Bullish(current) || V310BodyRangeRatio(current)<0.60 ||
      V310CloseLocation(current)<0.75)
   { out.reason="COUNTER_BUY_WEAK_REVERSAL_BAR"; return false; }
   out.neckline=bars[1].high;
   for(int i=2;i<latest;i++) out.neckline=MathMax(out.neckline,bars[i].high);
   const double break_buffer=MathMax(tick_size,0.10*m5_atr);
   if(current.close<=out.neckline+break_buffer)
   { out.reason="COUNTER_BUY_NECKLINE_HOLDS"; return false; }
   out.valid=true;
   out.reason="COUNTER_BUY_STRICT_STRUCTURE";
   return true;
}

#endif
