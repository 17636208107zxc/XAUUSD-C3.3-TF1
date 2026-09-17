#ifndef XAUAI_V3103_M5_COMPRESSION_BREAK_MQH
#define XAUAI_V3103_M5_COMPRESSION_BREAK_MQH

#include "Strategy01Types.mqh"

// The local consolidation window is exactly the three closed M5 bars before
// the confirmation bar.  At least two must intersect EMA20 ± tolerance.
bool V3103MostlyNearEMA(const V310Bar &local_closed[],const double &local_ema[],
                        const double ema_tolerance)
{
   if(ArraySize(local_closed)<3 || ArraySize(local_ema)<3 || ema_tolerance<0.0)
      return false;
   int near_count=0;
   for(int i=0;i<3;i++)
   {
      if(local_closed[i].low<=local_ema[i]+ema_tolerance &&
         local_closed[i].high>=local_ema[i]-ema_tolerance)
         near_count++;
   }
   return near_count>=2;
}

bool V3103BullCompressionBreak(const bool m15_context_allowed,const bool hard_exhaustion,
                               const bool protected_structure_intact,const bool ema_rising,
                               const bool range_like_state,const V310Bar &local_closed[],
                               const double &local_ema[],const V310Bar &current,
                               const double ema_tolerance,const double tick_size)
{
   if(!m15_context_allowed || hard_exhaustion || !protected_structure_intact ||
      !ema_rising || !range_like_state || tick_size<=0.0 ||
      !V3103MostlyNearEMA(local_closed,local_ema,ema_tolerance))
      return false;
   const double range=V310BarRange(current);
   const double eps=1e-12;
   if(current.close<=current.open || range<=0.0 || V310BarBody(current)+eps<tick_size)
      return false;
   double local_high=local_closed[0].high;
   for(int i=1;i<3;i++) local_high=MathMax(local_high,local_closed[i].high);
   return current.close+eps>=local_high+tick_size;
}

bool V3103BearCompressionBreak(const bool m15_context_allowed,const bool hard_exhaustion,
                               const bool protected_structure_intact,const bool ema_falling,
                               const bool range_like_state,const V310Bar &local_closed[],
                               const double &local_ema[],const V310Bar &current,
                               const double ema_tolerance,const double tick_size)
{
   if(!m15_context_allowed || hard_exhaustion || !protected_structure_intact ||
      !ema_falling || !range_like_state || tick_size<=0.0 ||
      !V3103MostlyNearEMA(local_closed,local_ema,ema_tolerance))
      return false;
   const double range=V310BarRange(current);
   const double eps=1e-12;
   if(current.close>=current.open || range<=0.0 || V310BarBody(current)+eps<tick_size)
      return false;
   double local_low=local_closed[0].low;
   for(int i=1;i<3;i++) local_low=MathMin(local_low,local_closed[i].low);
   return current.close-eps<=local_low-tick_size;
}

#endif
