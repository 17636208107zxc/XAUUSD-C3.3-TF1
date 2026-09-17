#ifndef XAUAI_V310_BULL_SIGNAL_BAR_MQH
#define XAUAI_V310_BULL_SIGNAL_BAR_MQH
#include "M5PullbackState.mqh"

// Pure closed-bar quality check.  The caller owns H2 state and candidate lifecycle.
bool V3103BullRecoveryBreak(const bool already_h2_candidate,const V310Bar &current,
                            const V310Bar &previous,const double tick_size)
{
   if(!already_h2_candidate || tick_size<=0.0)
      return false;

   const double range=V310BarRange(current);
   const double eps=1e-12;
   if(current.close<=current.open || range<=0.0 || V310BarBody(current)+eps<tick_size)
      return false;

   return current.close+eps>=previous.high+tick_size && V310CloseLocation(current)+eps>=0.75;
}

void V310ClassifyBullSignal(const V310Bar &signal,const V310Bar &previous[],V310SignalResult &out)
{
   ZeroMemory(out);
   const double body=V310BarBody(signal),range=V310BarRange(signal);
   const double lower=MathMin(signal.open,signal.close)-signal.low;
   const double upper=signal.high-MathMax(signal.open,signal.close);
   const bool top_third=range>0.0 && MathMin(signal.open,signal.close)>=signal.low+range*(2.0/3.0);
   out.pin=V310Bullish(signal) && body>0.0 && lower>=3.0*body && top_third && upper<=0.25*body;
   out.strong_bull=V310Bullish(signal) && V310BodyRangeRatio(signal)+1e-12>=2.0/3.0
      && upper<=0.5*body && V310CloseLocation(signal)>=0.75;
   bool bears=ArraySize(previous)>=3;
   double body_low=DBL_MAX,body_high=-DBL_MAX;
   for(int i=0;i<3 && bears;i++)
   {
      bears=V310Bearish(previous[i]);
      body_low=MathMin(body_low,MathMin(previous[i].open,previous[i].close));
      body_high=MathMax(body_high,MathMax(previous[i].open,previous[i].close));
   }
   out.three_bear_engulf=V310Bullish(signal) && bears && signal.open<=body_low && signal.close>=body_high
      && V310BodyRangeRatio(signal)+1e-12>=0.60;
   out.valid=out.pin || out.strong_bull || out.three_bear_engulf;
   out.reason_code=out.valid?"SIGNAL_VALID":"SIGNAL_INVALID";
}

#endif
