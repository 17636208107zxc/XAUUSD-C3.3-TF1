#ifndef XAUAI_V3109_C21_M15_PRIMARY_STRUCTURE_MQH
#define XAUAI_V3109_C21_M15_PRIMARY_STRUCTURE_MQH

// Pure closed-M15-bar primary-structure engine.  It owns direction authority;
// local phase is diagnostic and must never grant the opposite direction.

enum V3109C21PrimaryDirection
{
   PRIMARY_RANGE=0,
   PRIMARY_BULL=1,
   PRIMARY_TRANSITION_FROM_BULL=2,
   PRIMARY_BEAR=3,
   PRIMARY_TRANSITION_FROM_BEAR=4
};

enum V3109C21LocalPhase
{
   LOCAL_NEUTRAL=0,
   LOCAL_IMPULSE=1,
   LOCAL_PULLBACK=2,
   LOCAL_RESUME=3,
   LOCAL_EXHAUSTED=4
};

struct V3109C21PrimaryInput
{
   datetime closed_bar_time;
   double close_price;
   double ema20;
   double ema_slope_atr;
   double atr14;
   double spread_price;
   double tick_size;
   double latest_high;
   double previous_high;
   double latest_low;
   double previous_low;
   datetime latest_high_time;
   datetime previous_high_time;
   datetime latest_low_time;
   datetime previous_low_time;
   bool bull_late;
   bool bear_late;
};

struct V3109C21PrimaryMemory
{
   V3109C21PrimaryDirection primary;
   V3109C21LocalPhase local_phase;
   datetime state_started_at;

   double protected_low;
   double protected_high;
   datetime protected_time;
   double dominant_high;
   double dominant_low;
   datetime dominant_time;

   double candidate_low;
   datetime candidate_low_time;
   double candidate_high;
   datetime candidate_high_time;

   int break_close_count;
   double broken_level;
   double prebreak_dominant;
   datetime transition_started_at;

   double reversal_low;
   datetime reversal_low_time;
   double reversal_high;
   datetime reversal_high_time;
   double recovery_low;
   datetime recovery_low_time;
   double recovery_high;
   datetime recovery_high_time;

   datetime last_seen_high_time;
   datetime last_seen_low_time;
};

struct V3109C21PrimaryResult
{
   V3109C21PrimaryDirection primary;
   V3109C21LocalPhase local_phase;
   bool allow_buy;
   bool allow_sell;
   bool changed;
   string reason;
   double promotion_buffer;
   double break_buffer;
};

double V3109C21PromotionBuffer(const double spread_price,const double atr14,const double tick_size)
{
   return MathMax(2.0*MathMax(0.0,spread_price),
                  MathMax(0.10*MathMax(0.0,atr14),2.0*MathMax(0.0,tick_size)));
}

double V3109C21BreakBuffer(const double spread_price,const double atr14,const double tick_size)
{
   return MathMax(2.0*MathMax(0.0,spread_price),
                  MathMax(0.15*MathMax(0.0,atr14),2.0*MathMax(0.0,tick_size)));
}

bool V3109C21AllowsBuy(const V3109C21PrimaryDirection state)
{
   return state==PRIMARY_BULL;
}

bool V3109C21AllowsSell(const V3109C21PrimaryDirection state)
{
   return state==PRIMARY_BEAR;
}

string V3109C21PrimaryLabel(const V3109C21PrimaryDirection state)
{
   switch(state)
   {
      case PRIMARY_RANGE: return "PRIMARY_RANGE";
      case PRIMARY_BULL: return "PRIMARY_BULL";
      case PRIMARY_TRANSITION_FROM_BULL: return "PRIMARY_TRANSITION_FROM_BULL";
      case PRIMARY_BEAR: return "PRIMARY_BEAR";
      case PRIMARY_TRANSITION_FROM_BEAR: return "PRIMARY_TRANSITION_FROM_BEAR";
   }
   return "PRIMARY_UNKNOWN";
}

string V3109C21LocalPhaseLabel(const V3109C21LocalPhase phase)
{
   switch(phase)
   {
      case LOCAL_NEUTRAL: return "LOCAL_NEUTRAL";
      case LOCAL_IMPULSE: return "LOCAL_IMPULSE";
      case LOCAL_PULLBACK: return "LOCAL_PULLBACK";
      case LOCAL_RESUME: return "LOCAL_RESUME";
      case LOCAL_EXHAUSTED: return "LOCAL_EXHAUSTED";
   }
   return "LOCAL_UNKNOWN";
}

void V3109C21ResetPrimaryMemory(V3109C21PrimaryMemory &memory)
{
   ZeroMemory(memory);
   memory.primary=PRIMARY_RANGE;
   memory.local_phase=LOCAL_NEUTRAL;
}

void V3109C21ClearCandidate(V3109C21PrimaryMemory &memory)
{
   memory.candidate_low=0.0;
   memory.candidate_low_time=0;
   memory.candidate_high=0.0;
   memory.candidate_high_time=0;
}

void V3109C21ClearTransitionEvidence(V3109C21PrimaryMemory &memory)
{
   memory.break_close_count=0;
   memory.broken_level=0.0;
   memory.prebreak_dominant=0.0;
   memory.transition_started_at=0;
   memory.reversal_low=0.0;
   memory.reversal_low_time=0;
   memory.reversal_high=0.0;
   memory.reversal_high_time=0;
   memory.recovery_low=0.0;
   memory.recovery_low_time=0;
   memory.recovery_high=0.0;
   memory.recovery_high_time=0;
}

void V3109C21EnterBull(V3109C21PrimaryMemory &memory,const datetime bar_time,
                       const double protected_low,const datetime protected_time,
                       const double dominant_high,const datetime dominant_time)
{
   memory.primary=PRIMARY_BULL;
   memory.local_phase=LOCAL_IMPULSE;
   memory.state_started_at=bar_time;
   memory.protected_low=protected_low;
   memory.protected_high=0.0;
   memory.protected_time=protected_time;
   memory.dominant_high=dominant_high;
   memory.dominant_low=0.0;
   memory.dominant_time=dominant_time;
   V3109C21ClearCandidate(memory);
   V3109C21ClearTransitionEvidence(memory);
}

void V3109C21EnterBear(V3109C21PrimaryMemory &memory,const datetime bar_time,
                       const double protected_high,const datetime protected_time,
                       const double dominant_low,const datetime dominant_time)
{
   memory.primary=PRIMARY_BEAR;
   memory.local_phase=LOCAL_IMPULSE;
   memory.state_started_at=bar_time;
   memory.protected_high=protected_high;
   memory.protected_low=0.0;
   memory.protected_time=protected_time;
   memory.dominant_low=dominant_low;
   memory.dominant_high=0.0;
   memory.dominant_time=dominant_time;
   V3109C21ClearCandidate(memory);
   V3109C21ClearTransitionEvidence(memory);
}

bool V3109C21BullEma(const V3109C21PrimaryInput &in)
{
   return in.close_price>in.ema20 && in.ema_slope_atr>0.0;
}

bool V3109C21BearEma(const V3109C21PrimaryInput &in)
{
   return in.close_price<in.ema20 && in.ema_slope_atr<0.0;
}

void V3109C21FinalizePrimary(const V3109C21PrimaryMemory &memory,
                             const double promotion_buffer,const double break_buffer,
                             const bool changed,const string reason,
                             V3109C21PrimaryResult &out)
{
   out.primary=memory.primary;
   out.local_phase=memory.local_phase;
   out.allow_buy=V3109C21AllowsBuy(memory.primary) && memory.local_phase!=LOCAL_EXHAUSTED;
   out.allow_sell=V3109C21AllowsSell(memory.primary) && memory.local_phase!=LOCAL_EXHAUSTED;
   if(out.allow_buy && out.allow_sell)
   {
      out.allow_buy=false;
      out.allow_sell=false;
   }
   out.changed=changed;
   out.reason=reason;
   out.promotion_buffer=promotion_buffer;
   out.break_buffer=break_buffer;
}

void V3109C21AdvanceBull(const V3109C21PrimaryInput &in,const double promotion_buffer,
                         const double break_buffer,V3109C21PrimaryMemory &memory,
                         bool &changed,string &reason)
{
   const bool new_low=(in.latest_low>0.0 && in.latest_low_time>memory.last_seen_low_time);
   const bool new_high=(in.latest_high>0.0 && in.latest_high_time>memory.last_seen_high_time);

   if(new_low && in.latest_low_time>memory.dominant_time)
   {
      memory.candidate_low=in.latest_low;
      memory.candidate_low_time=in.latest_low_time;
      memory.local_phase=LOCAL_PULLBACK;
      changed=true;
      reason="BULL_CANDIDATE_LOW";
   }

   if(new_high && memory.candidate_low_time>memory.dominant_time &&
      in.latest_high_time>memory.candidate_low_time &&
      in.latest_high>memory.dominant_high+promotion_buffer)
   {
      memory.protected_low=memory.candidate_low;
      memory.protected_time=memory.candidate_low_time;
      memory.dominant_high=in.latest_high;
      memory.dominant_time=in.latest_high_time;
      V3109C21ClearCandidate(memory);
      memory.local_phase=LOCAL_RESUME;
      changed=true;
      reason="BULL_PROMOTE_LOW_ON_NEW_HIGH";
   }

   if(memory.protected_low>0.0 && in.close_price<memory.protected_low-break_buffer)
      memory.break_close_count++;
   else
      memory.break_close_count=0;

   if(memory.break_close_count>=2)
   {
      memory.primary=PRIMARY_TRANSITION_FROM_BULL;
      memory.local_phase=LOCAL_NEUTRAL;
      memory.state_started_at=in.closed_bar_time;
      memory.transition_started_at=in.closed_bar_time;
      memory.broken_level=memory.protected_low;
      memory.prebreak_dominant=memory.dominant_high;
      memory.break_close_count=0;
      V3109C21ClearCandidate(memory);
      changed=true;
      reason="BULL_PROTECTED_LOW_TWO_CLOSE_BREAK";
   }
   else if(in.bull_late)
      memory.local_phase=LOCAL_EXHAUSTED;
   else if(memory.local_phase==LOCAL_EXHAUSTED)
      memory.local_phase=(memory.candidate_low_time>0?LOCAL_PULLBACK:LOCAL_IMPULSE);
}

void V3109C21AdvanceBear(const V3109C21PrimaryInput &in,const double promotion_buffer,
                         const double break_buffer,V3109C21PrimaryMemory &memory,
                         bool &changed,string &reason)
{
   const bool new_high=(in.latest_high>0.0 && in.latest_high_time>memory.last_seen_high_time);
   const bool new_low=(in.latest_low>0.0 && in.latest_low_time>memory.last_seen_low_time);

   if(new_high && in.latest_high_time>memory.dominant_time)
   {
      memory.candidate_high=in.latest_high;
      memory.candidate_high_time=in.latest_high_time;
      memory.local_phase=LOCAL_PULLBACK;
      changed=true;
      reason="BEAR_CANDIDATE_HIGH";
   }

   if(new_low && memory.candidate_high_time>memory.dominant_time &&
      in.latest_low_time>memory.candidate_high_time &&
      in.latest_low<memory.dominant_low-promotion_buffer)
   {
      memory.protected_high=memory.candidate_high;
      memory.protected_time=memory.candidate_high_time;
      memory.dominant_low=in.latest_low;
      memory.dominant_time=in.latest_low_time;
      V3109C21ClearCandidate(memory);
      memory.local_phase=LOCAL_RESUME;
      changed=true;
      reason="BEAR_PROMOTE_HIGH_ON_NEW_LOW";
   }

   if(memory.protected_high>0.0 && in.close_price>memory.protected_high+break_buffer)
      memory.break_close_count++;
   else
      memory.break_close_count=0;

   if(memory.break_close_count>=2)
   {
      memory.primary=PRIMARY_TRANSITION_FROM_BEAR;
      memory.local_phase=LOCAL_NEUTRAL;
      memory.state_started_at=in.closed_bar_time;
      memory.transition_started_at=in.closed_bar_time;
      memory.broken_level=memory.protected_high;
      memory.prebreak_dominant=memory.dominant_low;
      memory.break_close_count=0;
      V3109C21ClearCandidate(memory);
      changed=true;
      reason="BEAR_PROTECTED_HIGH_TWO_CLOSE_BREAK";
   }
   else if(in.bear_late)
      memory.local_phase=LOCAL_EXHAUSTED;
   else if(memory.local_phase==LOCAL_EXHAUSTED)
      memory.local_phase=(memory.candidate_high_time>0?LOCAL_PULLBACK:LOCAL_IMPULSE);
}

void V3109C21AdvanceFromBullTransition(const V3109C21PrimaryInput &in,
                                       const double promotion_buffer,
                                       V3109C21PrimaryMemory &memory,
                                       bool &changed,string &reason)
{
   const bool post_low=(in.latest_low>0.0 && in.latest_low_time>memory.transition_started_at);
   const bool post_high=(in.latest_high>0.0 && in.latest_high_time>memory.transition_started_at);

   if(post_low && in.latest_low_time>memory.reversal_low_time)
   {
      memory.recovery_low=in.latest_low;
      memory.recovery_low_time=in.latest_low_time;
      if(in.latest_low<memory.broken_level-promotion_buffer)
      {
         memory.reversal_low=in.latest_low;
         memory.reversal_low_time=in.latest_low_time;
      }
   }
   if(post_high && memory.reversal_low_time>0 && in.latest_high_time>memory.reversal_low_time &&
      in.latest_high<memory.prebreak_dominant)
   {
      memory.reversal_high=in.latest_high;
      memory.reversal_high_time=in.latest_high_time;
   }
   if(post_high && memory.recovery_low_time>0 && in.latest_high_time>memory.recovery_low_time &&
      in.latest_high>memory.prebreak_dominant+promotion_buffer)
   {
      memory.recovery_high=in.latest_high;
      memory.recovery_high_time=in.latest_high_time;
   }

   if(memory.recovery_high_time>memory.recovery_low_time && V3109C21BullEma(in))
   {
      const double protected_low=memory.recovery_low;
      const datetime protected_time=memory.recovery_low_time;
      const double dominant_high=memory.recovery_high;
      const datetime dominant_time=memory.recovery_high_time;
      V3109C21EnterBull(memory,in.closed_bar_time,protected_low,protected_time,dominant_high,dominant_time);
      changed=true;
      reason="BULL_RECOVERY_NEW_STRUCTURE";
   }
   else if(memory.reversal_high_time>memory.reversal_low_time && V3109C21BearEma(in))
   {
      const double protected_high=memory.reversal_high;
      const datetime protected_time=memory.reversal_high_time;
      const double dominant_low=memory.reversal_low;
      const datetime dominant_time=memory.reversal_low_time;
      V3109C21EnterBear(memory,in.closed_bar_time,protected_high,protected_time,dominant_low,dominant_time);
      changed=true;
      reason="BEAR_REVERSAL_CAUSAL_STRUCTURE";
   }
}

void V3109C21AdvanceFromBearTransition(const V3109C21PrimaryInput &in,
                                       const double promotion_buffer,
                                       V3109C21PrimaryMemory &memory,
                                       bool &changed,string &reason)
{
   const bool post_high=(in.latest_high>0.0 && in.latest_high_time>memory.transition_started_at);
   const bool post_low=(in.latest_low>0.0 && in.latest_low_time>memory.transition_started_at);

   if(post_high && in.latest_high_time>memory.reversal_high_time)
   {
      memory.recovery_high=in.latest_high;
      memory.recovery_high_time=in.latest_high_time;
      if(in.latest_high>memory.broken_level+promotion_buffer)
      {
         memory.reversal_high=in.latest_high;
         memory.reversal_high_time=in.latest_high_time;
      }
   }
   if(post_low && memory.reversal_high_time>0 && in.latest_low_time>memory.reversal_high_time &&
      in.latest_low>memory.prebreak_dominant)
   {
      memory.reversal_low=in.latest_low;
      memory.reversal_low_time=in.latest_low_time;
   }
   if(post_low && memory.recovery_high_time>0 && in.latest_low_time>memory.recovery_high_time &&
      in.latest_low<memory.prebreak_dominant-promotion_buffer)
   {
      memory.recovery_low=in.latest_low;
      memory.recovery_low_time=in.latest_low_time;
   }

   if(memory.recovery_low_time>memory.recovery_high_time && V3109C21BearEma(in))
   {
      const double protected_high=memory.recovery_high;
      const datetime protected_time=memory.recovery_high_time;
      const double dominant_low=memory.recovery_low;
      const datetime dominant_time=memory.recovery_low_time;
      V3109C21EnterBear(memory,in.closed_bar_time,protected_high,protected_time,dominant_low,dominant_time);
      changed=true;
      reason="BEAR_RECOVERY_NEW_STRUCTURE";
   }
   else if(memory.reversal_low_time>memory.reversal_high_time && V3109C21BullEma(in))
   {
      const double protected_low=memory.reversal_low;
      const datetime protected_time=memory.reversal_low_time;
      const double dominant_high=memory.reversal_high;
      const datetime dominant_time=memory.reversal_high_time;
      V3109C21EnterBull(memory,in.closed_bar_time,protected_low,protected_time,dominant_high,dominant_time);
      changed=true;
      reason="BULL_REVERSAL_CAUSAL_STRUCTURE";
   }
}

void V3109C21AdvancePrimary(const V3109C21PrimaryInput &in,
                            V3109C21PrimaryMemory &memory,
                            V3109C21PrimaryResult &out)
{
   const double promotion_buffer=V3109C21PromotionBuffer(in.spread_price,in.atr14,in.tick_size);
   const double break_buffer=V3109C21BreakBuffer(in.spread_price,in.atr14,in.tick_size);
   const V3109C21PrimaryDirection before_primary=memory.primary;
   const V3109C21LocalPhase before_phase=memory.local_phase;
   const double before_protected_low=memory.protected_low;
   const double before_protected_high=memory.protected_high;
   const double before_dominant_high=memory.dominant_high;
   const double before_dominant_low=memory.dominant_low;
   bool changed=false;
   string reason="NO_PRIMARY_CHANGE";

   if(memory.primary==PRIMARY_RANGE)
   {
      const bool bull_order=in.previous_high_time>0 && in.previous_low_time>0 &&
         in.previous_high_time<in.latest_low_time && in.latest_low_time<in.latest_high_time;
      const bool bull_structure=bull_order && in.latest_high>in.previous_high+promotion_buffer &&
         in.latest_low>in.previous_low && V3109C21BullEma(in);
      const bool bear_order=in.previous_low_time>0 && in.previous_high_time>0 &&
         in.previous_low_time<in.latest_high_time && in.latest_high_time<in.latest_low_time;
      const bool bear_structure=bear_order && in.latest_low<in.previous_low-promotion_buffer &&
         in.latest_high<in.previous_high && V3109C21BearEma(in);
      if(bull_structure)
      {
         V3109C21EnterBull(memory,in.closed_bar_time,in.latest_low,in.latest_low_time,
                           in.latest_high,in.latest_high_time);
         changed=true;
         reason="BULL_BOOTSTRAP_ORDERED_HH_HL";
      }
      else if(bear_structure)
      {
         V3109C21EnterBear(memory,in.closed_bar_time,in.latest_high,in.latest_high_time,
                           in.latest_low,in.latest_low_time);
         changed=true;
         reason="BEAR_BOOTSTRAP_ORDERED_LL_LH";
      }
   }
   else if(memory.primary==PRIMARY_BULL)
      V3109C21AdvanceBull(in,promotion_buffer,break_buffer,memory,changed,reason);
   else if(memory.primary==PRIMARY_BEAR)
      V3109C21AdvanceBear(in,promotion_buffer,break_buffer,memory,changed,reason);
   else if(memory.primary==PRIMARY_TRANSITION_FROM_BULL)
      V3109C21AdvanceFromBullTransition(in,promotion_buffer,memory,changed,reason);
   else if(memory.primary==PRIMARY_TRANSITION_FROM_BEAR)
      V3109C21AdvanceFromBearTransition(in,promotion_buffer,memory,changed,reason);

   if(in.latest_high_time>memory.last_seen_high_time)
      memory.last_seen_high_time=in.latest_high_time;
   if(in.latest_low_time>memory.last_seen_low_time)
      memory.last_seen_low_time=in.latest_low_time;

   changed=changed || before_primary!=memory.primary || before_phase!=memory.local_phase ||
      before_protected_low!=memory.protected_low || before_protected_high!=memory.protected_high ||
      before_dominant_high!=memory.dominant_high || before_dominant_low!=memory.dominant_low;
   V3109C21FinalizePrimary(memory,promotion_buffer,break_buffer,changed,reason,out);
}

#endif
