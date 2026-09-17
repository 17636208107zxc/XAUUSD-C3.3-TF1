#ifndef XAUAI_V310_M5_PULLBACK_STATE_MQH
#define XAUAI_V310_M5_PULLBACK_STATE_MQH
#include "Strategy01Types.mqh"

bool V310ConfirmBullImpulse(const V310Bar &break_bar,const V310Bar &follow_bar,const double pullback_start_high,const double atr,const double tick_size)
{
   const double buffer=MathMax(tick_size,0.10*atr);
   return break_bar.close>=pullback_start_high+buffer && V310BodyRangeRatio(break_bar)>=0.60
      && V310CloseLocation(break_bar)>=0.75 && follow_bar.close>=pullback_start_high;
}

bool V310IsPullbackStart(const V310Bar &previous,const V310Bar &current,const double tick_size=0.0)
{
   const double tick=MathMax(0.0,tick_size);
   return current.low<=previous.low-tick+1e-12 && current.close<=previous.close-tick+1e-12
      && (tick>0.0 || current.low<previous.low) && (tick>0.0 || current.close<previous.close);
}

void V310InitPullbackState(V310PullbackState &state)
{
   ZeroMemory(state); state.h_state="NONE"; state.reason_code="PULLBACK_IDLE";
}

void V310AdvancePullbackState(V310PullbackState &state,const string event_name,const datetime event_time,const double event_high=0.0,const bool signal_valid=false)
{
   if(event_name=="NEW_IMPULSE")
   {
      V310InitPullbackState(state); state.impulse_confirmed_time=event_time;
      state.pullback_id="IMP-"+(string)event_time; state.reason_code="NEW_BULL_IMPULSE_CONFIRMED"; return;
   }
   if(state.locked) return;
   if(event_name=="PULLBACK_START")
   {
      state.active=true; state.pullback_id="PB-"+(string)event_time; state.pullback_start_time=event_time;
      state.pullback_start_high=event_high; state.attempt_count=0; state.h_state="NONE";
      state.down_leg_active=true; state.attempt_active=false; state.awaiting_down_leg=false;
      state.reason_code="PULLBACK_STARTED"; return;
   }
   if(!state.active) return;
   if(event_name=="CONTINUE_RECOVERY")
   {
      state.current_attempt_high=MathMax(state.current_attempt_high,event_high); state.reason_code="RECOVERY_ATTEMPT_CONTINUES"; return;
   }
   if(event_name=="NEW_RECOVERY_ATTEMPT")
   {
      state.attempt_count++;
      state.current_attempt_high=event_high;
      state.attempt_active=true; state.down_leg_active=false; state.awaiting_down_leg=false;
      if(state.attempt_count==1) { state.h_state="H1"; state.reason_code="H1_CONFIRMED"; return; }
      if(state.attempt_count==2)
      {
         // Structural H2 is confirmed before its signal-bar classification.
         // Keep existing H2/H2_INVALID state semantics untouched.
         state.h2_candidate_time=event_time;
         state.h_state=signal_valid?"H2":"H2_INVALID_SIGNAL";
         state.h2_signal_time=signal_valid?event_time:0;
         state.reason_code=signal_valid?"H2_SIGNAL_CONFIRMED":"H2_SIGNAL_INVALID"; return;
      }
      state.h_state="H3"; state.locked=true; state.h3_reached=true; state.reason_code="PULLBACK_LOCK_H3"; return;
   }
   if(event_name=="RANGE_LIKE")
   {
      state.locked=true; state.range_like=true; state.reason_code="PULLBACK_LOCK_RANGE_LIKE";
   }
}

void V310AdvanceFromClosedBar(V310PullbackState &state,const V310Bar &previous,const V310Bar &current,const double tick_size,const bool signal_valid=false)
{
   if(state.locked || !state.active) return;
   const double tick=MathMax(0.0,tick_size);
   const bool higher_high=current.high>=previous.high+tick-1e-12;
   const bool higher_close=current.close>=previous.close+tick-1e-12;
   if(state.attempt_active)
   {
      if(higher_high)
      {
         state.current_attempt_high=MathMax(state.current_attempt_high,current.high);
         state.reason_code="RECOVERY_ATTEMPT_CONTINUES"; return;
      }
      const bool next_down=V310IsPullbackStart(previous,current,tick_size);
      state.attempt_active=false; state.down_leg_active=next_down; state.awaiting_down_leg=!next_down;
      state.reason_code=next_down?"NEXT_DOWN_LEG_STARTED":"RECOVERY_ATTEMPT_STOPPED"; return;
   }
   if(state.awaiting_down_leg)
   {
      if(!V310IsPullbackStart(previous,current,tick_size)) return;
      state.down_leg_active=true; state.awaiting_down_leg=false; state.reason_code="NEXT_DOWN_LEG_STARTED"; return;
   }
   if(state.down_leg_active && higher_high && higher_close)
      V310AdvancePullbackState(state,"NEW_RECOVERY_ATTEMPT",current.time,current.high,signal_valid);
}

void V310EvaluateRangeLike(const V310Bar &bars[],const int min_bars,V310RangeLikeResult &out)
{
   ZeroMemory(out);
   if(ArraySize(bars)<min_bars) { out.reason_code="RANGE_INSUFFICIENT_BARS"; return; }
   int pairs=ArraySize(bars)-1,overlap_pass=0;
   double overlap_sum=0.0,path=0.0;
   for(int i=0;i<pairs;i++)
   {
      const double overlap=MathMax(0.0,MathMin(bars[i].high,bars[i+1].high)-MathMax(bars[i].low,bars[i+1].low));
      const double denom=MathMin(V310BarRange(bars[i]),V310BarRange(bars[i+1]));
      const double ratio=denom>0.0?overlap/denom:0.0;
      overlap_sum+=ratio; if(ratio>=0.60) overlap_pass++;
      path+=MathAbs(bars[i+1].close-bars[i].close);
   }
   out.overlap_ratio=pairs>0?overlap_sum/pairs:0.0;
   out.overlap_share=pairs>0?(double)overlap_pass/pairs:0.0;
   const double net=MathAbs(bars[ArraySize(bars)-1].close-bars[0].close);
   out.net_efficiency=path>0.0?net/path:0.0;
   out.is_range_like=out.overlap_share>=0.60 && out.net_efficiency<=0.35;
   out.reason_code=out.is_range_like?"RANGE_LIKE":"RANGE_DIRECTIONAL";
}

#endif
