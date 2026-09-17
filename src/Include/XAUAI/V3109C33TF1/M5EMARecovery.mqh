#ifndef XAUAI_V3103_M5_EMA_RECOVERY_MQH
#define XAUAI_V3103_M5_EMA_RECOVERY_MQH
#include "Strategy01Types.mqh"

// Route B measures the M5 trend over one full EMA20 period.  This is a
// strategy invariant derived from the selected EMA, not a Golden-sample
// parameter.
#define V3103_EMA_CYCLE_BARS 20

// This applies only to Route B's directional EMA-recovery candle.  Route A
// signal patterns, RecoveryBreak and StrongBar retain their original 0.75 rule.
#define V3103_EMA_RECOVERY_MIN_CLOSE_LOCATION 0.65

bool V3103BullEMAOverallDirection(const double &cycle_ema[])
{
   if(ArraySize(cycle_ema)<V3103_EMA_CYCLE_BARS)
      return false;
   return cycle_ema[0]>cycle_ema[V3103_EMA_CYCLE_BARS-1]+1e-12;
}

bool V3103BearEMAOverallDirection(const double &cycle_ema[])
{
   if(ArraySize(cycle_ema)<V3103_EMA_CYCLE_BARS)
      return false;
   return cycle_ema[0]<cycle_ema[V3103_EMA_CYCLE_BARS-1]-1e-12;
}

bool V3103HasCycleEMATouch(const V310Bar &cycle_closed[],const double &cycle_ema[],
                           const double ema_tolerance)
{
   if(ArraySize(cycle_closed)<V3103_EMA_CYCLE_BARS || ArraySize(cycle_ema)<V3103_EMA_CYCLE_BARS || ema_tolerance<0.0)
      return false;
   for(int i=0;i<V3103_EMA_CYCLE_BARS;i++)
      if(cycle_closed[i].low<=cycle_ema[i]+ema_tolerance &&
         cycle_closed[i].high>=cycle_ema[i]-ema_tolerance)
         return true;
   return false;
}

bool V3103HasCycleBullEMAPullback(const V310Bar &cycle_closed[],const double &cycle_ema[],
                                  const double ema_tolerance)
{
   if(ArraySize(cycle_closed)<V3103_EMA_CYCLE_BARS || ArraySize(cycle_ema)<V3103_EMA_CYCLE_BARS || ema_tolerance<0.0)
      return false;
   for(int i=0;i<V3103_EMA_CYCLE_BARS;i++)
      if(cycle_closed[i].close<cycle_closed[i].open &&
         cycle_closed[i].low<=cycle_ema[i]+ema_tolerance)
         return true;
   return false;
}

bool V3103HasCycleBearEMARally(const V310Bar &cycle_closed[],const double &cycle_ema[],
                               const double ema_tolerance)
{
   if(ArraySize(cycle_closed)<V3103_EMA_CYCLE_BARS || ArraySize(cycle_ema)<V3103_EMA_CYCLE_BARS || ema_tolerance<0.0)
      return false;
   for(int i=0;i<V3103_EMA_CYCLE_BARS;i++)
      if(cycle_closed[i].close>cycle_closed[i].open &&
         cycle_closed[i].high>=cycle_ema[i]-ema_tolerance)
         return true;
   return false;
}

// The Route B setup begins at the oldest qualifying EMA pullback/rally bar in
// its currently available, already-closed EMA cycle.  It is only a structure
// boundary for TargetSpace; it does not change the recovery signal itself.
datetime V3103BullEMAPullbackStart(const V310Bar &cycle_closed[],const double &cycle_ema[],
                                   const double ema_tolerance)
{
   const int count=MathMin(ArraySize(cycle_closed),ArraySize(cycle_ema));
   if(count<=0 || ema_tolerance<0.0) return 0;
   for(int i=count-1;i>=0;i--)
      if(cycle_closed[i].close<cycle_closed[i].open &&
         cycle_closed[i].low<=cycle_ema[i]+ema_tolerance)
         return cycle_closed[i].time;
   return 0;
}

datetime V3103BearEMARallyStart(const V310Bar &cycle_closed[],const double &cycle_ema[],
                                const double ema_tolerance)
{
   const int count=MathMin(ArraySize(cycle_closed),ArraySize(cycle_ema));
   if(count<=0 || ema_tolerance<0.0) return 0;
   for(int i=count-1;i>=0;i--)
      if(cycle_closed[i].close>cycle_closed[i].open &&
         cycle_closed[i].high>=cycle_ema[i]-ema_tolerance)
         return cycle_closed[i].time;
   return 0;
}

datetime V3103RecentEMAInteractionStart(const V310Bar &recent_closed[],const double &recent_ema[],
                                         const double ema_tolerance)
{
   const int count=MathMin(3,MathMin(ArraySize(recent_closed),ArraySize(recent_ema)));
   if(count<=0 || ema_tolerance<0.0) return 0;
   for(int i=count-1;i>=0;i--)
      if(recent_closed[i].low<=recent_ema[i]+ema_tolerance &&
         recent_closed[i].high>=recent_ema[i]-ema_tolerance)
         return recent_closed[i].time;
   return 0;
}

bool V3103HasRecentEMATouch(const V310Bar &recent_closed[],const double &recent_ema[],
                             const double ema_tolerance)
{
   const int count=MathMin(3,MathMin(ArraySize(recent_closed),ArraySize(recent_ema)));
   if(count<=0 || ema_tolerance<0.0)
      return false;

   for(int i=0;i<count;i++)
      if(recent_closed[i].low<=recent_ema[i]+ema_tolerance
         && recent_closed[i].high>=recent_ema[i]-ema_tolerance)
         return true;
   return false;
}

// Route B owns this check.  It deliberately uses only already-closed M5 bars
// and EMA values, so it does not inherit the legacy H1/H2 impulse state.
bool V3103HasRealBullEMAPullback(const V310Bar &recent_closed[],const double &recent_ema[],
                                 const double ema_tolerance)
{
   const int count=MathMin(3,MathMin(ArraySize(recent_closed),ArraySize(recent_ema)));
   if(count<=0 || ema_tolerance<0.0)
      return false;

   for(int i=0;i<count;i++)
      if(recent_closed[i].close<recent_closed[i].open
         && recent_closed[i].low<=recent_ema[i]+ema_tolerance)
         return true;
   return false;
}

bool V3103HasRealBearEMARally(const V310Bar &recent_closed[],const double &recent_ema[],
                              const double ema_tolerance)
{
   const int count=MathMin(3,MathMin(ArraySize(recent_closed),ArraySize(recent_ema)));
   if(count<=0 || ema_tolerance<0.0)
      return false;

   for(int i=0;i<count;i++)
      if(recent_closed[i].close>recent_closed[i].open
         && recent_closed[i].high>=recent_ema[i]-ema_tolerance)
         return true;
   return false;
}

// A recovery may first reclaim directional control, then break the immediately
// preceding extreme on the next bar.  This is Route B only; Route A is unchanged.
bool V3103BullDirectionalRecovery(const V310Bar &current,const V310Bar &previous,
                                  const V310Bar &recent_closed[],const double tick_size)
{
   if(tick_size<=0.0 || ArraySize(recent_closed)<=0 || current.close<=previous.close)
      return false;
   double pullback_low_close=recent_closed[0].close;
   for(int i=1;i<ArraySize(recent_closed) && i<3;i++)
      pullback_low_close=MathMin(pullback_low_close,recent_closed[i].close);
   return current.close+1e-12>=pullback_low_close+tick_size;
}

bool V3103BearDirectionalRecovery(const V310Bar &current,const V310Bar &previous,
                                  const V310Bar &recent_closed[],const double tick_size)
{
   if(tick_size<=0.0 || ArraySize(recent_closed)<=0 || current.close>=previous.close)
      return false;
   double rally_high_close=recent_closed[0].close;
   for(int i=1;i<ArraySize(recent_closed) && i<3;i++)
      rally_high_close=MathMax(rally_high_close,recent_closed[i].close);
   return current.close-1e-12<=rally_high_close-tick_size;
}

bool V3103BullEMARecovery(const bool m15_allowed,const bool hard_exhaustion,
                          const bool protected_structure_intact,const bool ema_rising,
                          const bool real_pullback,const V310Bar &recent_closed[],
                          const double &recent_ema[],const V310Bar &current,
                          const V310Bar &previous,const double ema_tolerance,
                          const double tick_size)
{
   const double eps=1e-12;
   const double range=V310BarRange(current);
   if(!m15_allowed || hard_exhaustion || !protected_structure_intact || !ema_rising || !real_pullback
      || tick_size<=0.0 || range<=0.0 || current.close<=current.open
      || V310BarBody(current)+eps<tick_size)
      return false;

   const bool previous_bar_break=(current.close+eps>=previous.high+tick_size);
   const bool directional_recovery=V3103BullDirectionalRecovery(current,previous,recent_closed,tick_size);
   return V3103HasCycleEMATouch(recent_closed,recent_ema,ema_tolerance)
      && (previous_bar_break || directional_recovery)
      && V310CloseLocation(current)+eps>=V3103_EMA_RECOVERY_MIN_CLOSE_LOCATION;
}

bool V3103BearEMARecovery(const bool m15_allowed,const bool hard_exhaustion,
                          const bool protected_structure_intact,const bool ema_falling,
                          const bool real_rally,const V310Bar &recent_closed[],
                          const double &recent_ema[],const V310Bar &current,
                          const V310Bar &previous,const double ema_tolerance,
                          const double tick_size)
{
   const double eps=1e-12;
   const double range=V310BarRange(current);
   if(!m15_allowed || hard_exhaustion || !protected_structure_intact || !ema_falling || !real_rally
      || tick_size<=0.0 || range<=0.0 || current.close>=current.open
      || V310BarBody(current)+eps<tick_size)
      return false;

   const bool previous_bar_break=(current.close-eps<=previous.low-tick_size);
   const bool directional_recovery=V3103BearDirectionalRecovery(current,previous,recent_closed,tick_size);
   return V3103HasCycleEMATouch(recent_closed,recent_ema,ema_tolerance)
      && (previous_bar_break || directional_recovery)
      && (current.high-current.close)/range+eps>=V3103_EMA_RECOVERY_MIN_CLOSE_LOCATION;
}

#endif
