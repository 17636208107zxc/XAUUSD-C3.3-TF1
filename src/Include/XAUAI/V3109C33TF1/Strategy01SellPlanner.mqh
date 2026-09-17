#ifndef XAUAI_V3101_STRATEGY01_SELL_PLANNER_MQH
#define XAUAI_V3101_STRATEGY01_SELL_PLANNER_MQH
#include "BearSignalBar.mqh"

void V3101BuildSellTradePlan(const V310Bar &signal,const double stop_anchor,const double tick_size,const double broker_min_distance,const double market_bid,const double target_low,V310TradePlan &out)
{
   ZeroMemory(out); out.expiry_bars=3;
   out.entry=V310AlignDown(signal.low-tick_size,tick_size);
   if(stop_anchor<=0.0) { out.reason_code="PLAN_REJECT_NO_STRUCTURE_STOP"; return; }
   const double base_stop=stop_anchor+2.0;
   out.final_stop=V310AlignUp(base_stop,tick_size);
   out.planned_r=out.final_stop-out.entry;
   if(out.planned_r<=0.0) { out.reason_code="PLAN_REJECT_NONPOSITIVE_R"; return; }
   const bool stop_too_close=(out.final_stop-out.entry<broker_min_distance);
   if(stop_too_close) { out.reason_code="PLAN_REJECT_BROKER_STOP_DISTANCE"; return; }
   if(target_low>0.0 && out.entry-target_low<2.0*out.planned_r) { out.reason_code="PLAN_REJECT_TARGET_SPACE_LT_2R"; return; }
   const bool entry_too_close=(market_bid-out.entry<broker_min_distance);
   out.armed=entry_too_close;
   out.valid=true;
   out.reason_code=entry_too_close?"PLAN_ARMED_WAIT_DISTANCE":"PLAN_VALID";
}

bool V3101SellExitLevels(const double actual_fill,const double final_stop,double &actual_r,double &one_r,double &two_r)
{
   actual_r=final_stop-actual_fill;
   if(actual_r<=0.0) { one_r=0.0; two_r=0.0; return false; }
   one_r=actual_fill-actual_r; two_r=actual_fill-2.0*actual_r; return true;
}

#endif
