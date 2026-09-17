#ifndef XAUAI_V3101_M5_BEAR_PULLBACK_STATE_MQH
#define XAUAI_V3101_M5_BEAR_PULLBACK_STATE_MQH
#include "Strategy01Types.mqh"

struct V310BearPullbackState
{
   bool active;
   bool locked;
   string pullback_id;
   datetime pullback_start_time;
   double pullback_start_low;
   int attempt_count;
   string h_state;
   datetime h2_signal_time;
   datetime h2_candidate_time;
   double current_attempt_low;
   datetime impulse_confirmed_time;
   bool h3_reached;
   bool range_like;
   bool attempt_active;
   bool up_leg_active;
   bool awaiting_up_leg;
   string reason_code;
};

bool V3101ConfirmBearImpulse(const V310Bar &break_bar,const V310Bar &follow_bar,const double pullback_start_low,const double atr,const double tick_size)
{
   const double buffer=MathMax(tick_size,0.10*atr);
   return break_bar.close<=pullback_start_low-buffer && V310BodyRangeRatio(break_bar)>=0.60
      && V310CloseLocation(break_bar)<=0.25 && follow_bar.close<=pullback_start_low;
}

bool V3101IsRallyStart(const V310Bar &previous,const V310Bar &current,const double tick_size=0.0)
{
   const double tick=MathMax(0.0,tick_size);
   return current.high>=previous.high+tick-1e-12 && current.close>=previous.close+tick-1e-12
      && (tick>0.0 || current.high>previous.high) && (tick>0.0 || current.close>previous.close);
}

void V3101InitBearPullbackState(V310BearPullbackState &state)
{
   ZeroMemory(state); state.h_state="NONE"; state.reason_code="PULLBACK_IDLE";
}

void V3101AdvanceBearPullbackState(V310BearPullbackState &state,const string event_name,const datetime event_time,const double event_low=0.0,const bool signal_valid=false)
{
   if(event_name=="NEW_IMPULSE")
   {
      V3101InitBearPullbackState(state); state.impulse_confirmed_time=event_time;
      state.pullback_id="IMP-S-"+(string)event_time; state.reason_code="NEW_BEAR_IMPULSE_CONFIRMED"; return;
   }
   if(state.locked) return;
   if(event_name=="PULLBACK_START")
   {
      state.active=true; state.pullback_id="PB-S-"+(string)event_time; state.pullback_start_time=event_time;
      state.pullback_start_low=event_low; state.attempt_count=0; state.h_state="NONE";
      state.up_leg_active=true; state.attempt_active=false; state.awaiting_up_leg=false;
      state.reason_code="PULLBACK_STARTED"; return;
   }
   if(!state.active) return;
   if(event_name=="CONTINUE_RECOVERY")
   {
      if(state.current_attempt_low<=0.0) state.current_attempt_low=event_low;
      else state.current_attempt_low=MathMin(state.current_attempt_low,event_low);
      state.reason_code="RECOVERY_ATTEMPT_CONTINUES"; return;
   }
   if(event_name=="NEW_RECOVERY_ATTEMPT")
   {
      state.attempt_count++;
      state.current_attempt_low=event_low;
      state.attempt_active=true; state.up_leg_active=false; state.awaiting_up_leg=false;
      if(state.attempt_count==1) { state.h_state="H1"; state.reason_code="H1_CONFIRMED"; return; }
      if(state.attempt_count==2)
      {
         // Structural L2 is confirmed before its signal-bar classification.
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

void V3101AdvanceBearFromClosedBar(V310BearPullbackState &state,const V310Bar &previous,const V310Bar &current,const double tick_size,const bool signal_valid=false)
{
   if(state.locked || !state.active) return;
   const double tick=MathMax(0.0,tick_size);
   const bool lower_low=current.low<=previous.low-tick+1e-12;
   const bool lower_close=current.close<=previous.close-tick+1e-12;
   if(state.attempt_active)
   {
      if(lower_low)
      {
         if(state.current_attempt_low<=0.0) state.current_attempt_low=current.low;
         else state.current_attempt_low=MathMin(state.current_attempt_low,current.low);
         state.reason_code="RECOVERY_ATTEMPT_CONTINUES"; return;
      }
      const bool next_up=V3101IsRallyStart(previous,current,tick_size);
      state.attempt_active=false; state.up_leg_active=next_up; state.awaiting_up_leg=!next_up;
      state.reason_code=next_up?"NEXT_UP_LEG_STARTED":"RECOVERY_ATTEMPT_STOPPED"; return;
   }
   if(state.awaiting_up_leg)
   {
      if(!V3101IsRallyStart(previous,current,tick_size)) return;
      state.up_leg_active=true; state.awaiting_up_leg=false; state.reason_code="NEXT_UP_LEG_STARTED"; return;
   }
   if(state.up_leg_active && lower_low && lower_close)
      V3101AdvanceBearPullbackState(state,"NEW_RECOVERY_ATTEMPT",current.time,current.low,signal_valid);
}

#endif
