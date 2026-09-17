#ifndef XAUAI_V310_STRATEGY01_PLANNER_MQH
#define XAUAI_V310_STRATEGY01_PLANNER_MQH
#include "BullSignalBar.mqh"

void V310EvaluateLocation(const double price,const double atr,const V310LocationLevels &levels,V310SignalResult &out,const double tolerance_atr=0.15)
{
   ZeroMemory(out);
   const double tolerance=MathMax(0.0,tolerance_atr*atr);
   out.location_fib236=MathAbs(price-levels.fib236)<=tolerance;
   out.location_fib382=MathAbs(price-levels.fib382)<=tolerance;
   out.location_ema20=MathAbs(price-levels.ema20)<=tolerance;
   out.location_breakout_retest=MathAbs(price-levels.breakout_retest)<=tolerance;
   out.m2b=out.location_ema20;
   out.valid=out.location_fib236 || out.location_fib382 || out.location_ema20 || out.location_breakout_retest;
   out.reason_code=out.valid?"LOCATION_VALID":"LOCATION_INVALID";
}

void V310EvaluateLocationBar(const V310Bar &signal,const double atr,const V310LocationLevels &levels,V310SignalResult &out,const double tolerance_atr=0.15)
{
   ZeroMemory(out);
   const double tolerance=MathMax(0.0,tolerance_atr*atr);
   out.location_fib236=(levels.fib236>=signal.low-tolerance && levels.fib236<=signal.high+tolerance);
   out.location_fib382=(levels.fib382>=signal.low-tolerance && levels.fib382<=signal.high+tolerance);
   out.location_ema20=(levels.ema20>=signal.low-tolerance && levels.ema20<=signal.high+tolerance);
   out.location_breakout_retest=(levels.breakout_retest>=signal.low-tolerance && levels.breakout_retest<=signal.high+tolerance);
   out.m2b=out.location_ema20;
   out.valid=out.location_fib236 || out.location_fib382 || out.location_ema20 || out.location_breakout_retest;
   out.reason_code=out.valid?"LOCATION_VALID":"LOCATION_INVALID";
}

void V310BuildTradePlan(const V310Bar &signal,const double stop_anchor,const double tick_size,const double broker_min_distance,const double market_ask,const double target_high,V310TradePlan &out)
{
   ZeroMemory(out); out.expiry_bars=3;
   out.entry=V310AlignUp(signal.high+tick_size,tick_size);
   if(stop_anchor<=0.0) { out.reason_code="PLAN_REJECT_NO_STRUCTURE_STOP"; return; }
   const double base_stop=stop_anchor-2.0;
   out.final_stop=V310AlignDown(base_stop,tick_size);
   out.planned_r=out.entry-out.final_stop;
   if(out.planned_r<=0.0) { out.reason_code="PLAN_REJECT_NONPOSITIVE_R"; return; }
   const bool stop_too_close=(out.entry-out.final_stop<broker_min_distance);
   if(stop_too_close) { out.reason_code="PLAN_REJECT_BROKER_STOP_DISTANCE"; return; }
   if(target_high>0.0 && target_high-out.entry<2.0*out.planned_r) { out.reason_code="PLAN_REJECT_TARGET_SPACE_LT_2R"; return; }
   const bool entry_too_close=(out.entry-market_ask<broker_min_distance);
   out.armed=entry_too_close;
   out.valid=true;
   out.reason_code=entry_too_close?"PLAN_ARMED_WAIT_DISTANCE":"PLAN_VALID";
}

bool V310SizeVolume(const double balance,const double equity,const double risk_percent,const double stop_distance,const double value_per_price_lot,const double volume_min,const double volume_step,double &initial,double &partial,double &remainder)
{
   initial=0.0; partial=0.0; remainder=0.0;
   if(stop_distance<=0.0 || value_per_price_lot<=0.0) return false;
   const double risk_cash=MathMin(balance,equity)*MathMin(MathMax(risk_percent,0.0),1.0)/100.0;
   initial=V310VolumeDown(risk_cash/(stop_distance*value_per_price_lot),volume_step);
   if(initial<2.0*volume_min) return false;
   partial=V310VolumeDown(initial*0.5,volume_step);
   remainder=V310VolumeDown(initial-partial,volume_step);
   return partial>=volume_min && remainder>=volume_min;
}

bool V310ExitLevels(const double actual_fill,const double final_stop,double &actual_r,double &one_r,double &two_r)
{
   actual_r=actual_fill-final_stop;
   if(actual_r<=0.0) { one_r=0.0; two_r=0.0; return false; }
   one_r=actual_fill+actual_r; two_r=actual_fill+2.0*actual_r; return true;
}

#endif
