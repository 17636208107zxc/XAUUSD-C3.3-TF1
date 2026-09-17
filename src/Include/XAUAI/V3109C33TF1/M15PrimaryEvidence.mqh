#ifndef XAUAI_V3109_C21_M15_PRIMARY_EVIDENCE_MQH
#define XAUAI_V3109_C21_M15_PRIMARY_EVIDENCE_MQH

#include "Strategy01Types.mqh"

#define V3109C21_PRIMARY_PIVOT_WING 20
#define V3109C21_PRIMARY_LOOKBACK 300

struct V3109C21PrimaryPivots
{
   double latest_high;
   double previous_high;
   double latest_low;
   double previous_low;
   datetime latest_high_time;
   datetime previous_high_time;
   datetime latest_low_time;
   datetime previous_low_time;
};

bool V3109C21DerivePrimaryPivots(const V310Bar &bars[],const int wing,
                                 V3109C21PrimaryPivots &out)
{
   ZeroMemory(out);
   const int total=ArraySize(bars);
   if(wing<2 || total<2*wing+3) return false;
   int high_count=0;
   int low_count=0;
   for(int i=wing;i<total-wing;i++)
   {
      bool is_high=true;
      bool is_low=true;
      for(int offset=1;offset<=wing;offset++)
      {
         if(bars[i].high<=bars[i-offset].high || bars[i].high<=bars[i+offset].high)
            is_high=false;
         if(bars[i].low>=bars[i-offset].low || bars[i].low>=bars[i+offset].low)
            is_low=false;
         if(!is_high && !is_low) break;
      }
      if(is_high && high_count<2)
      {
         if(high_count==0) { out.latest_high=bars[i].high; out.latest_high_time=bars[i].time; }
         else { out.previous_high=bars[i].high; out.previous_high_time=bars[i].time; }
         high_count++;
      }
      if(is_low && low_count<2)
      {
         if(low_count==0) { out.latest_low=bars[i].low; out.latest_low_time=bars[i].time; }
         else { out.previous_low=bars[i].low; out.previous_low_time=bars[i].time; }
         low_count++;
      }
      if(high_count>=2 && low_count>=2) break;
   }
   return high_count>=2 && low_count>=2;
}

bool V3109C21DeriveConfiguredPrimaryPivots(const V310Bar &bars[],
                                           V3109C21PrimaryPivots &out)
{
   return V3109C21DerivePrimaryPivots(bars,V3109C21_PRIMARY_PIVOT_WING,out);
}

#endif
