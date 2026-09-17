#ifndef XAUAI_V3103_FAST_EXECUTION_AUDIT_MQH
#define XAUAI_V3103_FAST_EXECUTION_AUDIT_MQH

// Test-only execution-mode classifier.  The production EA does not include
// this file.  Direction follows the audit convention: false=BUY, true=SELL.
enum ENUM_V3103_FAST_EXECUTION_MODE
{
   V3103_FAST_NONE=0,
   V3103_FAST_ENTRY_CROSSED=1,
   V3103_FAST_PRE_BREAK=2
};

struct V3103FastExecutionResult
{
   ENUM_V3103_FAST_EXECUTION_MODE mode;
   double gap_usd;
   double gap_r;
   double actual_r;
};

V3103FastExecutionResult V3103ClassifyFastExecution(const bool sell,
                                                     const double stop_entry,
                                                     const double final_stop,
                                                     const double market_quote,
                                                     const double max_cross_r,
                                                     const double max_prebreak_r)
{
   V3103FastExecutionResult result;
   result.mode=V3103_FAST_NONE;
   result.gap_usd=0.0;
   result.gap_r=0.0;
   result.actual_r=0.0;
   if(stop_entry<=0.0 || final_stop<=0.0 || market_quote<=0.0 ||
      max_cross_r<0.0 || max_prebreak_r<0.0)
      return result;

   const double planned_r=(sell ? final_stop-stop_entry : stop_entry-final_stop);
   const double actual_r=(sell ? final_stop-market_quote : market_quote-final_stop);
   if(planned_r<=0.0 || actual_r<=0.0)
      return result;
   result.actual_r=actual_r;

   const double crossed_gap=(sell ? stop_entry-market_quote : market_quote-stop_entry);
   if(crossed_gap>0.0)
   {
      result.gap_usd=crossed_gap;
      result.gap_r=crossed_gap/planned_r;
      if(result.gap_r<=max_cross_r+1e-12)
         result.mode=V3103_FAST_ENTRY_CROSSED;
      return result;
   }

   const double prebreak_gap=(sell ? market_quote-stop_entry : stop_entry-market_quote);
   if(prebreak_gap>0.0)
   {
      result.gap_usd=prebreak_gap;
      result.gap_r=prebreak_gap/actual_r;
      if(result.gap_r<=max_prebreak_r+1e-12)
         result.mode=V3103_FAST_PRE_BREAK;
   }
   return result;
}

#endif
