#ifndef XAUAI_V3103_M5_TARGET_STRUCTURE_MQH
#define XAUAI_V3103_M5_TARGET_STRUCTURE_MQH

// A structure level is usable only after two closed M5 bars have formed on
// either side of its centre.  This deliberately excludes the just-closed
// pullback bar from becoming a hard forward target.
bool V3103IsConfirmedM5PivotHigh(const V310Bar &bars[],const int center)
{
   if(center<2 || center+2>=ArraySize(bars)) return false;
   const double level=bars[center].high;
   for(int i=1;i<=2;i++)
      if(level<=bars[center-i].high || level<=bars[center+i].high)
         return false;
   return true;
}

bool V3103IsConfirmedM5PivotLow(const V310Bar &bars[],const int center)
{
   if(center<2 || center+2>=ArraySize(bars)) return false;
   const double level=bars[center].low;
   for(int i=1;i<=2;i++)
      if(level>=bars[center-i].low || level>=bars[center+i].low)
         return false;
   return true;
}

// bars[] is a closed-bar series (index 0 is the signal-time latest closed
// bar).  A level may be consumed only by a later CLOSED bar, never by a bar
// after the signal.  The pivot still has to satisfy its original 2L2R test.
bool V3103PivotConsumedByClosedBars(const V310Bar &bars[],const int center,
                                    const bool resistance,const double level,
                                    const double tick_size)
{
   if(center<=0 || center>=ArraySize(bars) || level<=0.0 || tick_size<=0.0)
      return false;

   for(int i=0;i<center;i++)
   {
      if(resistance && bars[i].close>=level+tick_size)
         return true;
      if(!resistance && bars[i].close<=level-tick_size)
         return true;
   }
   return false;
}

bool V3103IsCurrentRouteBInternalPivot(const V310Bar &bar,const datetime setup_anchor,
                                       const bool exclude_setup_internal)
{
   return (exclude_setup_internal && setup_anchor>0 && bar.time>=setup_anchor);
}

double V3103NearestValidBuyM15Resistance(const V310Bar &bars[],const double entry,
                                         const double tick_size,const datetime setup_anchor,
                                         const bool exclude_setup_internal)
{
   double result=0.0;
   for(int i=2;i+2<ArraySize(bars);i++)
   {
      if(!V3103IsConfirmedM5PivotHigh(bars,i) || bars[i].high<=entry)
         continue;
      if(V3103PivotConsumedByClosedBars(bars,i,true,bars[i].high,tick_size))
         continue;
      if(V3103IsCurrentRouteBInternalPivot(bars[i],setup_anchor,exclude_setup_internal))
         continue;
      if(result<=0.0 || bars[i].high<result)
         result=bars[i].high;
   }
   return result;
}

double V3103NearestValidSellM15Support(const V310Bar &bars[],const double entry,
                                       const double tick_size,const datetime setup_anchor,
                                       const bool exclude_setup_internal)
{
   double result=0.0;
   for(int i=2;i+2<ArraySize(bars);i++)
   {
      if(!V3103IsConfirmedM5PivotLow(bars,i) || bars[i].low>=entry)
         continue;
      if(V3103PivotConsumedByClosedBars(bars,i,false,bars[i].low,tick_size))
         continue;
      if(V3103IsCurrentRouteBInternalPivot(bars[i],setup_anchor,exclude_setup_internal))
         continue;
      if(result<=0.0 || bars[i].low>result)
         result=bars[i].low;
   }
   return result;
}

// Current TargetSpace selector.  For Route A, setup_anchor must be zero and
// exclude_setup_internal false.  Route B passes the current EMA setup start
// and true, so only its own pullback/rally pivots are excluded.
double V3103NearestValidBuyResistance(const V310Bar &m5_bars[],const V310Bar &m15_bars[],
                                      const double entry,const double tick_size,
                                      const datetime setup_anchor,const bool exclude_setup_internal)
{
   double result=V3103NearestValidBuyM15Resistance(m15_bars,entry,tick_size,setup_anchor,
                                                    exclude_setup_internal);
   for(int i=2;i+2<ArraySize(m5_bars);i++)
   {
      if(!V3103IsConfirmedM5PivotHigh(m5_bars,i) || m5_bars[i].high<=entry)
         continue;
      if(V3103PivotConsumedByClosedBars(m5_bars,i,true,m5_bars[i].high,tick_size))
         continue;
      if(V3103IsCurrentRouteBInternalPivot(m5_bars[i],setup_anchor,exclude_setup_internal))
         continue;
      if(result<=0.0 || m5_bars[i].high<result)
         result=m5_bars[i].high;
   }
   return result;
}

double V3103NearestValidSellSupport(const V310Bar &m5_bars[],const V310Bar &m15_bars[],
                                    const double entry,const double tick_size,
                                    const datetime setup_anchor,const bool exclude_setup_internal)
{
   double result=V3103NearestValidSellM15Support(m15_bars,entry,tick_size,setup_anchor,
                                                  exclude_setup_internal);
   for(int i=2;i+2<ArraySize(m5_bars);i++)
   {
      if(!V3103IsConfirmedM5PivotLow(m5_bars,i) || m5_bars[i].low>=entry)
         continue;
      if(V3103PivotConsumedByClosedBars(m5_bars,i,false,m5_bars[i].low,tick_size))
         continue;
      if(V3103IsCurrentRouteBInternalPivot(m5_bars[i],setup_anchor,exclude_setup_internal))
         continue;
      if(result<=0.0 || m5_bars[i].low>result)
         result=m5_bars[i].low;
   }
   return result;
}

// V3.10.4: pick the NEAREST valid support that is at least min_distance below
// the entry, instead of always taking the nearest support (which may only offer
// <2R).  Returns 0 when no support can reach the required distance.
double V3103NearestValidSellSupport2R(const V310Bar &m5_bars[],const V310Bar &m15_bars[],
                                      const double entry,const double tick_size,
                                      const double min_distance,
                                      const datetime setup_anchor,const bool exclude_setup_internal)
{
   double result=0.0;
   const int m15_count=ArraySize(m15_bars);
   for(int i=2;i+2<m15_count;i++)
   {
      if(!V3103IsConfirmedM5PivotLow(m15_bars,i) || m15_bars[i].low>=entry) continue;
      if(entry-m15_bars[i].low<min_distance) continue;
      if(V3103PivotConsumedByClosedBars(m15_bars,i,false,m15_bars[i].low,tick_size)) continue;
      if(V3103IsCurrentRouteBInternalPivot(m15_bars[i],setup_anchor,exclude_setup_internal)) continue;
      if(result<=0.0 || m15_bars[i].low>result) result=m15_bars[i].low;
   }
   const int m5_count=ArraySize(m5_bars);
   for(int i=2;i+2<m5_count;i++)
   {
      if(!V3103IsConfirmedM5PivotLow(m5_bars,i) || m5_bars[i].low>=entry) continue;
      if(entry-m5_bars[i].low<min_distance) continue;
      if(V3103PivotConsumedByClosedBars(m5_bars,i,false,m5_bars[i].low,tick_size)) continue;
      if(V3103IsCurrentRouteBInternalPivot(m5_bars[i],setup_anchor,exclude_setup_internal)) continue;
      if(result<=0.0 || m5_bars[i].low>result) result=m5_bars[i].low;
   }
   return result;
}

// V3.10.2-compatible selector retained solely for Route A regression
// diagnostics.  The production V3.10.3 selector above is used for plans.
double V3103NearestValidBuyResistance(const V310Bar &bars[],const double entry,const double confirmed_m15_resistance)
{
   double result=(confirmed_m15_resistance>entry ? confirmed_m15_resistance : 0.0);
   for(int i=2;i+2<ArraySize(bars);i++)
      if(V3103IsConfirmedM5PivotHigh(bars,i) && bars[i].high>entry &&
         (result<=0.0 || bars[i].high<result))
         result=bars[i].high;
   return result;
}

double V3103NearestValidSellSupport(const V310Bar &bars[],const double entry,const double confirmed_m15_support)
{
   double result=(confirmed_m15_support>0.0 && confirmed_m15_support<entry ? confirmed_m15_support : 0.0);
   for(int i=2;i+2<ArraySize(bars);i++)
      if(V3103IsConfirmedM5PivotLow(bars,i) && bars[i].low<entry &&
         (result<=0.0 || bars[i].low>result))
         result=bars[i].low;
   return result;
}

#endif
