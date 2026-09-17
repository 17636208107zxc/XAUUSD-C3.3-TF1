#ifndef XAUAI_V3109_C21_M15_TREND_STATE_MQH
#define XAUAI_V3109_C21_M15_TREND_STATE_MQH

#include "Strategy01Types.mqh"
#include "M15PrimaryStructure.mqh"
#include "M15PrimaryEvidence.mqh"

// Compatibility states consumed by the unchanged M5 layer.
enum V3109M15TrendState
{
   M15_RANGE=0,
   M15_EARLY_BULL=1,
   M15_BULL=2,
   M15_LATE_BULL=3,
   M15_TRANSITION=4,
   M15_EARLY_BEAR=5,
   M15_BEAR=6,
   M15_LATE_BEAR=7
};

struct V3109M15TrendContext
{
   V3109M15TrendState state;
   V3109C21PrimaryDirection primary;
   V3109C21LocalPhase local_phase;
   bool allow_buy_scan;
   bool allow_sell_scan;
   bool hard_exhaustion_bull;
   bool hard_exhaustion_bear;
   bool continuation_bull;
   bool continuation_bear;
   bool bull_structure;
   bool bear_structure;
   string reason_buy;
   string reason_sell;
   string primary_reason;
   double promotion_buffer;
   double break_buffer;
};

struct V3109M15TrendMemory
{
   V3109M15TrendState state;
   datetime state_started_at;
   V3109C21PrimaryMemory primary_memory;
};

string V3109M15StateLabel(const V3109M15TrendState state)
{
   switch(state)
   {
      case M15_RANGE: return "RANGE";
      case M15_EARLY_BULL: return "EARLY_BULL";
      case M15_BULL: return "BULL";
      case M15_LATE_BULL: return "LATE_BULL";
      case M15_TRANSITION: return "TRANSITION";
      case M15_EARLY_BEAR: return "EARLY_BEAR";
      case M15_BEAR: return "BEAR";
      case M15_LATE_BEAR: return "LATE_BEAR";
   }
   return "UNKNOWN";
}

bool V3109BullIsLate(const V310M15Metrics &bull)
{
   const bool climax_no_follow=(bull.climax_candidate_m15 && bull.no_follow_through_m15);
   return climax_no_follow || (bull.move_atr>=1.50 && bull.opposite_close_count<=2);
}

bool V3109BearIsLate(const V310M15BearMetrics &bear)
{
   const bool climax_no_follow=(bear.climax_candidate_m15 && bear.no_follow_through_m15);
   return climax_no_follow || (bear.move_atr>=1.50 && bear.opposite_close_count<=2);
}

bool V3109IsBullFamily(const V3109M15TrendState state)
{
   return state==M15_EARLY_BULL || state==M15_BULL || state==M15_LATE_BULL;
}

bool V3109IsBearFamily(const V3109M15TrendState state)
{
   return state==M15_EARLY_BEAR || state==M15_BEAR || state==M15_LATE_BEAR;
}

void V3109ResetM15TrendMemory(V3109M15TrendMemory &memory)
{
   ZeroMemory(memory);
   memory.state=M15_RANGE;
   V3109C21ResetPrimaryMemory(memory.primary_memory);
}

V3109M15TrendState V3109C21LegacyState(const V3109C21PrimaryResult &primary)
{
   if(primary.primary==PRIMARY_BULL)
      return (primary.local_phase==LOCAL_EXHAUSTED?M15_LATE_BULL:M15_BULL);
   if(primary.primary==PRIMARY_BEAR)
      return (primary.local_phase==LOCAL_EXHAUSTED?M15_LATE_BEAR:M15_BEAR);
   if(primary.primary==PRIMARY_TRANSITION_FROM_BULL || primary.primary==PRIMARY_TRANSITION_FROM_BEAR)
      return M15_TRANSITION;
   return M15_RANGE;
}

void V3109C21BuildPrimaryInput(const V310M15Metrics &bull,
                               const V310M15BearMetrics &bear,
                               const V3109C21PrimaryPivots &pivots,
                               const datetime closed_bar_time,
                               const double spread_price,const double tick_size,
                               V3109C21PrimaryInput &in)
{
   ZeroMemory(in);
   in.closed_bar_time=closed_bar_time;
   in.close_price=bull.closed_bar_close;
   in.ema20=bull.ema20;
   in.ema_slope_atr=bull.ema_slope_atr;
   in.atr14=bull.atr14;
   in.spread_price=spread_price;
   in.tick_size=tick_size;
   in.latest_high=pivots.latest_high;
   in.previous_high=pivots.previous_high;
   in.latest_low=pivots.latest_low;
   in.previous_low=pivots.previous_low;
   in.latest_high_time=pivots.latest_high_time;
   in.previous_high_time=pivots.previous_high_time;
   in.latest_low_time=pivots.latest_low_time;
   in.previous_low_time=pivots.previous_low_time;
   in.bull_late=V3109BullIsLate(bull);
   in.bear_late=V3109BearIsLate(bear);
}

void V3109FinalizeM15Trend(const V3109M15TrendMemory &memory,
                           const V3109C21PrimaryResult &primary,
                           const V310M15Metrics &bull,const V310M15BearMetrics &bear,
                           V3109M15TrendContext &out)
{
   ZeroMemory(out);
   out.state=memory.state;
   out.primary=primary.primary;
   out.local_phase=primary.local_phase;
   out.allow_buy_scan=(out.state==M15_BULL && primary.allow_buy);
   out.allow_sell_scan=(out.state==M15_BEAR && primary.allow_sell);
   out.hard_exhaustion_bull=(out.state==M15_LATE_BULL);
   out.hard_exhaustion_bear=(out.state==M15_LATE_BEAR);
   out.continuation_bull=(out.state==M15_LATE_BULL);
   out.continuation_bear=(out.state==M15_LATE_BEAR);
   out.bull_structure=bull.bull_structure;
   out.bear_structure=bear.bear_structure;
   out.primary_reason=primary.reason;
   out.promotion_buffer=primary.promotion_buffer;
   out.break_buffer=primary.break_buffer;

   if(out.state==M15_BULL)
   {
      out.reason_buy="C21_PRIMARY_BULL";
      out.reason_sell="C21_BLOCK_SELL_PRIMARY_BULL";
   }
   else if(out.state==M15_BEAR)
   {
      out.reason_buy="C21_BLOCK_BUY_PRIMARY_BEAR";
      out.reason_sell="C21_PRIMARY_BEAR";
   }
   else if(out.state==M15_LATE_BULL)
   {
      out.reason_buy="C21_LOCAL_EXHAUSTED_BULL";
      out.reason_sell="C21_BLOCK_SELL_PRIMARY_BULL";
   }
   else if(out.state==M15_LATE_BEAR)
   {
      out.reason_buy="C21_BLOCK_BUY_PRIMARY_BEAR";
      out.reason_sell="C21_LOCAL_EXHAUSTED_BEAR";
   }
   else if(out.state==M15_TRANSITION)
   {
      out.reason_buy="C21_PRIMARY_TRANSITION";
      out.reason_sell="C21_PRIMARY_TRANSITION";
   }
   else
   {
      out.reason_buy="C21_PRIMARY_RANGE";
      out.reason_sell="C21_PRIMARY_RANGE";
   }
}

void V3109AdvanceM15Trend(const V310M15Metrics &bull,const V310M15BearMetrics &bear,
                          const V3109C21PrimaryPivots &pivots,
                          const datetime closed_bar_time,const double spread_price,
                          const double tick_size,V3109M15TrendMemory &memory,
                          V3109M15TrendContext &out)
{
   V3109C21PrimaryInput in;
   V3109C21BuildPrimaryInput(bull,bear,pivots,closed_bar_time,spread_price,tick_size,in);
   V3109C21PrimaryResult primary;
   V3109C21AdvancePrimary(in,memory.primary_memory,primary);
   const V3109M15TrendState next=V3109C21LegacyState(primary);
   if(memory.state!=next)
   {
      memory.state=next;
      memory.state_started_at=closed_bar_time;
   }
   V3109FinalizeM15Trend(memory,primary,bull,bear,out);
}

void V3109ApplyTrendToContexts(const V3109M15TrendContext &trend,
                               V310M15Result &buy_ctx,V310M15BearResult &sell_ctx)
{
   buy_ctx.allow_m5_scan=trend.allow_buy_scan;
   sell_ctx.allow_m5_scan=trend.allow_sell_scan;
   buy_ctx.hard_exhaustion=trend.hard_exhaustion_bull;
   sell_ctx.hard_exhaustion=trend.hard_exhaustion_bear;
   buy_ctx.continuation_context=trend.continuation_bull;
   sell_ctx.continuation_context=trend.continuation_bear;
   buy_ctx.reason_code=trend.reason_buy;
   sell_ctx.reason_code=trend.reason_sell;
}

#endif
