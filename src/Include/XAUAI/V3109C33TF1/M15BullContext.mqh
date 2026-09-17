#ifndef XAUAI_V310_M15_BULL_CONTEXT_MQH
#define XAUAI_V310_M15_BULL_CONTEXT_MQH
#include "Strategy01Types.mqh"

void V310EvaluateRangeLike(const V310Bar &bars[],const int min_bars,V310RangeLikeResult &out);

bool V3103ConfirmedBullProtectedBreak(const V310Bar &bars[],const double protected_low,const double atr)
{
   if(ArraySize(bars)<2 || atr<=0.0) return false;
   const double buffered_level=protected_low-0.15*atr;
   return (bars[0].close<buffered_level && bars[1].close<buffered_level);
}

void V310InitM15Metrics(V310M15Metrics &m)
{
   ZeroMemory(m);
   m.trend_phase=V310_PHASE_INVALID;
   m.opposite_close_count=999;
   m.ema_cross_count=999;
}

void V310EvaluateM15(const V310M15Metrics &m,V310M15Result &out)
{
   ZeroMemory(out);
   out.spike_score=(m.move_atr>=2.0)+(m.slope_atr>=0.20)+(m.direction_efficiency>=0.65)
      +(m.breakout_body_atr>=0.80)+(m.breakout_body_range_ratio>=0.60)
      +(m.breakout_close_location>=0.75)+(m.break_distance_atr>=0.10);
   out.spike_path=m.valid_bull_breakout && m.broken_level_count>=2 && m.breakout_follow_through && out.spike_score>=5;
   out.ema_score=(m.same_side_close_ratio>=0.80)+(m.opposite_close_count<=2)
      +(m.ema_cross_count<=2)+(m.ema_slope_atr>=0.03);
   out.ema_path=m.bull_structure && m.ema_slope_atr>0.0 && m.protected_swing_low_intact && out.ema_score>=3;
   const bool climax_no_follow=m.climax_candidate_m15 && m.no_follow_through_m15;
   out.hard_exhaustion=m.failed_breakout_m15 || m.protected_swing_low_broken_m15 || m.m15_range_like || climax_no_follow;
   out.strong_bull_trend=out.spike_path || out.ema_path;
   out.common_gate=m.bull_context && m.protected_swing_low_intact
      && (m.trend_phase==V310_PHASE_EARLY || m.trend_phase==V310_PHASE_MATURE)
      && !out.hard_exhaustion;
   out.allow_m5_scan=out.common_gate && out.strong_bull_trend;
   if(!m.bull_context) out.reason_code="M15_BLOCK_BULL_CONTEXT";
   else if(!m.protected_swing_low_intact) out.reason_code="M15_BLOCK_PROTECTED_LOW";
   else if(m.trend_phase!=V310_PHASE_EARLY && m.trend_phase!=V310_PHASE_MATURE) out.reason_code="M15_BLOCK_TREND_PHASE";
   else if(m.failed_breakout_m15) out.reason_code="M15_HARD_FAILED_BREAKOUT";
   else if(m.protected_swing_low_broken_m15) out.reason_code="M15_HARD_PROTECTED_LOW_BROKEN";
   else if(m.m15_range_like) out.reason_code="M15_HARD_RANGE_LIKE";
   else if(climax_no_follow) out.reason_code="M15_HARD_CLIMAX_NO_FOLLOW_THROUGH";
   else if(!out.strong_bull_trend) out.reason_code="M15_BLOCK_NO_STRONG_BULL_PATH";
   else if(out.spike_path && out.ema_path) out.reason_code="M15_ALLOW_SPIKE_AND_EMA";
   else if(out.spike_path) out.reason_code="M15_ALLOW_SPIKE";
   else out.reason_code="M15_ALLOW_EMA";
}

void V3103EvaluateM15BullContext(const V310M15Metrics &m,V310M15Result &out)
{
   V310EvaluateM15(m,out);
   const bool continuation_memory=(m.bull_structure || m.same_side_close_ratio>=0.80);
   const bool continuation_pause=(m.m15_range_like || (m.climax_candidate_m15 && m.no_follow_through_m15));
   const bool continuation=continuation_pause && m.protected_swing_low_intact &&
      !m.failed_breakout_m15 && !m.protected_swing_low_broken_m15 &&
      m.ema_slope_atr>=0.03 && continuation_memory;
   out.continuation_context=continuation;
   if(continuation)
   {
      out.hard_exhaustion=false;
      out.common_gate=true;
      out.strong_bull_trend=true;
      out.allow_m5_scan=true;
      out.reason_code="M15_ALLOW_BULL_CONTINUATION_PAUSE";
   }
}

double V310ATRFromNewest(const V310Bar &bars[],const int period=14)
{
   if(ArraySize(bars)<period+1) return 0.0;
   double total=0.0;
   for(int i=period-1;i>=0;i--)
      total+=MathMax(bars[i].high-bars[i].low,
         MathMax(MathAbs(bars[i].high-bars[i+1].close),MathAbs(bars[i].low-bars[i+1].close)));
   return total/period;
}

bool V310DeriveM15Metrics(const V310Bar &bars[],V310M15Metrics &m,
                          double &impulse_low,double &impulse_high,double &nearest_resistance)
{
   V310InitM15Metrics(m); impulse_low=0.0; impulse_high=0.0; nearest_resistance=0.0;
   if(ArraySize(bars)<100) return false;
   double ema[]; ArrayResize(ema,100);
   const double alpha=2.0/21.0;
   ema[99]=bars[99].close;
   for(int i=98;i>=0;i--) ema[i]=alpha*bars[i].close+(1.0-alpha)*ema[i+1];
   const double atr=V310ATRFromNewest(bars,14);
   int high_idx[]; int low_idx[]; ArrayResize(high_idx,0); ArrayResize(low_idx,0);
   for(int i=2;i<=97;i++)
   {
      if(bars[i].high>bars[i-1].high && bars[i].high>bars[i-2].high &&
         bars[i].high>bars[i+1].high && bars[i].high>bars[i+2].high)
      { const int n=ArraySize(high_idx); ArrayResize(high_idx,n+1); high_idx[n]=i; }
      if(bars[i].low<bars[i-1].low && bars[i].low<bars[i-2].low &&
         bars[i].low<bars[i+1].low && bars[i].low<bars[i+2].low)
      { const int n=ArraySize(low_idx); ArrayResize(low_idx,n+1); low_idx[n]=i; }
   }
   m.bull_higher_low=ArraySize(low_idx)>=2 && bars[low_idx[0]].low>bars[low_idx[1]].low;
   m.bull_higher_high=ArraySize(high_idx)>=2 && bars[high_idx[0]].high>bars[high_idx[1]].high;
   m.bull_structure=m.bull_higher_low && m.bull_higher_high;
   impulse_low=(ArraySize(low_idx)>0?bars[low_idx[0]].low:bars[19].low);
   for(int i=0;i<20;i++) impulse_low=MathMin(impulse_low,bars[i].low);
   impulse_high=bars[0].high;
   for(int i=0;i<20;i++) impulse_high=MathMax(impulse_high,bars[i].high);
   const double protected_low=(ArraySize(low_idx)>0?bars[low_idx[0]].low:impulse_low);
   m.closed_bar_time=bars[0].time;
   m.closed_bar_close=bars[0].close;
   m.ema20=ema[0];
   m.atr14=atr;
   m.latest_confirmed_high=(ArraySize(high_idx)>0?bars[high_idx[0]].high:0.0);
   m.previous_confirmed_high=(ArraySize(high_idx)>1?bars[high_idx[1]].high:0.0);
   m.latest_confirmed_low=(ArraySize(low_idx)>0?bars[low_idx[0]].low:0.0);
   m.previous_confirmed_low=(ArraySize(low_idx)>1?bars[low_idx[1]].low:0.0);
   m.latest_confirmed_high_time=(ArraySize(high_idx)>0?bars[high_idx[0]].time:0);
   m.previous_confirmed_high_time=(ArraySize(high_idx)>1?bars[high_idx[1]].time:0);
   m.latest_confirmed_low_time=(ArraySize(low_idx)>0?bars[low_idx[0]].time:0);
   m.previous_confirmed_low_time=(ArraySize(low_idx)>1?bars[low_idx[1]].time:0);
   m.protected_level=protected_low;
   m.protected_break_buffer=0.15*atr;
   m.protected_swing_low_broken_m15=V3103ConfirmedBullProtectedBreak(bars,protected_low,atr);
   m.protected_swing_low_intact=!m.protected_swing_low_broken_m15;
   m.bull_context=atr>0.0 && bars[0].close>ema[0];
   m.trend_phase=(m.bull_structure?V310_PHASE_MATURE:V310_PHASE_EARLY);
   int same=0,opposite=0,crosses=0;
   for(int i=0;i<20;i++) { if(bars[i].close>ema[i]) same++; else if(bars[i].close<ema[i]) opposite++; }
   for(int i=0;i<19;i++) if((bars[i].close-ema[i])*(bars[i+1].close-ema[i+1])<0.0) crosses++;
   m.same_side_close_ratio=(double)same/20.0; m.opposite_close_count=opposite; m.ema_cross_count=crosses;
   m.ema_slope_atr=(atr>0.0?(ema[0]-ema[10])/(10.0*atr):0.0);

   V310Bar range_bars[]; ArrayResize(range_bars,20);
   for(int i=0;i<20;i++) range_bars[i]=bars[i];
   V310RangeLikeResult range_result; V310EvaluateRangeLike(range_bars,20,range_result);
   m.m15_range_like=range_result.is_range_like;

   int broken=0; double break_level=0.0;
   for(int i=0;i<ArraySize(high_idx);i++)
   {
      const double level=bars[high_idx[i]].high;
      if(bars[0].close>level)
      {
         bool clustered=false;
         if(break_level>0.0 && atr>0.0) clustered=MathAbs(level-break_level)<=0.15*atr;
         if(!clustered) { broken++; break_level=MathMax(break_level,level); }
      }
      if(level>bars[0].close && (nearest_resistance<=0.0 || level<nearest_resistance)) nearest_resistance=level;
   }
   // An impulse extreme is not necessarily a confirmed structural pivot and
   // therefore must not be promoted into a hard 2R resistance target.
   m.broken_level_count=broken;
   m.valid_bull_breakout=(broken>=2 && break_level>0.0 && bars[1].close>break_level && bars[2].close<=break_level);
   m.breakout_follow_through=m.valid_bull_breakout && bars[0].close>break_level;
   const int origin=(ArraySize(low_idx)>0?low_idx[0]:19);
   const double net=bars[0].close-bars[origin].low;
   double path=0.0; for(int i=origin-1;i>=0;i--) path+=MathAbs(bars[i].close-bars[i+1].close);
   m.move_atr=(atr>0.0?net/atr:0.0);
   m.slope_atr=(atr>0.0?net/(MathMax(1,origin)*atr):0.0);
   m.direction_efficiency=(path>0.0?net/path:0.0);
   m.breakout_body_atr=(atr>0.0?V310BarBody(bars[1])/atr:0.0);
   m.breakout_body_range_ratio=V310BodyRangeRatio(bars[1]);
   m.breakout_close_location=V310CloseLocation(bars[1]);
   m.break_distance_atr=(m.valid_bull_breakout && atr>0.0?(bars[1].close-break_level)/atr:0.0);
   m.failed_breakout_m15=(break_level>0.0 && bars[2].close>break_level && bars[0].close<break_level);
   double bodies[]; ArrayResize(bodies,20); for(int i=2;i<22;i++) bodies[i-2]=V310BarBody(bars[i]);
   ArraySort(bodies); const double median_body=(bodies[9]+bodies[10])*0.5;
   m.climax_candidate_m15=(atr>0.0 && V310BarBody(bars[2])/atr>=1.50) ||
      (median_body>0.0 && V310BarBody(bars[2])>=1.80*median_body);
   const double midpoint=(bars[2].high+bars[2].low)*0.5;
   m.no_follow_through_m15=m.climax_candidate_m15 && bars[1].close<=bars[2].high &&
      bars[0].close<=bars[2].high && (bars[1].close<midpoint || bars[0].close<midpoint);
   return true;
}

#endif
