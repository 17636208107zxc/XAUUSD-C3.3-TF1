#ifndef XAUAI_V3101_BEAR_SIGNAL_BAR_MQH
#define XAUAI_V3101_BEAR_SIGNAL_BAR_MQH
#include "M5BearPullbackState.mqh"

// Pure closed-bar quality check.  The caller owns L2 state and candidate lifecycle.
bool V3103BearRecoveryBreak(const bool already_l2_candidate,const V310Bar &current,
                            const V310Bar &previous,const double tick_size)
{
   if(!already_l2_candidate || tick_size<=0.0)
      return false;

   const double range=V310BarRange(current);
   const double eps=1e-12;
   if(current.close>=current.open || range<=0.0 || V310BarBody(current)+eps<tick_size)
      return false;

   return current.close-eps<=previous.low-tick_size && (current.high-current.close)/range+eps>=0.75;
}

struct V310BearSignalResult
{
   bool valid;
   string signal_type;
   bool pin;
   bool strong_bear;
   bool three_bull_engulf;
   string reason_code;
};

void V3101ClassifyBearSignal(const V310Bar &signal,const V310Bar &previous[],V310BearSignalResult &out)
{
   ZeroMemory(out);
   const double body=V310BarBody(signal),range=V310BarRange(signal);
   const double lower=MathMin(signal.open,signal.close)-signal.low;
   const double upper=signal.high-MathMax(signal.open,signal.close);
   const bool bottom_third=range>0.0 && MathMax(signal.open,signal.close)<=signal.low+range/3.0;
   out.pin=V310Bearish(signal) && body>0.0 && upper>=3.0*body && bottom_third && lower<=0.25*body;
   out.strong_bear=V310Bearish(signal) && V310BodyRangeRatio(signal)+1e-12>=2.0/3.0
      && lower<=0.5*body && V310CloseLocation(signal)<=0.25;
   bool bulls=ArraySize(previous)>=3;
   double body_low=DBL_MAX,body_high=-DBL_MAX;
   for(int i=0;i<3 && bulls;i++)
   {
      bulls=V310Bullish(previous[i]);
      body_low=MathMin(body_low,MathMin(previous[i].open,previous[i].close));
      body_high=MathMax(body_high,MathMax(previous[i].open,previous[i].close));
   }
   out.three_bull_engulf=V310Bearish(signal) && bulls && signal.open>=body_high && signal.close<=body_low
      && V310BodyRangeRatio(signal)+1e-12>=0.60;
   out.valid=out.pin || out.strong_bear || out.three_bull_engulf;
   if(out.pin) out.signal_type="BEAR_PIN";
   else if(out.strong_bear) out.signal_type="STRONG_BEAR";
   else if(out.three_bull_engulf) out.signal_type="ONE_BEAR_ENGULF_THREE_BULL";
   out.reason_code=out.valid?"SIGNAL_VALID":"SIGNAL_INVALID";
}

#endif
