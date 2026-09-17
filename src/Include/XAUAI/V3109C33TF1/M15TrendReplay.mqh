#ifndef XAUAI_V3109_C33_TF1_M15_TREND_REPLAY_MQH
#define XAUAI_V3109_C33_TF1_M15_TREND_REPLAY_MQH

#include "M15TrendState.mqh"

// One causal, closed-M15 observation used to rebuild the state machine after
// an EA/terminal restart.  Frames must be supplied oldest first.
struct V3109C33TrendReplayFrame
{
   datetime                 closed_bar_time;
   V310M15Metrics           bull;
   V310M15BearMetrics       bear;
   V3109C21PrimaryPivots    pivots;
};

int V3109C33ReplayTrendFrames(const V3109C33TrendReplayFrame &frames[],
                              const double spread_price,const double tick_size,
                              V3109M15TrendMemory &memory,
                              V3109M15TrendContext &trend)
{
   V3109ResetM15TrendMemory(memory);
   ZeroMemory(trend);
   const int total=ArraySize(frames);
   if(total<=0 || tick_size<=0.0) return 0;

   for(int i=0;i<total;i++)
      V3109AdvanceM15Trend(frames[i].bull,frames[i].bear,frames[i].pivots,
                           frames[i].closed_bar_time,spread_price,tick_size,
                           memory,trend);
   return total;
}

#endif
