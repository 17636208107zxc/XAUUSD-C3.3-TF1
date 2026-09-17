#ifndef XAUAI_V3109_C32_M5_MICRO_CYCLE_POLICY_MQH
#define XAUAI_V3109_C32_M5_MICRO_CYCLE_POLICY_MQH

#define V3109C32_DIR_NONE -1
#define V3109C32_DIR_BUY 0
#define V3109C32_DIR_SELL 1

struct V3109C32MicroCycleState
{
   bool active;
   bool consumed;
   bool locked;
   int direction;
   datetime trend_segment_id;
   datetime pullback_start_time;
   datetime last_signal_bar_time;
   string consumed_setup_key;
};

void V3109C32InitMicroCycle(V3109C32MicroCycleState &state)
{
   ZeroMemory(state);
   state.direction=V3109C32_DIR_NONE;
}

string V3109C32MicroSetupKey(const int direction,
                             const datetime trend_segment_id,
                             const datetime pullback_start_time,
                             const int attempt_no,
                             const datetime signal_bar_time)
{
   if((direction!=V3109C32_DIR_BUY && direction!=V3109C32_DIR_SELL) || trend_segment_id<=0 ||
      pullback_start_time<=0 || attempt_no<1 || attempt_no>2 || signal_bar_time<=0)
      return "";
   return IntegerToString((int)direction)+"|"+
      IntegerToString((long)trend_segment_id)+"|"+
      IntegerToString((long)pullback_start_time)+"|A"+
      IntegerToString(attempt_no)+"|"+
      IntegerToString((long)signal_bar_time);
}

void V3109C32StartMicroCycle(V3109C32MicroCycleState &state,
                             const int direction,
                             const datetime trend_segment_id,
                             const datetime pullback_start_time)
{
   if((direction!=V3109C32_DIR_BUY && direction!=V3109C32_DIR_SELL) || trend_segment_id<=0 ||
      pullback_start_time<=0)
   {
      V3109C32InitMicroCycle(state);
      return;
   }
   const bool same_identity=state.active && state.direction==direction &&
      state.trend_segment_id==trend_segment_id &&
      state.pullback_start_time==pullback_start_time;
   if(same_identity)
      return;
   V3109C32InitMicroCycle(state);
   state.active=true;
   state.direction=direction;
   state.trend_segment_id=trend_segment_id;
   state.pullback_start_time=pullback_start_time;
}

bool V3109C32CanEmitCycle(const V3109C32MicroCycleState &state,
                          const int attempt_no,
                          const datetime signal_bar_time)
{
   return state.active && !state.consumed && !state.locked &&
      (state.direction==V3109C32_DIR_BUY || state.direction==V3109C32_DIR_SELL) &&
      state.trend_segment_id>0 && state.pullback_start_time>0 &&
      attempt_no>=1 && attempt_no<=2 && signal_bar_time>0 &&
      signal_bar_time>state.last_signal_bar_time;
}

void V3109C32MarkCycleFilled(V3109C32MicroCycleState &state,
                             const datetime signal_bar_time,
                             const string setup_key)
{
   if(!state.active || signal_bar_time<=0 || setup_key=="")
      return;
   state.last_signal_bar_time=signal_bar_time;
   state.consumed_setup_key=setup_key;
   state.consumed=true;
}

#endif
